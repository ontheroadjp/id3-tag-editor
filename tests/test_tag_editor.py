import json
import shutil
import subprocess

import pytest
from mutagen.id3 import APIC, COMM, ID3, TIT2, TPE1
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

from app import tag_editor as editor


def version(path):
    with path.open('rb') as stream:
        return editor.file_version(stream)


def mp3(tmp_path):
    path = tmp_path / 'song.mp3'
    path.write_bytes(b'audio payload')
    tags = ID3()
    tags.add(TIT2(encoding=3, text=['Old']))
    tags.add(TPE1(encoding=3, text=['Artist 1', 'Artist 2']))
    tags.add(COMM(encoding=3, lang='eng', desc='one', text=['First']))
    tags.add(COMM(encoding=3, lang='jpn', desc='two', text=['Second']))
    tags.add(APIC(encoding=3, mime='image/png', type=3, desc='front', data=b'front'))
    tags.add(APIC(encoding=3, mime='image/png', type=4, desc='back', data=b'back'))
    tags.save(path)
    return path


def m4a(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg required')
    path = tmp_path / 'song.m4a'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
                    '-t', '0.1', '-c:a', 'aac', str(path)], check=True)
    return path


def test_mp3_edit_and_sqlite_restore_preserve_unselected_values(tmp_path):
    path = mp3(tmp_path)
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    backup, new_version = editor.save_text_updates(path, 'file', version(path),
        {'title': 'New', 'comment': 'Changed'}, store, comment_ref=('jpn', 'two'))
    tags = ID3(path)
    assert tags['TIT2'].text == ['New']
    assert tags['TPE1'].text == ['Artist 1', 'Artist 2']
    assert {(item.lang, str(item)) for item in tags.getall('COMM')} == {('eng', 'First'), ('jpn', 'Changed')}
    assert {item.type for item in tags.getall('APIC')} == {3, 4}
    restore_id, _ = editor.restore_backup(path, 'file', new_version, backup, store)
    assert ID3(path)['TIT2'].text == ['Old']
    assert {str(item) for item in ID3(path).getall('COMM')} == {'First', 'Second'}
    assert store.get('file', restore_id)['restored_from'] == backup
    assert not list(tmp_path.glob('.id3-tag-*.recovery'))


def test_m4a_edit_and_restore_multiple_values_and_freeform(tmp_path):
    path = m4a(tmp_path)
    tags = MP4(path)
    tags['\xa9nam'] = ['Old']
    tags['\xa9ART'] = ['First', 'Second']
    tags['----:com.apple.iTunes:test'] = [MP4FreeForm(b'value')]
    tags['trkn'] = [(2, 10)]
    tags.save()
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    backup, new_version = editor.save_text_updates(path, 'file', version(path),
        {'title': 'New', 'discnumber': '1/2'}, store)
    tags = MP4(path)
    assert tags['\xa9nam'] == ['New']
    assert tags['\xa9ART'] == ['First', 'Second']
    assert tags['disk'] == [(1, 2)]
    assert tags['----:com.apple.iTunes:test'] == [b'value']
    editor.restore_backup(path, 'file', new_version, backup, store)
    tags = MP4(path)
    assert tags['\xa9nam'] == ['Old']
    assert tags['trkn'] == [(2, 10)]
    assert 'disk' not in tags
    assert tags['----:com.apple.iTunes:test'] == [b'value']


def test_write_failure_restores_original_copy(tmp_path, monkeypatch):
    path = mp3(tmp_path)
    original = path.read_bytes()
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    def fail(stream, *args):
        stream.seek(0)
        stream.write(b'corruption')
        stream.flush()
        raise OSError('write failed')
    monkeypatch.setattr(editor, '_apply_mp3', fail)
    with pytest.raises(editor.EditFailure) as exc:
        editor.save_text_updates(path, 'file', version(path), {'title': 'New'}, store)
    assert exc.value.recovered
    assert path.read_bytes() == original
    assert store.list('file')[0]['result'] == 'failed_recovered'
    assert not list(tmp_path.glob('.id3-tag-*.recovery'))


