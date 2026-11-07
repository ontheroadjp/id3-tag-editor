import base64
import importlib
import json
import shutil
import struct
import subprocess
import urllib.error
import urllib.request
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import APIC, COMM, ID3, TIT2, TPE1, TPOS
from mutagen.mp4 import MP4, MP4Cover
from mutagen.mp4._atom import Atoms


def identifier(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "music"
    root.mkdir()
    (root / "album").mkdir()
    song = root / "album" / "song.mp3"
    song.write_bytes(b"audio data for range requests")
    tags = ID3()
    tags.add(TIT2(encoding=3, text=["Example"]))
    tags.add(APIC(encoding=3, mime="image/png", type=3, desc="cover", data=b"image data"))
    tags.save(song)
    (root / "empty.mp3").write_bytes(b"unrecognized audio")
    (tmp_path / "outside.mp3").write_bytes(b"outside")
    (root / "outside.mp3").symlink_to(tmp_path / "outside.mp3")
    monkeypatch.setenv("MUSIC_DIR", str(root))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setenv("LIBRARY_ID", "test-library")
    monkeypatch.setenv("ENABLE_TAG_WRITES", "1")
    import app.main
    importlib.reload(app.main)
    with TestClient(app.main.app) as client:
        yield client, root, song


def test_directory_and_tags(library):
    client, root, song = library
    before = song.read_bytes()
    entries = client.get("/api/files").json()["entries"]
    assert [entry["name"] for entry in entries] == ["album", "empty.mp3"]
    response = client.get(f"/api/files/{identifier('album/song.mp3')}")
    assert response.json()["tags"]["title"] == "Example"
    assert response.json()["artwork"] is True
    assert client.get(f"/api/files/{identifier('empty.mp3')}").json()["tags"]["title"] == ""
    assert client.get(f"/api/files/{identifier('album/song.mp3')}/artwork").content == b"image data"
    assert song.read_bytes() == before


def test_named_presets_persist_without_modifying_music(library, monkeypatch):
    client, root, song = library
    before = song.read_bytes()
    tags = {key: key for key in ('artist', 'album', 'albumartist', 'date', 'tracknumber', 'discnumber', 'genre')}
    headers = {'host': 'localhost:18081', 'origin': 'http://localhost:18081'}
    monkeypatch.setattr('app.main.WRITES_ENABLED', False)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))

    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(b'\x00\x00\x00\x00\xff')) + chunk(b'IEND', b''))
    image = {'data': base64.b64encode(png).decode(), 'mime': 'image/png', 'description': None}
    payload = {'name': 'Example', 'tags': tags, 'artwork': {'front': image}}
    assert client.post('/api/presets', json=payload, headers=headers).status_code == 201
    assert client.post('/api/presets', json=payload, headers=headers).status_code == 409
    assert client.get('/api/presets', headers=headers).json() == [payload]
    updated = {**payload, 'tags': {**tags, 'artist': 'new artist'}}
    assert client.put('/api/presets', json=updated, headers=headers).status_code == 200
    assert client.get('/api/presets', headers=headers).json() == [updated]
    assert client.delete('/api/presets', params={'name': 'Example'}, headers={'host': 'localhost:18081', 'origin': 'http://evil.example'}).status_code == 403
    assert client.delete('/api/presets', params={'name': 'Example'}, headers=headers).status_code == 200
    assert client.get('/api/presets', headers=headers).json() == []
    assert client.delete('/api/presets', params={'name': 'Example'}, headers=headers).status_code == 404
    assert client.put('/api/presets', json=updated, headers=headers).status_code == 404
    assert client.post('/api/presets', json={**payload, 'tags': {**tags, 'title': 'forbidden'}}, headers=headers).status_code == 422
    assert client.post('/api/presets', json={**payload, 'name': 'Invalid', 'artwork': {'front': {'data': 'bad', 'mime': 'image/png'}}}, headers=headers).status_code == 422
    assert client.post('/api/presets', json=payload, headers={'host': 'localhost:18081', 'origin': 'http://evil.example'}).status_code == 403
    assert song.read_bytes() == before


def test_reject_paths_outside_root(library):
    client, root, song = library
    assert client.get(f"/api/files/{identifier('../outside.mp3')}").status_code == 404
    assert client.get(f"/api/files/{identifier('outside.mp3')}/audio").status_code == 404
    assert client.get('/api/files', params={'directory': identifier('..')}).status_code == 404


