import base64
import importlib
import io
import json
import shutil
import subprocess
import threading
import time
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import lookup

RECORDING = '12345678-1234-1234-1234-123456789abc'
RELEASE = '87654321-4321-4321-4321-abcdef123456'


def test_fingerprint_uses_fpcalc_without_changing_file(tmp_path, monkeypatch):
    song = tmp_path / '曲.m4a'
    song.write_bytes(b'music')
    def run(args, **kwargs):
        assert args == ['fpcalc', '-json', str(song)]
        return subprocess.CompletedProcess(args, 0, '{"duration": 123.2, "fingerprint": "test"}', '')
    monkeypatch.setattr(lookup.subprocess, 'run', run)
    assert lookup.fingerprint(song) == (123, 'test')
    assert song.read_bytes() == b'music'


def test_candidates_page_releases_and_deduplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(lookup, 'fingerprint', lambda path: (123, 'fingerprint'))
    calls = []
    def request(url, *, data=None, service='musicbrainz'):
        calls.append(url)
        if service == 'acoustid':
            assert data['fingerprint'] == 'fingerprint'
            return {'status': 'ok', 'results': [{'score': 0.9, 'recordings': [{'id': RECORDING}, {'id': RECORDING}]}]}
        if '/recording/' in url:
            return {'title': 'Song', 'artist-credit': [{'name': 'Artist'}]}
        if 'offset=0' in url:
            return {'release-count': 2, 'releases': [{'id': RELEASE, 'title': 'First'}]}
        return {'release-count': 2, 'releases': [{'id': RELEASE, 'title': 'Second'}]}
    monkeypatch.setattr(lookup, 'request_json', request)
    result = lookup.find_recordings(tmp_path / 'song.mp3', 'key')
    assert len(result) == 1
    assert result[0]['artist'] == 'Artist'
    assert [r['title'] for r in result[0]['releases']] == ['First', 'Second']
    assert len(calls) == 4


def test_release_details_preserves_track_credit(monkeypatch):
    monkeypatch.setattr(lookup, 'request_json', lambda url: {'title': 'Album', 'artist-credit': [{'name': 'Various'}], 'date': '2001', 'media': [{'track-count': 17, 'tracks': [{'number': '2', 'title': 'Track', 'artist-credit': [{'name': 'Singer'}], 'recording': {'id': RECORDING}}]}]})
    result = lookup.release_details(RECORDING, RELEASE)
    assert result['title'] == 'Track'
    assert result['artist'] == 'Singer'
    assert result['albumartist'] == 'Various'
    assert result['tracknumber'] == '2/17'
    assert RELEASE in result['artwork_url']


def test_missing_key_and_bad_external_response(tmp_path, monkeypatch):
    monkeypatch.delenv('ACOUSTID_API_KEY', raising=False)
    with pytest.raises(lookup.LookupError, match='ACOUSTID_API_KEY'):
        lookup.start_lookup(tmp_path / 'song.mp3')
    def fail(*args, **kwargs):
        raise urllib.error.URLError('offline')
    monkeypatch.setattr(lookup.urllib.request, 'urlopen', fail)
    monkeypatch.setattr(lookup.time, 'sleep', lambda seconds: None)
    with pytest.raises(lookup.LookupError, match='MusicBrainz に接続できません'):
        lookup.request_json('https://example.invalid')