def test_stale_version_does_not_modify_file(tmp_path):
    path = mp3(tmp_path)
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    before = path.read_bytes()
    with pytest.raises(editor.EditConflict):
        editor.save_text_updates(path, 'file', '0' * 64, {'title': 'New'}, store)
    assert path.read_bytes() == before
    assert store.list('file') == []


def test_multiple_comments_require_selection(tmp_path):
    path = mp3(tmp_path)
    before = path.read_bytes()
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    with pytest.raises(editor.UnsupportedTagLayout):
        editor.save_text_updates(path, 'file', version(path), {'comment': 'New'}, store)
    assert path.read_bytes() == before


def test_multiple_mp4_images_require_confirmation(tmp_path):
    path = m4a(tmp_path)
    media = MP4(path)
    media['covr'] = [MP4Cover(b'one'), MP4Cover(b'two')]
    media.save()
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    with pytest.raises(editor.UnsupportedTagLayout):
        editor.save_text_updates(path, 'file', version(path), {}, store,
                                 artwork={'cover': editor.ArtworkChange(None)})
    assert len(MP4(path)['covr']) == 2


@pytest.mark.parametrize('value', ['0', '65536', '1/0', 'abc'])
def test_invalid_number(value):
    with pytest.raises(ValueError):
        editor.validate_number(value)


@pytest.mark.parametrize('value', ['2024-02-30', '2024-13', 'abc'])
def test_invalid_date(value):
    with pytest.raises(ValueError):
        editor.validate_date(value)


def test_backup_history_is_file_scoped(tmp_path):
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    first = store.create('a', 'save', None, json.dumps(['title']), 'test', b'tag')
    assert store.get('b', first) is None
    assert not store.delete('b', first)


def test_artwork_both_sides_and_history_restore(tmp_path):
    path = mp3(tmp_path)
    # Use a complete generated PNG, including valid CRCs.
    import struct
    import zlib
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data))
    image = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
             + chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00\xff')) + chunk(b'IEND', b''))
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    backup, new_version = editor.save_text_updates(path, 'file', version(path), {}, store, artwork={
        'front': editor.ArtworkChange(image, 'image/png', 'front'),
        'back': editor.ArtworkChange(image, 'image/png', 'back'),
    })
    assert all(item.data == image for item in ID3(path).getall('APIC'))
    editor.restore_backup(path, 'file', new_version, backup, store)
    assert {item.data for item in ID3(path).getall('APIC')} == {b'front', b'back'}


def test_m4a_multiple_comments_only_selected_is_changed(tmp_path):
    path = m4a(tmp_path)
    media = MP4(path)
    media['\xa9cmt'] = ['First', 'Second']
    media.save()
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    backup, new_version = editor.save_text_updates(path, 'file', version(path), {'comment': 'Changed'},
                                                    store, comment_index=1)
    assert MP4(path)['\xa9cmt'] == ['First', 'Changed']
    editor.restore_backup(path, 'file', new_version, backup, store)
    assert MP4(path)['\xa9cmt'] == ['First', 'Second']


def test_second_history_restore_remains_undoable(tmp_path):
    path = mp3(tmp_path)
    store = editor.BackupStore(tmp_path / 'history.sqlite')
    first, ver = editor.save_text_updates(path, 'file', version(path), {'title': 'First'}, store)
    second, ver = editor.save_text_updates(path, 'file', ver, {'title': 'Second'}, store)
    restored, ver = editor.restore_backup(path, 'file', ver, first, store)
    assert ID3(path)['TIT2'].text == ['Old']
    editor.restore_backup(path, 'file', ver, restored, store)
    assert ID3(path)['TIT2'].text == ['Second']
    assert second != first
