import base64
import importlib
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import APIC, ID3, TIT2
from mutagen.mp4 import MP4, MP4Cover


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
    tags['covr'] = [MP4Cover(b'image data', imageformat=MP4Cover.FORMAT_PNG)]
    tags.save()
    before = audio.read_bytes()
    assert '日本語 曲.m4a' in [entry['name'] for entry in client.get('/api/files').json()['entries']]
    file_id = identifier('日本語 曲.m4a')
    info = client.get(f'/api/files/{file_id}').json()
    assert info['tags']['title'] == '曲名'
    assert info['tags']['artist'] == '演奏者'
    assert info['tags']['tracknumber'] == '2/10'
    assert info['artwork'] is True
    assert client.get(f'/api/files/{file_id}/artwork').content == b'image data'
    response = client.get(f'/api/files/{file_id}/audio', headers={'Range': 'bytes=0-9'})
    assert response.status_code == 206
    assert response.headers['content-type'] == 'audio/mp4'
    assert response.content == before[:10]
    assert audio.read_bytes() == before