def test_transient_http_errors_are_retried(monkeypatch):
    attempts = []
    def respond(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise urllib.error.HTTPError('https://musicbrainz.org/', 503, 'unavailable', {}, None)
        return io.BytesIO(b'{"result": "ok"}')
    monkeypatch.setattr(lookup.urllib.request, 'urlopen', respond)
    monkeypatch.setattr(lookup.time, 'sleep', lambda seconds: None)
    assert lookup.request_json('https://musicbrainz.org/') == {'result': 'ok'}
    assert len(attempts) == 3


def test_partial_results_survive_later_failure(tmp_path, monkeypatch):
    second = '12345678-1234-1234-1234-123456789abd'
    monkeypatch.setattr(lookup, 'fingerprint', lambda path: (123, 'fingerprint'))
    def request(url, *, data=None, service='musicbrainz'):
        if service == 'acoustid':
            return {'status': 'ok', 'results': [{'recordings': [{'id': RECORDING}, {'id': second}]}]}
        if second in url:
            raise lookup.LookupError('MusicBrainz が HTTP 503 を返しました。')
        if '/recording/' in url:
            return {'title': 'Song', 'artist-credit': []}
        return {'release-count': 1, 'releases': [{'id': RELEASE, 'title': 'Album'}]}
    monkeypatch.setattr(lookup, 'request_json', request)
    warnings = []
    results = lookup.find_recordings(tmp_path / 'song.mp3', 'key', warnings)
    assert len(results) == 1
    assert warnings and 'HTTP 503' in warnings[0]


def test_musicbrainz_request_spacing(monkeypatch):
    clock = [100.0]
    waits = []
    def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds
    class Response(io.BytesIO):
        pass
    monkeypatch.setattr(lookup.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(lookup.time, 'sleep', sleep)
    monkeypatch.setattr(lookup.urllib.request, 'urlopen', lambda *args, **kwargs: Response(b'{}'))
    monkeypatch.setattr(lookup, '_last_request', {})
    lookup.request_json('https://musicbrainz.org/test')
    lookup.request_json('https://musicbrainz.org/test')
    assert waits == pytest.approx([1.1])


def test_job_api_is_async_and_rejects_missing_key(tmp_path, monkeypatch):
    root = tmp_path / 'music'
    root.mkdir()
    song = root / 'song.mp3'
    song.write_bytes(b'audio')
    monkeypatch.setenv('MUSIC_DIR', str(root))
    monkeypatch.delenv('ACOUSTID_API_KEY', raising=False)
    import app.main
    importlib.reload(app.main)
    identifier = base64.urlsafe_b64encode(b'song.mp3').decode().rstrip('=')
    release = threading.Event()
    def find(path, key, warnings, cancel):
        assert path == song
        assert release.wait(timeout=5)
        return []
    monkeypatch.setattr(lookup, 'find_recordings', find)
    with TestClient(app.main.app) as client:
        assert client.post(f'/api/files/{identifier}/lookup').status_code == 503
        monkeypatch.setenv('ACOUSTID_API_KEY', 'test-key')
        response = client.post(f'/api/files/{identifier}/lookup')
        assert response.status_code == 202
        job_id = response.json()['job_id']
        assert client.get(f'/api/lookups/{job_id}').json()['status'] == 'running'
        release.set()
        for _ in range(100):
            job = client.get(f'/api/lookups/{job_id}').json()
            if job['status'] != 'running':
                break
            time.sleep(0.01)
        assert job == {'status': 'complete', 'recordings': [], 'warnings': []}
    assert song.read_bytes() == b'audio'


def test_cancelling_lookup_stops_after_current_request(tmp_path, monkeypatch):
    cancel = threading.Event()
    monkeypatch.setattr(lookup, 'fingerprint', lambda path: (123, 'fingerprint'))
    calls = []
    def request(url, *, data=None, service='musicbrainz'):
        calls.append(url)
        if service == 'acoustid':
            return {'status': 'ok', 'results': [{'recordings': [{'id': RECORDING}]}]}
        cancel.set()
        return {'title': 'Song', 'artist-credit': []}
    monkeypatch.setattr(lookup, 'request_json', request)
    with pytest.raises(lookup.LookupCancelled):
        lookup.find_recordings(tmp_path / 'song.mp3', 'key', [], cancel)
    assert len(calls) == 2


def test_new_lookup_cancels_previous_job(tmp_path, monkeypatch):
    monkeypatch.setenv('ACOUSTID_API_KEY', 'key')
    started = threading.Event()
    stopped = threading.Event()
    def find(path, key, warnings, cancel):
        if path.name == 'first.mp3':
            started.set()
            assert cancel.wait(timeout=3)
            stopped.set()
            raise lookup.LookupCancelled()
        return []
    monkeypatch.setattr(lookup, 'find_recordings', find)
    first = lookup.start_lookup(tmp_path / 'first.mp3')
    assert started.wait(timeout=3)
    second = lookup.start_lookup(tmp_path / 'second.mp3')
    assert stopped.wait(timeout=3)
    for _ in range(100):
        if lookup.get_job(second)['status'] != 'running':
            break
        time.sleep(0.01)
    assert lookup.get_job(first)['status'] == 'cancelled'
    assert lookup.get_job(second)['status'] == 'complete'


def test_candidate_grouping_combines_country_variants():
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is required to test candidate grouping')
    script = Path('app/static/app.js').read_text()
    start = script.index('function normalizeAlbum(')
    end = script.index('\nasync function showCandidates(', start)
    example = [
        {'artist': 'Artist', 'releases': [
            {'id': 'uk', 'title': 'Album', 'country': 'GB'},
            {'id': 'us', 'title': 'Ａｌｂｕｍ', 'country': 'US'},
            {'id': 'live', 'title': 'Album Live', 'country': 'GB'},
        ]},
        {'artist': 'Artist', 'releases': [{'id': 'jp', 'title': 'album', 'country': 'JP'}]},
    ]
    command = script[start:end] + '\nconsole.log(JSON.stringify(groupCandidates(' + json.dumps(example) + ')))'
    result = subprocess.run([node, '-e', command], capture_output=True, text=True, check=True)
    groups = json.loads(result.stdout)
    assert [group['title'] for group in groups] == ['Album', 'Album Live']
    assert [item['release']['id'] for item in groups[0]['variants']] == ['uk', 'us', 'jp']
    command = script[start:end] + '\nconsole.log(JSON.stringify(groupCandidates(' + json.dumps(example) + ', "Album Live")))'
    result = subprocess.run([node, '-e', command], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)[0]['title'] == 'Album Live'
    example[0]['releases'][2]['language'] = 'eng'
    command = script[start:end] + '\nconsole.log(JSON.stringify(groupCandidates(' + json.dumps(example) + ', "Album")))'
    result = subprocess.run([node, '-e', command], capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)[0]['title'] == 'Album Live'
    example[0]['releases'][0]['language'] = 'jpn'
    command = script[start:end] + '\nconsole.log(JSON.stringify([groupCandidates(' + json.dumps(example) + ', "", "eng"), groupCandidates(' + json.dumps(example) + ', "", "jpn"), groupCandidates(' + json.dumps(example) + ', "", "unknown")]))'
    result = subprocess.run([node, '-e', command], capture_output=True, text=True, check=True)
    english, japanese, unknown = json.loads(result.stdout)
    assert [group['title'] for group in english] == ['Album Live']
    assert [variant['release']['id'] for variant in japanese[0]['variants']] == ['uk']
    assert [variant['release']['id'] for variant in unknown[0]['variants']] == ['us', 'jp']


def test_candidate_list_is_sorted_and_filters_as_user_types():
    node = shutil.which('node')
    if node is None:
        pytest.skip('node is required to test candidate list')
    script = Path('app/static/app.js').read_text()
    start = script.index('function normalizeAlbum(')
    end = script.index('\nasync function startLookup(', start)
    harness = '''
class Element {
  constructor() { this.children = []; this.hidden = false; this.events = {}; this.value = ''; this.attributes = {}; }
  append(...elements) { this.children.push(...elements); }
  replaceChildren() { this.children = []; }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, callback) { this.events[name] = callback; }
}
const container = new Element();
const status = new Element();
const document = {createElement: () => new Element(), querySelector: (key) => key === '#candidates' ? container : status};
const existingTags = {};
let lookupGeneration = 0;
let selections = [];
function renderComparison() {}
async function chooseRelease(recording, release) { selections.push(release.id); }
'''
    example = [{'artist': 'Artist', 'releases': [{'id': '1', 'title': 'Zulu'}, {'id': '2', 'title': 'Alpha'}, {'id': '3', 'title': 'Beta'}]}]
    command = harness + script[start:end] + '\n' + '''
(async () => {
  await showCandidates(RECORDINGS, 0);
  const steps = container.children[0];
  const albums = steps.children[0];
  const editions = steps.children[1];
  const search = albums.children[1].children[0];
  const list = albums.children[2];
  const titles = list.children.map(row => row.children[0].children[0].textContent);
  search.value = 'BET';
  search.events.input();
  const visible = list.children.filter(row => !row.hidden).map(row => row.children[0].children[0].textContent);
  const before = selections.length;
  list.children[1].children[0].events.click();
  const afterAlbum = selections.length;
  const editionList = editions.children[2];
  editionList.children[0].events.click();
  console.log(JSON.stringify({titles, visible, before, afterAlbum, selections}));
})();
'''.replace('RECORDINGS', json.dumps(example))
    result = subprocess.run([node, '-e', command], capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    assert [title.split(' · ')[0] for title in data['titles']] == ['Alpha', 'Beta', 'Zulu']
    assert [title.split(' · ')[0] for title in data['visible']] == ['Beta']
    assert data['before'] == data['afterAlbum'] == 0
    assert data['selections'] == ['3']