def test_audio_range(library):
    client, root, song = library
    response = client.get(f"/api/files/{identifier('album/song.mp3')}/audio", headers={"Range": "bytes=2-7"})
    assert response.status_code == 206
    assert response.content == song.read_bytes()[2:8]
    assert response.headers['content-range'].startswith('bytes 2-7/')


def test_file_info_reports_version_and_rejects_changed_file(library, monkeypatch):
    client, root, song = library
    file_id = identifier('album/song.mp3')
    version = client.get(f'/api/files/{file_id}').json()['version']
    assert isinstance(version, str) and len(version) == 64
    import app.main
    versions = iter(['before', 'after'])
    monkeypatch.setattr(app.main.tag_editor, 'file_version', lambda stream: next(versions))
    assert client.get(f'/api/files/{file_id}').status_code == 409


def test_mp3_disc_and_comments_are_read_without_modification(library):
    client, root, song = library
    tags = ID3(song)
    tags.add(TPOS(encoding=3, text=['1/2']))
    tags.add(COMM(encoding=3, lang='eng', desc='first', text=['First']))
    tags.add(COMM(encoding=3, lang='jpn', desc='second', text=['Second']))
    tags.save(song)
    before = song.read_bytes()
    info = client.get(f"/api/files/{identifier('album/song.mp3')}").json()
    values = info['tags']
    assert values['discnumber'] == '1/2'
    assert set(values['comment'].split('; ')) == {'First', 'Second'}
    assert {(item['language'], item['description'], item['value']) for item in info['comments']} == {
        ('eng', 'first', 'First'), ('jpn', 'second', 'Second')
    }
    assert song.read_bytes() == before


def test_existing_multiple_values_are_returned_separately(library):
    client, root, song = library
    tags = ID3(song)
    tags.add(TPE1(encoding=3, text=['Artist One', 'Artist Two']))
    tags.save(song)
    info = client.get(f"/api/files/{identifier('album/song.mp3')}").json()
    assert info['tag_values']['artist'] == ['Artist One', 'Artist Two']


def test_front_and_back_artwork_are_distinguished(library):
    client, root, song = library
    tags = ID3(song)
    tags.add(APIC(encoding=3, mime='image/png', type=4, desc='back', data=b'back image'))
    tags.add(APIC(encoding=3, mime='image/png', type=4, desc='back2', data=b'other back'))
    tags.save(song)
    file_id = identifier('album/song.mp3')
    info = client.get(f'/api/files/{file_id}').json()
    assert info['artwork'] and info['back_artwork']
    assert {item['description'] for item in info['artworks']['back']} == {'back', 'back2'}
    assert client.get(f'/api/files/{file_id}/artwork').content == b'image data'
    assert {client.get(f'/api/files/{file_id}/artwork?side=back&index={i}').content for i in range(2)} == {b'back image', b'other back'}
    assert client.get(f'/api/files/{file_id}/artwork?side=back&index=2').status_code == 404
    tags.delall('APIC')
    tags.add(APIC(encoding=3, mime='image/png', type=4, desc='back', data=b'back image'))
    tags.save(song)
    assert not client.get(f'/api/files/{file_id}').json()['artwork']
    assert client.get(f'/api/files/{file_id}/artwork').status_code == 404


def test_release_info_includes_disc_number_and_back_cover(monkeypatch):
    from app import lookup
    recording = '12345678-1234-1234-1234-123456789abc'
    release = '87654321-4321-4321-4321-abcdef123456'
    monkeypatch.setattr(lookup, 'request_json', lambda url: {
        'title': 'Album', 'medium-count': 2,
        'media': [{'position': 1, 'tracks': [{'number': '4', 'recording': {'id': recording}}]}],
    })
    result = lookup.release_details(recording, release)
    assert result['discnumber'] == '1/2'
    assert result['artwork_back_url'].endswith(f'/{release}/back-250')


