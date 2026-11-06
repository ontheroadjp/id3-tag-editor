"""Edit tags using Mutagen, with SQLite history and recoverable writes."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import io
import json
import logging
import os
import re
import sqlite3
import stat
import struct
import uuid
import zlib
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import date
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from mutagen.id3 import APIC, COMM, ID3, ID3NoHeaderError, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TPOS, TRCK
from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm


MP3_TEXT_FRAMES = {
    "title": ("TIT2", TIT2), "artist": ("TPE1", TPE1),
    "album": ("TALB", TALB), "albumartist": ("TPE2", TPE2),
    "tracknumber": ("TRCK", TRCK), "discnumber": ("TPOS", TPOS),
    "genre": ("TCON", TCON),
}
MP4_TEXT_ITEMS = {
    "title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb",
    "albumartist": "aART", "date": "\xa9day", "genre": "\xa9gen",
    "tracknumber": "trkn", "discnumber": "disk", "comment": "\xa9cmt",
}


class AuditLogger:
    """Bounded local event log; callers provide metadata, never tag values."""

    def __init__(self, directory: Path, *, max_bytes: int = 1024 * 1024, backups: int = 5) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._logger = logging.Logger("tag-editor-audit", level=logging.INFO)
        handler = RotatingFileHandler(directory / "tag-editor.log", maxBytes=max_bytes,
                                      backupCount=backups, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        self._logger.addHandler(handler)

    def event(self, action: str, file_id: str, operation_id: str, *,
              backup_id: int | None = None, error_type: str | None = None,
              recovered: bool | None = None, fields: list[str] | None = None) -> None:
        entry: dict[str, object] = {"action": action, "file": file_id, "operation_id": operation_id}
        if backup_id is not None:
            entry["backup_id"] = backup_id
        if error_type is not None:
            entry["error_type"] = error_type
        if recovered is not None:
            entry["recovered"] = recovered
        if fields is not None:
            entry["fields"] = fields
        self._logger.info(json.dumps(entry, ensure_ascii=True, separators=(",", ":")))

    def close(self) -> None:
        for handler in self._logger.handlers[:]:
            handler.close()
            self._logger.removeHandler(handler)


@dataclass(frozen=True)
class ArtworkChange:
    """New image bytes, or None for explicit deletion of a selected image."""

    data: bytes | None
    mime: str | None = None
    description: str | None = None


def _validate_artwork(change: ArtworkChange) -> None:
    if change.data is None:
        return
    if len(change.data) > 10 * 1024 * 1024:
        raise ValueError("画像は10 MiB以下にしてください。")
    if change.mime == "image/png":
        valid = _valid_png(change.data)
    elif change.mime == "image/jpeg":
        valid = _valid_jpeg(change.data)
    else:
        valid = False
    if not valid:
        raise ValueError("JPEGまたはPNG画像を指定してください。")


def _valid_png(data: bytes) -> bool:
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return False
    position = 8
    header_seen = False
    compressed = bytearray()
    rows: list[int] = []
    palette_required = False
    palette_seen = False
    while position + 12 <= len(data):
        length = int.from_bytes(data[position:position + 4], "big")
        end = position + 12 + length
        if end > len(data):
            return False
        name = data[position + 4:position + 8]
        payload = data[position + 8:position + 8 + length]
        checksum = int.from_bytes(data[position + 8 + length:end], "big")
        if zlib.crc32(name + payload) != checksum:
            return False
        if not header_seen:
            if name != b"IHDR" or length != 13:
                return False
            width, height = struct.unpack(">II", payload[:8])
            if not width or not height or max(width, height) > 20_000 or width * height > 40_000_000:
                return False
            depth, color, compression, filtering, interlace = payload[8:13]
            formats = {0: (1, {1, 2, 4, 8, 16}), 2: (3, {8, 16}), 3: (1, {1, 2, 4, 8}),
                       4: (2, {8, 16}), 6: (4, {8, 16})}
            if color not in formats or depth not in formats[color][1] or compression or filtering or interlace not in (0, 1):
                return False
            channels = formats[color][0]
            palette_required = color == 3
            passes = [(0, 0, 1, 1)] if not interlace else [
                (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
                (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2),
            ]
            for x, y, dx, dy in passes:
                pass_width = max(0, (width - x + dx - 1) // dx)
                pass_height = max(0, (height - y + dy - 1) // dy)
                if pass_width and pass_height:
                    row_size = 1 + (pass_width * channels * depth + 7) // 8
                    rows.extend([row_size] * pass_height)
            if sum(rows) > 128 * 1024 * 1024:
                return False
            header_seen = True
        elif name == b"IHDR":
            return False
        if name == b"IDAT":
            compressed.extend(payload)
        if name == b"PLTE":
            palette_seen = 0 < length <= 768 and length % 3 == 0
        if name == b"IEND":
            if length != 0 or end != len(data) or not compressed or (palette_required and not palette_seen):
                return False
            try:
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(compressed, 128 * 1024 * 1024 + 1)
                if len(decoded) != sum(rows) or not decoder.eof or decoder.unused_data:
                    return False
                offset = 0
                for row in rows:
                    if decoded[offset] > 4:
                        return False
                    offset += row
                return True
            except zlib.error:
                return False
        position = end
    return False


def _valid_jpeg(data: bytes) -> bool:
    if not data.startswith(b"\xff\xd8"):
        return False
    position = 2
    frame_seen = False
    scan_seen = False
    frame_markers = {0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf}
    while position < len(data):
        if data[position] != 0xff:
            if not scan_seen:
                return False
            position += 1
            continue
        while position < len(data) and data[position] == 0xff:
            position += 1
        if position >= len(data):
            return False
        marker = data[position]
        position += 1
        if marker == 0x00 or 0xd0 <= marker <= 0xd7:
            if not scan_seen:
                return False
            continue
        if marker == 0xd9:
            return frame_seen and scan_seen and position == len(data)
        if marker in {0xd8, 0x01} or position + 2 > len(data):
            return False
        length = int.from_bytes(data[position:position + 2], "big")
        if length < 2 or position + length > len(data):
            return False
        if marker in frame_markers:
            if length < 8:
                return False
            height, width = struct.unpack(">HH", data[position + 3:position + 7])
            if not width or not height or max(width, height) > 20_000 or width * height > 40_000_000:
                return False
            frame_seen = True
        if marker == 0xda:
            if not frame_seen:
                return False
            scan_seen = True
        position += length
    return False


class UnsupportedTagLayout(ValueError):
    """The operation cannot safely be applied."""


def validate_number(value: str) -> tuple[int, int] | None:
    """Validate a common MP3/M4A track or disc number representation."""
    if value == "":
        return None
    if not re.fullmatch(r"[0-9]+(?:/[0-9]+)?", value):
        raise ValueError("番号は数値または数値/総数で入力してください。")
    parts = [int(part) for part in value.split("/")]
    if any(part < 1 or part > 65535 for part in parts):
        raise ValueError("番号は1から65535の範囲で入力してください。")
    return parts[0], parts[1] if len(parts) == 2 else 0


def validate_date(value: str) -> str:
    """Accept a year, year-month or calendar date without changing precision."""
    if value == "":
        return value
    if not re.fullmatch(r"[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?", value):
        raise ValueError("日付は YYYY、YYYY-MM または YYYY-MM-DD で入力してください。")
    parts = [int(part) for part in value.split("-")]
    try:
        date(parts[0], parts[1] if len(parts) > 1 else 1, parts[2] if len(parts) > 2 else 1)
    except ValueError as error:
        raise ValueError("実在する日付を入力してください。") from error
    return value


def file_version(stream: BinaryIO) -> str:
    """Hash an opened file and reject changes observed during the read."""
    before = os.fstat(stream.fileno())
    stream.seek(0)
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    after = os.fstat(stream.fileno())
    identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
    identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
    if identity_before != identity_after:
        raise UnsupportedTagLayout("File changed while calculating its version")
    digest.update(repr(identity_after).encode("ascii"))
    return digest.hexdigest()


def _open_music_parent(path: Path, root: Path | None) -> tuple[int, str]:
    if root is None:
        return os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW), path.name
    try:
        parts = path.relative_to(root).parts
    except ValueError as error:
        raise UnsupportedTagLayout("File is outside the music root") from error
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise UnsupportedTagLayout("Invalid music path")
    parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = child
        return parent, parts[-1]
    except BaseException:
        os.close(parent)
        raise


def _open_music_file(path: Path, root: Path | None) -> int:
    parent, name = _open_music_parent(path, root)
    try:
        return os.open(name, os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)


@contextmanager
def locked_music_file(path: Path, root: Path | None = None) -> Iterator[BinaryIO]:
    """Open a regular file and acquire a nonblocking advisory exclusive lock."""
    descriptor = _open_music_file(path, root)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise UnsupportedTagLayout("Music file is not a regular file")
        if os.fstat(descriptor).st_nlink != 1:
            raise UnsupportedTagLayout("Files with multiple hard links are not supported")
        with os.fdopen(descriptor, "r+b") as stream:
            descriptor = -1
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield stream
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    finally:
        if descriptor != -1:
            os.close(descriptor)


class BackupStore:
    """Store opaque format-specific snapshots and their human-readable history."""

    def __init__(self, database: Path) -> None:
        self.database = database
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS backups (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    operation TEXT NOT NULL CHECK (operation IN ('save', 'restore')),
                    restored_from INTEGER,
                    fields TEXT NOT NULL,
                    snapshot_format TEXT NOT NULL,
                    snapshot BLOB NOT NULL,
                    result TEXT NOT NULL DEFAULT 'pending',
                    file_path TEXT,
                    library_id TEXT,
                    recovery_name TEXT,
                    recovery_sha256 TEXT,
                    recovery_state TEXT,
                    recovery_device INTEGER,
                    recovery_inode INTEGER,
                    recovery_copy_device INTEGER,
                    recovery_copy_inode INTEGER,
                    CHECK ((operation = 'save' AND restored_from IS NULL) OR
                           (operation = 'restore' AND restored_from IS NOT NULL))
                )
            """)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(backups)")}
            if "result" not in columns:
                connection.execute("ALTER TABLE backups ADD COLUMN result TEXT NOT NULL DEFAULT 'pending'")
            for column, kind in (("file_path", "TEXT"), ("library_id", "TEXT"), ("recovery_name", "TEXT"), ("recovery_sha256", "TEXT"),
                                 ("recovery_state", "TEXT"), ("recovery_device", "INTEGER"), ("recovery_inode", "INTEGER"),
                                 ("recovery_copy_device", "INTEGER"), ("recovery_copy_inode", "INTEGER")):
                if column not in columns:
                    connection.execute(f"ALTER TABLE backups ADD COLUMN {column} {kind}")
            connection.execute("CREATE INDEX IF NOT EXISTS backups_file_id ON backups(file_id, id DESC)")

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def create(self, file_id: str, operation: str, restored_from: int | None,
               fields: str, snapshot_format: str, snapshot: bytes,
               file_path: str | None = None, library_id: str | None = None) -> int:
        with closing(self._connect()) as connection, connection:
            if operation == "restore":
                connection.execute("BEGIN IMMEDIATE")
                target = connection.execute(
                    "SELECT 1 FROM backups WHERE file_id = ? AND id = ?", (file_id, restored_from),
                ).fetchone()
                if target is None:
                    raise ValueError("Restore source does not exist for this file")
            cursor = connection.execute(
                "INSERT INTO backups (file_id, operation, restored_from, fields, snapshot_format, snapshot, file_path, library_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (file_id, operation, restored_from, fields, snapshot_format, snapshot, file_path, library_id),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("backup ID was not generated")
            return cursor.lastrowid

    def list(self, file_id: str) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, created_at, operation, restored_from, fields, snapshot_format, result, recovery_state, recovery_name "
                "FROM backups WHERE file_id = ? ORDER BY id DESC", (file_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def pending(self, library_id: str) -> list[dict[str, object]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT id, file_id, file_path, result, recovery_state, recovery_name FROM backups "
                "WHERE library_id = ? AND (result IN ('pending', 'failed_unrecovered') OR recovery_name IS NOT NULL) "
                "ORDER BY id DESC", (library_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get(self, file_id: str, backup_id: int) -> dict[str, object] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM backups WHERE file_id = ? AND id = ?", (file_id, backup_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def delete(self, file_id: str, backup_id: int) -> bool:
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("DELETE FROM backups WHERE file_id = ? AND id = ? AND recovery_name IS NULL", (file_id, backup_id))
            return cursor.rowcount == 1

    def set_recovery(self, file_id: str, backup_id: int, name: str | None,
                     digest: str | None, state: str | None,
                     device: int | None = None, inode: int | None = None,
                     copy_device: int | None = None, copy_inode: int | None = None) -> None:
        if state not in {None, "copying", "ready"}:
            raise ValueError("Invalid recovery state")
        if name is not None and not re.fullmatch(r"\.id3-tag-[0-9a-f]{32}\.recovery", name):
            raise ValueError("Invalid recovery copy name")
        if digest is not None and not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid recovery digest")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE backups SET recovery_name = ?, recovery_sha256 = ?, recovery_state = ?, recovery_device = ?, recovery_inode = ?, recovery_copy_device = ?, recovery_copy_inode = ? WHERE file_id = ? AND id = ?",
                (name, digest, state, device, inode, copy_device, copy_inode, file_id, backup_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Backup does not exist for this file")

    def finish_recovery(self, file_id: str, backup_id: int, result: str) -> None:
        if result not in {"failed_recovered", "interrupted_restored"}:
            raise ValueError("Invalid recovery outcome")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute(
                "UPDATE backups SET result = ?, recovery_name = NULL, recovery_sha256 = NULL, recovery_state = NULL, "
                "recovery_device = NULL, recovery_inode = NULL, recovery_copy_device = NULL, recovery_copy_inode = NULL "
                "WHERE file_id = ? AND id = ? AND recovery_name IS NOT NULL", (result, file_id, backup_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Recovery record does not exist")

    def set_result(self, file_id: str, backup_id: int, result: str) -> None:
        allowed = {"pending", "completed", "not_applied", "failed_recovered", "failed_unrecovered", "interrupted_restored"}
        if result not in allowed:
            raise ValueError("Invalid backup result")
        with closing(self._connect()) as connection, connection:
            cursor = connection.execute("UPDATE backups SET result = ? WHERE file_id = ? AND id = ?",
                                        (result, file_id, backup_id))
            if cursor.rowcount != 1:
                raise ValueError("Backup does not exist for this file")


class EditConflict(ValueError):
    """The music file no longer matches the user's view."""


class EditFailure(RuntimeError):
    def __init__(self, message: str, *, recovered: bool, backup_id: int) -> None:
        super().__init__(message)
        self.recovered = recovered
        self.backup_id = backup_id


def _verify_path_identity(path: Path, stream: BinaryIO) -> None:
    opened = os.fstat(stream.fileno())
    current = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
        raise EditConflict("The selected file has changed")


def _stream_digest(stream: BinaryIO) -> str:
    stream.seek(0)
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _create_recovery_copy(path: Path, root: Path | None, stream: BinaryIO, expected_version: str,
                          store: BackupStore, file_id: str, backup_id: int,
                          extra_bytes: int = 0) -> tuple[str, str]:
    """Durably copy and verify the original alongside it before any write."""
    parent, _ = _open_music_parent(path, root)
    source_stat = os.fstat(stream.fileno())
    name = f".id3-tag-{uuid.uuid4().hex}.recovery"
    created = False
    try:
        space = os.fstatvfs(parent)
        if space.f_bavail * space.f_frsize < source_stat.st_size + max(extra_bytes, 0) + 1024 * 1024:
            raise OSError("復旧用コピーの空き容量が足りません。")
        store.set_recovery(file_id, backup_id, name, None, "copying", source_stat.st_dev, source_stat.st_ino)
        descriptor = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        created = True
        with os.fdopen(descriptor, "w+b") as copy:
            stream.seek(0)
            digest = hashlib.sha256()
            total = 0
            while chunk := stream.read(1024 * 1024):
                if copy.write(chunk) != len(chunk):
                    raise OSError("復旧用コピーを完全に書き込めませんでした。")
                digest.update(chunk)
                total += len(chunk)
            if total != source_stat.st_size or file_version(stream) != expected_version:
                raise EditConflict("コピー中にファイルが変更されました。")
            copy.flush()
            copied_stat = os.fstat(copy.fileno())
            if (copied_stat.st_uid, copied_stat.st_gid) != (source_stat.st_uid, source_stat.st_gid):
                os.fchown(copy.fileno(), source_stat.st_uid, source_stat.st_gid)
            os.fchmod(copy.fileno(), stat.S_IMODE(source_stat.st_mode))
            attributes = {key: os.getxattr(stream.fileno(), key) for key in os.listxattr(stream.fileno())}
            for key in os.listxattr(copy.fileno()):
                if key not in attributes:
                    os.removexattr(copy.fileno(), key)
            for key, value in attributes.items():
                os.setxattr(copy.fileno(), key, value)
            if {key: os.getxattr(copy.fileno(), key) for key in os.listxattr(copy.fileno())} != attributes:
                raise OSError("復旧用コピーの拡張属性を維持できません。")
            os.utime(copy.fileno(), ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
            os.fsync(copy.fileno())
            if _stream_digest(copy) != digest.hexdigest():
                raise OSError("復旧用コピーの検証に失敗しました。")
            final_stat = os.fstat(copy.fileno())
            if (final_stat.st_uid, final_stat.st_gid, stat.S_IMODE(final_stat.st_mode)) != (
                source_stat.st_uid, source_stat.st_gid, stat.S_IMODE(source_stat.st_mode)
            ):
                raise OSError("復旧用コピーの所有者または権限を維持できません。")
        os.fsync(parent)
        store.set_recovery(file_id, backup_id, name, digest.hexdigest(), "ready", source_stat.st_dev,
                           source_stat.st_ino, final_stat.st_dev, final_stat.st_ino)
        return name, digest.hexdigest()
    except BaseException:
        removed = not created
        if created:
            try:
                os.unlink(name, dir_fd=parent)
                os.fsync(parent)
                removed = True
            except OSError:
                logging.getLogger(__name__).exception("Could not remove an incomplete recovery copy")
        if removed:
            store.set_recovery(file_id, backup_id, None, None, None)
        raise
    finally:
        os.close(parent)


def _open_recovery_copy(parent: int, record: dict[str, object]) -> tuple[int, str]:
    name, digest = record.get("recovery_name"), record.get("recovery_sha256")
    if (record.get("recovery_state") != "ready" or not isinstance(name, str)
            or not re.fullmatch(r"\.id3-tag-[0-9a-f]{32}\.recovery", name)
            or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise UnsupportedTagLayout("復旧用コピーの記録が不正です。")
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise UnsupportedTagLayout("復旧用コピーの構造が不正です。")
        if (info.st_dev, info.st_ino) != (record.get("recovery_copy_device"), record.get("recovery_copy_inode")):
            raise EditConflict("復旧用コピーが別のファイルに置き換わっています。")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with os.fdopen(os.dup(descriptor), "rb") as source:
            if _stream_digest(source) != digest:
                raise UnsupportedTagLayout("復旧用コピーが変更されています。")
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise EditConflict("復旧用コピーが変更されました。")
        return descriptor, name
    except BaseException:
        os.close(descriptor)
        raise


def _remove_recovery_copy(path: Path, root: Path | None, stream: BinaryIO,
                          store: BackupStore, file_id: str, backup_id: int) -> None:
    record = store.get(file_id, backup_id)
    if record is None:
        raise ValueError("Backup does not exist for this file")
    parent, _ = _open_music_parent(path, root)
    try:
        _verify_path_identity(path, stream)
        try:
            descriptor, name = _open_recovery_copy(parent, record)
        except FileNotFoundError:
            current = os.fstat(stream.fileno())
            if (record["result"] not in {"completed", "not_applied"} or
                    (current.st_dev, current.st_ino) != (record["recovery_device"], record["recovery_inode"])):
                raise
            store.set_recovery(file_id, backup_id, None, None, None)
            return
        try:
            os.unlink(name, dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(descriptor)
        store.set_recovery(file_id, backup_id, None, None, None)
    finally:
        os.close(parent)


def _restore_recovery_copy(path: Path, root: Path | None, stream: BinaryIO,
                           store: BackupStore, file_id: str, backup_id: int,
                           expected_version: str | None = None,
                           outcome: str | None = None) -> str:
    """Replace a partially modified original with its verified recovery copy."""
    record = store.get(file_id, backup_id)
    if record is None:
        raise ValueError("Backup does not exist for this file")
    source_stat = os.fstat(stream.fileno())
    if (source_stat.st_dev, source_stat.st_ino) != (record["recovery_device"], record["recovery_inode"]):
        raise EditConflict("元ファイルが別のファイルに置き換わっています。")
    parent, original_name = _open_music_parent(path, root)
    try:
        _verify_path_identity(path, stream)
        descriptor, copy_name = _open_recovery_copy(parent, record)
        try:
            copy_stat = os.fstat(descriptor)
            if (copy_stat.st_uid, copy_stat.st_gid, stat.S_IMODE(copy_stat.st_mode)) != (
                source_stat.st_uid, source_stat.st_gid, stat.S_IMODE(source_stat.st_mode)
            ):
                raise UnsupportedTagLayout("復旧用コピーの所有者または権限が一致しません。")
            source_attrs = {key: os.getxattr(stream.fileno(), key) for key in os.listxattr(stream.fileno())}
            copy_attrs = {key: os.getxattr(descriptor, key) for key in os.listxattr(descriptor)}
            if source_attrs != copy_attrs:
                raise UnsupportedTagLayout("復旧用コピーの拡張属性が一致しません。")
            if expected_version is not None and file_version(stream) != expected_version:
                raise EditConflict("復旧の準備中にファイルが変更されました。")
            os.replace(copy_name, original_name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            current = os.stat(original_name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (copy_stat.st_dev, copy_stat.st_ino):
                raise EditConflict("復旧したファイルが変更されました。")
            with os.fdopen(os.dup(descriptor), "rb") as restored:
                if _stream_digest(restored) != record["recovery_sha256"]:
                    raise OSError("復旧したファイルの検証に失敗しました。")
                version = file_version(restored)
            if outcome is None:
                store.set_recovery(file_id, backup_id, None, None, None)
            else:
                store.finish_recovery(file_id, backup_id, outcome)
            return version
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def recover_interrupted_copy(path: Path, file_id: str, expected_version: str,
                             backup_id: int, store: BackupStore, music_root: Path | None = None) -> str:
    """Recover a stopped or failed resize using the verified temporary copy."""
    with locked_music_file(path, music_root) as stream:
        _verify_path_identity(path, stream)
        if file_version(stream) != expected_version:
            raise EditConflict("表示後にファイルが変更されました。")
        record = store.get(file_id, backup_id)
        if record is None or record["result"] not in {"pending", "failed_unrecovered"} or record["recovery_state"] != "ready":
            raise UnsupportedTagLayout("復旧できる一時コピーがありません。")
        current = os.fstat(stream.fileno())
        if (current.st_dev, current.st_ino) == (record["recovery_copy_device"], record["recovery_copy_inode"]):
            name = record["recovery_name"]
            if not isinstance(name, str) or not re.fullmatch(r"\.id3-tag-[0-9a-f]{32}\.recovery", name):
                raise UnsupportedTagLayout("復旧用コピーの記録が不正です。")
            parent, _ = _open_music_parent(path, music_root)
            try:
                try:
                    os.stat(name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise EditConflict("一時コピーの状態を確認できません。")
                if _stream_digest(stream) != record["recovery_sha256"] or file_version(stream) != expected_version:
                    raise EditConflict("復旧済みファイルの内容を確認できません。")
                _verify_path_identity(path, stream)
                store.finish_recovery(file_id, backup_id, "interrupted_restored")
                return file_version(stream)
            finally:
                os.close(parent)
        return _restore_recovery_copy(path, music_root, stream, store, file_id, backup_id,
                                      expected_version, "interrupted_restored")


def _pack(value: object) -> object:
    """Encode Mutagen's MP4 value types without serializing executable objects."""
    if isinstance(value, MP4Cover):
        return {"type": "cover", "data": base64.b64encode(value).decode(), "format": int(value.imageformat)}
    if isinstance(value, MP4FreeForm):
        return {"type": "freeform", "data": base64.b64encode(value).decode(), "format": int(value.dataformat)}
    if isinstance(value, bytes):
        return {"type": "bytes", "data": base64.b64encode(value).decode()}
    if isinstance(value, tuple):
        return {"type": "tuple", "items": [_pack(item) for item in value]}
    if isinstance(value, list):
        return [_pack(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise ValueError(f"Unsupported MP4 tag value: {type(value).__name__}")


def _unpack(value: object) -> object:
    if isinstance(value, list):
        return [_unpack(item) for item in value]
    if isinstance(value, dict):
        kind = value.get("type")
        if kind == "tuple":
            return tuple(_unpack(item) for item in value["items"])
        data = base64.b64decode(value["data"], validate=True)
        if kind == "cover":
            return MP4Cover(data, imageformat=value["format"])
        if kind == "freeform":
            return MP4FreeForm(data, dataformat=value["format"])
        if kind == "bytes":
            return data
        raise ValueError("Invalid tag backup")
    return value


def _read_id3(stream: BinaryIO) -> ID3:
    stream.seek(0)
    try:
        return ID3(stream)
    except ID3NoHeaderError:
        return ID3()


def _snapshot(stream: BinaryIO, suffix: str) -> bytes:
    stream.seek(0)
    if suffix == ".mp3":
        try:
            tags = ID3(stream)
        except ID3NoHeaderError:
            return b""
        saved = io.BytesIO()
        tags.save(saved, v2_version=3 if tags.version[1] == 3 else 4, v1=0)
        return saved.getvalue()
    if suffix == ".m4a":
        media = MP4(stream)
        return json.dumps({key: _pack(value) for key, value in (media.tags or {}).items()},
                          ensure_ascii=False, sort_keys=True).encode("utf-8")
    raise UnsupportedTagLayout("Unsupported file format")


def _apply_mp3(stream: BinaryIO, updates: dict[str, str], comment_ref: tuple[str, str] | None,
               artwork: dict[str, ArtworkChange]) -> None:
    tags = _read_id3(stream)
    for field, value in updates.items():
        if field == "comment":
            comments = tags.getall("COMM")
            if comment_ref is None and len(comments) > 1:
                raise UnsupportedTagLayout("Select a comment to edit")
            matches = [item for item in comments if comment_ref is None or (item.lang, item.desc) == comment_ref]
            if comment_ref is not None and len(matches) != 1:
                raise ValueError("選択したコメントが見つかりません。")
            language, desc = (matches[0].lang, matches[0].desc) if matches else ("eng", "")
            if matches:
                del tags[matches[0].HashKey]
            if value:
                tags.add(COMM(encoding=3, lang=language, desc=desc, text=[value]))
            continue
        if field == "date":
            validate_date(value)
            frame_id, frame_type = "TDRC", TDRC
        elif field in MP3_TEXT_FRAMES:
            frame_id, frame_type = MP3_TEXT_FRAMES[field]
        else:
            raise ValueError("Unsupported tag field")
        if field in {"tracknumber", "discnumber"}:
            validate_number(value)
        tags.delall(frame_id)
        if value:
            tags.add(frame_type(encoding=3, text=[value]))
    for side, change in artwork.items():
        if side not in {"front", "back"}:
            raise ValueError("Unsupported artwork side")
        _validate_artwork(change)
        picture_type = 3 if side == "front" else 4
        pictures = [item for item in tags.getall("APIC") if item.type == picture_type]
        if change.description is None and len(pictures) > 1:
            raise UnsupportedTagLayout("Select the artwork to edit")
        matches = [item for item in pictures if change.description is None or item.desc == change.description]
        if change.description is not None and len(matches) != 1:
            raise ValueError("選択した画像が見つかりません。")
        description = matches[0].desc if matches else f"{side} cover"
        if matches:
            del tags[matches[0].HashKey]
        elif any(item.desc == description for item in tags.getall("APIC")):
            description = f"{side} cover {uuid.uuid4().hex[:8]}"
        if change.data is not None:
            tags.add(APIC(encoding=3, mime=change.mime, type=picture_type, desc=description, data=change.data))
    version = 3 if tags.version[1] == 3 else 4
    check = io.BytesIO()
    tags.save(check, v2_version=version, v1=0)
    check.seek(0)
    expected = ID3(check)
    stream.seek(0)
    tags.save(stream, v2_version=version, v1=0)
    if _read_id3(stream) != expected:
        raise OSError("Saved tags failed verification")


def _apply_mp4(stream: BinaryIO, updates: dict[str, str], comment_index: int | None,
               artwork: dict[str, ArtworkChange], confirm_multiple: bool) -> None:
    stream.seek(0)
    media = MP4(stream)
    if media.tags is None:
        media.add_tags()
    tags = media.tags
    if tags is None:
        raise ValueError("MP4 tags could not be created")
    for field, value in updates.items():
        if field not in MP4_TEXT_ITEMS:
            raise ValueError("Unsupported tag field")
        key = MP4_TEXT_ITEMS[field]
        if field == "date":
            validate_date(value)
        if field in {"tracknumber", "discnumber"}:
            number = validate_number(value)
            if number:
                tags[key] = [number]
            else:
                tags.pop(key, None)
        elif field == "comment":
            comments = list(tags.get(key, []))
            if comment_index is None and len(comments) > 1:
                raise UnsupportedTagLayout("Select a comment to edit")
            index = 0 if comment_index is None else comment_index
            if comment_index is not None and index >= len(comments):
                raise ValueError("選択したコメントが見つかりません。")
            if index < len(comments):
                if value:
                    comments[index] = value
                else:
                    comments.pop(index)
            elif value:
                comments.append(value)
            if comments:
                tags[key] = comments
            else:
                tags.pop(key, None)
        elif value:
            tags[key] = [value]
        else:
            tags.pop(key, None)
    if artwork:
        if set(artwork) != {"cover"}:
            raise ValueError("M4A supports one artwork selection")
        change = artwork["cover"]
        _validate_artwork(change)
        if len(tags.get("covr", [])) > 1 and not confirm_multiple:
            raise UnsupportedTagLayout("Multiple images will be replaced; confirmation is required")
        if change.data is None:
            tags.pop("covr", None)
        else:
            image_format = MP4Cover.FORMAT_JPEG if change.mime == "image/jpeg" else MP4Cover.FORMAT_PNG
            tags["covr"] = [MP4Cover(change.data, imageformat=image_format)]
    expected = {key: _pack(value) for key, value in tags.items()}
    stream.seek(0)
    media.save(stream)
    stream.seek(0)
    actual = MP4(stream).tags or {}
    if {key: _pack(value) for key, value in actual.items()} != expected:
        raise OSError("Saved tags failed verification")


def _restore_tags(stream: BinaryIO, suffix: str, snapshot: bytes) -> None:
    stream.seek(0)
    if suffix == ".mp3":
        if not snapshot:
            ID3().delete(stream, delete_v1=False, delete_v2=True)
            return
        tags = ID3(io.BytesIO(snapshot))
        tags.save(stream, v2_version=3 if tags.version[1] == 3 else 4, v1=0)
    elif suffix == ".m4a":
        data = json.loads(snapshot)
        media = MP4(stream)
        if media.tags is None:
            media.add_tags()
        if media.tags is None:
            raise ValueError("MP4 tags could not be created")
        media.tags.clear()
        for key, value in data.items():
            media.tags[key] = _unpack(value)
        stream.seek(0)
        media.save(stream)
    else:
        raise UnsupportedTagLayout("Unsupported file format")


def _transaction(path: Path, file_id: str, expected_version: str, store: BackupStore,
                 operation: str, source_id: int | None, fields: list[str],
                 writer: Callable[[BinaryIO], None], music_root: Path | None,
                 events: Callable[[str, int, bool | None], None] | None,
                 library_id: str | None, expected_snapshot: bytes | None = None) -> tuple[int, str]:
    with locked_music_file(path, music_root) as stream:
        if any(row["result"] in {"pending", "failed_unrecovered"} or row["recovery_name"] for row in store.list(file_id)):
            raise UnsupportedTagLayout("未完了のバックアップがあります。履歴を確認してください。")
        _verify_path_identity(path, stream)
        if file_version(stream) != expected_version:
            raise EditConflict("The selected file has changed")
        snapshot = _snapshot(stream, path.suffix.lower())
        relative = str(path.relative_to(music_root)) if music_root else path.name
        backup_id = store.create(file_id, operation, source_id, json.dumps(fields),
                                 f"mutagen-{path.suffix.lower()[1:]}-v1", snapshot, relative, library_id)
        if events:
            events("backup_created", backup_id, None)
        try:
            _create_recovery_copy(path, music_root, stream, expected_version, store, file_id, backup_id)
        except Exception:
            store.set_result(file_id, backup_id, "not_applied")
            raise
        try:
            _verify_path_identity(path, stream)
            if file_version(stream) != expected_version:
                raise EditConflict("The selected file changed before writing")
            if events:
                events("write_started", backup_id, None)
            writer(stream)
            stream.flush()
            os.fsync(stream.fileno())
            _verify_path_identity(path, stream)
            after = _snapshot(stream, path.suffix.lower())
            if expected_snapshot is not None and not _same_snapshot(path.suffix.lower(), after, expected_snapshot):
                raise OSError("Restored tags failed verification")
            version = file_version(stream)
            store.set_result(file_id, backup_id, "completed")
        except Exception as error:
            recovered = False
            try:
                _restore_recovery_copy(path, music_root, stream, store, file_id, backup_id, outcome="failed_recovered")
                recovered = True
            except Exception:
                logging.getLogger(__name__).exception("Could not restore recovery copy")
                store.set_result(file_id, backup_id, "failed_unrecovered")
            if recovered and isinstance(error, (ValueError, EditConflict)):
                raise error
            raise EditFailure("Tag update failed", recovered=recovered, backup_id=backup_id) from error
        try:
            _remove_recovery_copy(path, music_root, stream, store, file_id, backup_id)
        except (OSError, ValueError, sqlite3.Error):
            logging.getLogger(__name__).exception("Could not remove recovery copy")
        if events:
            events("write_verified", backup_id, None)
        return backup_id, version


def _same_snapshot(suffix: str, left: bytes, right: bytes) -> bool:
    if suffix == ".mp3":
        if not left or not right:
            return left == right
        return ID3(io.BytesIO(left)) == ID3(io.BytesIO(right))
    return json.loads(left) == json.loads(right)


def save_text_updates(path: Path, file_id: str, expected_version: str, updates: dict[str, str],
                      store: BackupStore, comment_ref: tuple[str, str] | None = None,
                      comment_index: int | None = None, artwork: dict[str, ArtworkChange] | None = None,
                      confirm_multiple_artwork: bool = False, music_root: Path | None = None,
                      events: Callable[[str, int, bool | None], None] | None = None,
                      library_id: str | None = None) -> tuple[int, str]:
    images = artwork or {}
    if not updates and not images:
        raise ValueError("No fields selected")
    def write(stream: BinaryIO) -> None:
        if path.suffix.lower() == ".mp3":
            _apply_mp3(stream, updates, comment_ref, images)
        elif path.suffix.lower() == ".m4a":
            _apply_mp4(stream, updates, comment_index, images, confirm_multiple_artwork)
        else:
            raise UnsupportedTagLayout("Unsupported file format")
    return _transaction(path, file_id, expected_version, store, "save", None,
                        sorted([*updates, *(f"artwork_{side}" for side in images)]),
                        write, music_root, events, library_id)


def restore_backup(path: Path, file_id: str, expected_version: str, source_id: int, store: BackupStore,
                   music_root: Path | None = None, events: Callable[[str, int, bool | None], None] | None = None,
                   library_id: str | None = None) -> tuple[int, str]:
    record = store.get(file_id, source_id)
    if record is None or record["snapshot_format"] != f"mutagen-{path.suffix.lower()[1:]}-v1":
        raise ValueError("Backup does not exist for this file")
    snapshot = record["snapshot"]
    return _transaction(path, file_id, expected_version, store, "restore", source_id, [],
                        lambda stream: _restore_tags(stream, path.suffix.lower(), snapshot),
                        music_root, events, library_id, expected_snapshot=snapshot)


def _discard_incomplete_copy(path: Path, root: Path | None, stream: BinaryIO,
                             store: BackupStore, file_id: str, backup_id: int) -> None:
    record = store.get(file_id, backup_id)
    if record is None or record["recovery_state"] != "copying":
        raise UnsupportedTagLayout("未完了コピーがありません。")
    if _snapshot(stream, path.suffix.lower()) != record["snapshot"]:
        raise EditConflict("元ファイルが変更されています。")
    name = record["recovery_name"]
    if not isinstance(name, str) or not re.fullmatch(r"\.id3-tag-[0-9a-f]{32}\.recovery", name):
        raise UnsupportedTagLayout("一時コピーの記録が不正です。")
    parent, _ = _open_music_parent(path, root)
    try:
        _verify_path_identity(path, stream)
        try:
            os.unlink(name, dir_fd=parent)
        except FileNotFoundError:
            logging.getLogger(__name__).info("Incomplete copy already absent")
        store.set_recovery(file_id, backup_id, None, None, None)
        if record["result"] == "pending":
            store.set_result(file_id, backup_id, "not_applied")
    finally:
        os.close(parent)