def test_save_restore_history_and_origin_protection(library):
    client, root, song = library
    file_id = identifier('album/song.mp3')
    url = f'/api/files/{file_id}'
    version = client.get(url).json()['version']
    body = {'expected_version': version, 'updates': {'title': 'Changed'}}
    assert client.post(f'{url}/tags', json=body).status_code == 403
    headers = {'host': 'localhost:18081', 'origin': 'http://localhost:18081'}
    response = client.post(f'{url}/tags', headers=headers, json=body)
    assert response.status_code == 200
    saved = response.json()
    assert client.get(url).json()['tags']['title'] == 'Changed'
    assert client.post(f'{url}/tags', headers=headers, json=body).status_code == 409
    history = client.get(f'{url}/history', headers=headers).json()
    assert history[0]['id'] == saved['backup_id']
    assert history[0]['fields'] == ['title']
    assert 'snapshot' not in history[0]
    restored = client.post(f'{url}/history/{saved["backup_id"]}/restore', headers=headers,
                           json={'expected_version': saved['version']})
    assert restored.status_code == 200
    assert client.get(url).json()['tags']['title'] == 'Example'
    history = client.get(f'{url}/history', headers=headers).json()
    assert history[0]['operation'] == 'restore'
    assert history[0]['restored_from'] == saved['backup_id']
    from app import tag_editor
    with tag_editor.locked_music_file(song, root):
        assert client.delete(f'{url}/history/{saved["backup_id"]}', headers=headers).status_code == 409
    assert client.delete(f'{url}/history/{saved["backup_id"]}', headers=headers).status_code == 204
    logs = (root.parent / 'app-data' / 'logs' / 'tag-editor.log').read_text()
    assert 'save_verified' in logs and 'restore_verified' in logs
    assert 'backup_created' in logs and 'write_started' in logs and 'write_verified' in logs
    assert 'Changed' not in logs and 'Example' not in logs


def test_editor_selection_and_reset_in_browser_state():
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is required to test editor state')
    script = Path('app/static/app.js').read_text()
    harness = '''
class Element {
  constructor() { this.children = []; this.events = {}; this.hidden = false; this.disabled = false; this.value = ''; this.attributes = {}; }
  append(...items) { this.children.push(...items); }
  replaceChildren() { this.children = []; }
  addEventListener(name, fn) { this.events[name] = fn; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute() {}
  pause() {} load() {}
}
const elements = new Map();
const document = {createElement: () => new Element(), querySelector: (key) => {
  if (!elements.has(key)) elements.set(key, new Element()); return elements.get(key);
}};
const window = {confirm: () => true, addEventListener() {}};
const fetch = async () => ({ok: true, json: async () => ({parent: null, name: 'music', entries: []})});
'''
    check = '''
currentFile = 'file'; fileFormat = 'mp3'; writeEnabled = true; comments = [];
existingTags = {title: 'Old', artist: '', album: '', albumartist: '', date: '', tracknumber: '', discnumber: '', genre: '', comment: ''};
existingArtworks = {}; setDraft(existingTags); renderComparison();
const row = elements.get('#comparison-rows').children[0];
const input = row.children[1].children[0]; input.value = 'New'; input.events.input();
const beforeSelect = elements.get('#save-tags').disabled;
row.children[2].children[0].events.click();
const afterSelect = elements.get('#save-tags').disabled;
const arrow = elements.get('#comparison-rows').children[0].children[2].children[0].textContent;
elements.get('#reset-tags').events.click();
const resetValue = elements.get('#comparison-rows').children[0].children[1].children[0].value;
const resetDisabled = elements.get('#reset-tags').disabled;
const saveDisabled = elements.get('#save-tags').disabled;
setDraft({title: 'Candidate'}); renderComparison();
const candidateDisabled = elements.get('#save-tags').disabled;
console.log(JSON.stringify({beforeSelect, afterSelect, arrow, resetValue, resetDisabled, saveDisabled, candidateDisabled}));
'''
    result = subprocess.run([node, '-e', harness + script + check], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == {'beforeSelect': True, 'afterSelect': False, 'arrow': '⇒',
                                         'resetValue': 'Old', 'resetDisabled': True,
                                         'saveDisabled': True, 'candidateDisabled': True}


def test_artwork_proxy_only_accepts_trusted_https_redirects(library):
    from app.main import CoverRedirectHandler
    handler = CoverRedirectHandler()
    request = urllib.request.Request('https://coverartarchive.org/release/example/front-250')
    for target in ('http://archive.org/image', 'https://archive.org.evil.example/image', 'https://localhost/image'):
        with pytest.raises(urllib.error.HTTPError):
            handler.redirect_request(request, None, 302, 'Found', {}, target)
    allowed = handler.redirect_request(request, None, 302, 'Found', {}, 'https://example.archive.org/image')
    assert allowed.full_url == 'https://example.archive.org/image'


def test_interrupted_write_can_be_recovered_from_copy_via_api(library):
    from app import main, tag_editor
    client, root, song = library
    before = song.read_bytes()
    store = main.backup_store()
    key = main.file_key(song)
    with tag_editor.locked_music_file(song, root) as stream:
        snapshot = tag_editor._snapshot(stream, '.mp3')
        version = tag_editor.file_version(stream)
        backup = store.create(key, 'save', None, '["title"]', 'mutagen-mp3-v1', snapshot,
                              'album/song.mp3', 'test-library')
        tag_editor._create_recovery_copy(song, root, stream, version, store, key, backup)
        stream.seek(0)
        stream.write(b'broken extended tag')
        stream.flush()
    file_id = identifier('album/song.mp3')
    headers = {'host': 'localhost:18081', 'origin': 'http://localhost:18081'}
    pending = client.get('/api/recovery/pending', headers=headers).json()
    assert [(item['id'], item['path']) for item in pending] == [(backup, 'album/song.mp3')]
    info = client.get(f'/api/files/{file_id}/recovery', headers=headers).json()
    response = client.post(f'/api/files/{file_id}/history/{backup}/recover', headers=headers,
                           json={'expected_version': info['version']})
    assert response.status_code == 200
    assert song.read_bytes() == before
    assert store.get(key, backup)['result'] == 'interrupted_restored'
    assert client.get('/api/recovery/pending', headers=headers).json() == []


def test_untagged_mp3_can_grow_and_restore_via_api(library):
    client, root, song = library
    path = root / 'empty.mp3'
    before = path.read_bytes()
    file_id = identifier('empty.mp3')
    headers = {'host': 'localhost:18081', 'origin': 'http://localhost:18081'}
    version = client.get(f'/api/files/{file_id}').json()['version']
    saved = client.post(f'/api/files/{file_id}/tags', headers=headers,
                        json={'expected_version': version, 'updates': {'title': 'A new title'}})
    assert saved.status_code == 200
    assert saved.json()['temporary_copy_remaining'] is False
    assert client.get(f'/api/files/{file_id}').json()['tags']['title'] == 'A new title'
    restored = client.post(f'/api/files/{file_id}/history/{saved.json()["backup_id"]}/restore', headers=headers,
                           json={'expected_version': saved.json()['version']})
    assert restored.status_code == 200
    assert path.read_bytes() == before
    assert not list(root.glob('.id3-tag-*.recovery'))


def test_untagged_fragmented_m4a_saves_tags_and_artwork_via_api(library):
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg is required to create the M4A fixture')
    client, root, song = library
    path = root / 'fragmented.m4a'
    subprocess.run(['ffmpeg', '-v', 'error', '-fflags', '+bitexact', '-f', 'lavfi', '-i',
                    'anullsrc=r=44100:cl=mono', '-t', '2', '-c:a', 'aac', '-flags:a', '+bitexact',
                    '-movflags', '+frag_keyframe+empty_moov+default_base_moof+dash+skip_trailer',
                    '-frag_duration', '1000000', '-f', 'mp4', str(path)], check=True)
    data = path.read_bytes()
    with path.open('rb') as source:
        atoms = Atoms(source)
    moov = atoms[b'moov']
    udta = atoms.path(b'moov', b'udta')[-1]
    original = bytearray(data[:udta.offset] + data[udta.offset + udta.length:])
    original[moov.offset:moov.offset + 4] = (moov.length - udta.length).to_bytes(4, 'big')
    path.write_bytes(original)
    def chunk(name, payload):
        return struct.pack('>I', len(payload)) + name + payload + struct.pack('>I', zlib.crc32(name + payload))
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00\xff')) + chunk(b'IEND', b''))
    file_id = identifier('fragmented.m4a')
    headers = {'host': 'localhost:18081', 'origin': 'http://localhost:18081'}
    version = client.get(f'/api/files/{file_id}').json()['version']
    saved = client.post(f'/api/files/{file_id}/tags', headers=headers, json={
        'expected_version': version, 'updates': {'album': 'Album', 'albumartist': 'Artist', 'artist': 'Artist'},
        'artwork': {'cover': {'data': base64.b64encode(png).decode(), 'mime': 'image/png'}},
    })
    assert saved.status_code == 200
    assert MP4(path)['\xa9alb'] == ['Album']
    assert bytes(MP4(path)['covr'][0]) == png
    restored = client.post(f'/api/files/{file_id}/history/{saved.json()["backup_id"]}/restore', headers=headers,
                           json={'expected_version': saved.json()['version']})
    assert restored.status_code == 200
    assert not MP4(path).tags


def test_editor_save_sends_only_selected_changes_and_resets_buttons():
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is required to test editor state')
    script = Path('app/static/app.js').read_text()
    harness = '''
class Element {
  constructor() { this.children = []; this.events = {}; this.hidden = false; this.disabled = false; this.value = ''; this.attributes = {}; }
  append(...items) { this.children.push(...items); } replaceChildren() { this.children = []; }
  addEventListener(name, fn) { this.events[name] = fn; } setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute() {} pause() {} load() {}
}
const elements = new Map();
const document = {createElement: () => new Element(), querySelector: (key) => {
  if (!elements.has(key)) elements.set(key, new Element()); return elements.get(key);
}};
const window = {confirm: () => true, addEventListener() {}};
const calls = [];
const fetch = async (url, options = {}) => {
  if (options.method === 'POST') { calls.push(JSON.parse(options.body)); return {ok: true, status: 200, json: async () => ({backup_id: 1})}; }
  if (url.includes('/history')) return {ok: true, json: async () => []};
  if (url.startsWith('/api/files/')) return {ok: true, json: async () => ({tags: {title: 'New', artist: 'Artist'}, comments: [], artworks: {}, artwork: false, back_artwork: false, version: 'b'.repeat(64), write_enabled: true})};
  return {ok: true, json: async () => ({parent: null, name: 'music', entries: []})};
};
'''
    check = '''
(async () => {
  currentFile = 'file'; fileFormat = 'mp3'; fileVersion = 'a'.repeat(64); writeEnabled = true; comments = [];
  existingTags = {title: 'Old', artist: 'Artist'}; existingArtworks = {}; setDraft(existingTags);
  draftTags.title = 'New'; draftTags.artist = 'Unselected'; selectedFields.add('title'); renderComparison();
  await saveTags();
  console.log(JSON.stringify({calls, saveDisabled: elements.get('#save-tags').disabled, resetDisabled: elements.get('#reset-tags').disabled, status: elements.get('#save-status').textContent}));
})();
'''
    result = subprocess.run([node, '-e', harness + script + check], capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    assert data['calls'] == [{'expected_version': 'a' * 64, 'updates': {'title': 'New'}}]
    assert data['saveDisabled'] and data['resetDisabled']
    assert data['status'] == '保存しました。'


def test_japanese_names(library):
    client, root, song = library
    folder = root / "日本語 フォルダ"
    folder.mkdir()
    (folder / "曲.mp3").write_bytes(b"audio")
    response = client.get('/api/files', params={'directory': identifier('日本語 フォルダ')})
    assert response.status_code == 200
    assert response.json()['entries'][0]['name'] == '曲.mp3'
    assert client.get(f"/api/files/{identifier('日本語 フォルダ/曲.mp3')}").status_code == 200


def test_m4a_tags_artwork_and_range(library):
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg is required to create the M4A fixture')
    client, root, song = library
    audio = root / '日本語 曲.m4a'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono', '-t', '0.1', '-c:a', 'aac', str(audio)], check=True)
    tags = MP4(audio)
    tags['\u00a9nam'] = ['曲名']
    tags['\u00a9ART'] = ['演奏者']
    tags['trkn'] = [(2, 10)]
    tags['disk'] = [(1, 2)]
    tags['\u00a9cmt'] = ['comment']
    tags['covr'] = [MP4Cover(b'image data', imageformat=MP4Cover.FORMAT_PNG)]
    tags.save()
    before = audio.read_bytes()
    assert '日本語 曲.m4a' in [entry['name'] for entry in client.get('/api/files').json()['entries']]
    file_id = identifier('日本語 曲.m4a')
    info = client.get(f'/api/files/{file_id}').json()
    assert info['tags']['title'] == '曲名'
    assert info['tags']['artist'] == '演奏者'
    assert info['tags']['tracknumber'] == '2/10'
    assert info['tags']['discnumber'] == '1/2'
    assert info['tags']['comment'] == 'comment'
    assert info['comments'] == [{'value': 'comment'}]
    assert info['artwork'] is True
    assert client.get(f'/api/files/{file_id}/artwork').content == b'image data'
    response = client.get(f'/api/files/{file_id}/audio', headers={'Range': 'bytes=0-9'})
    assert response.status_code == 206
    assert response.headers['content-type'] == 'audio/mp4'
    assert response.content == before[:10]
    assert audio.read_bytes() == before
