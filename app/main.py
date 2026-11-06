import base64
import binascii
import hashlib
import json
import os
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app import lookup, tag_editor

ROOT = Path(os.environ.get("MUSIC_DIR", "/music")).resolve()
DATA = Path(os.environ.get("DATA_DIR", "/data"))
LIBRARY_ID = os.environ.get("LIBRARY_ID", str(ROOT))
WRITES_ENABLED = os.environ.get("ENABLE_TAG_WRITES") == "1"
STATIC = Path(__file__).parent / "static"
FIELDS = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "date": "TDRC", "tracknumber": "TRCK", "discnumber": "TPOS", "genre": "TCON"}
MP4_FIELDS = {"title": "\u00a9nam", "artist": "\u00a9ART", "album": "\u00a9alb", "albumartist": "aART", "date": "\u00a9day", "genre": "\u00a9gen", "comment": "\u00a9cmt"}
FORMATS = {".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC), name="static")
AUDIT_LOCK = threading.Lock()


class ArtworkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data: str | None = Field(default=None, max_length=14 * 1024 * 1024)
    mime: str | None = None
    description: str | None = None


class SaveInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")
    updates: dict[str, str] = Field(default_factory=dict)
    comment_ref: tuple[str, str] | None = None
    comment_index: int | None = Field(default=None, ge=0)
    artwork: dict[str, ArtworkInput] = Field(default_factory=dict)
    confirm_multiple_artwork: bool = False

    @field_validator("updates")
    @classmethod
    def validate_updates(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"title", "artist", "album", "albumartist", "date", "tracknumber", "discnumber", "genre", "comment"}
        if not set(value).issubset(allowed) or any(len(text) > 65535 or "\x00" in text for text in value.values()):
            raise ValueError("タグの項目または値が不正です。")
        return value

    @field_validator("artwork")
    @classmethod
    def validate_artwork_keys(cls, value: dict[str, ArtworkInput]) -> dict[str, ArtworkInput]:
        if not set(value).issubset({"front", "back", "cover"}) or len(value) > 2:
            raise ValueError("画像の項目が不正です。")
        return value


class RestoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_version: str = Field(pattern=r"^[0-9a-f]{64}$")


def require_local_origin(request: Request, *, mutation: bool = True) -> None:
    host = request.headers.get("host", "").lower()
    if host not in {"127.0.0.1:18081", "localhost:18081"}:
        raise HTTPException(status_code=403, detail="ローカルからのみ操作できます。")
    if mutation and request.headers.get("origin") != f"http://{host}":
        raise HTTPException(status_code=403, detail="同じ画面から操作してください。")
    if mutation and not WRITES_ENABLED:
        raise HTTPException(status_code=403, detail="タグ書き込みは無効です。")


def file_key(path: Path) -> str:
    identity = f"{LIBRARY_ID}\0{path.relative_to(ROOT)}"
    return hashlib.sha256(identity.encode()).hexdigest()


def can_write_file(path: Path) -> bool:
    try:
        return (WRITES_ENABLED and not (os.statvfs(path).f_flag & os.ST_RDONLY)
                and os.access(path, os.W_OK) and path.stat().st_nlink == 1)
    except OSError:
        return False


def backup_store() -> tag_editor.BackupStore:
    return tag_editor.BackupStore(DATA / "backups.sqlite3")


def audit_logger() -> tag_editor.AuditLogger:
    with AUDIT_LOCK:
        if not hasattr(app.state, "audit_logger"):
            app.state.audit_logger = tag_editor.AuditLogger(DATA / "logs")
    return app.state.audit_logger


def decode_artwork(values: dict[str, ArtworkInput]) -> dict[str, tag_editor.ArtworkChange]:
    result = {}
    for side, item in values.items():
        try:
            data = base64.b64decode(item.data, validate=True) if item.data is not None else None
        except (ValueError, binascii.Error) as error:
            raise HTTPException(status_code=422, detail="画像データが不正です。") from error
        result[side] = tag_editor.ArtworkChange(data, item.mime, item.description)
    return result


def operation_error(error: Exception) -> HTTPException:
    if isinstance(error, tag_editor.EditFailure):
        return HTTPException(status_code=500, detail={"message": "書き込みに失敗しました。", "recovered": error.recovered, "backup_id": error.backup_id})
    if isinstance(error, (tag_editor.EditConflict, BlockingIOError)):
        return HTTPException(status_code=409, detail="ファイルが変更中か、表示後に変更されました。再読み込みしてください。")
    if isinstance(error, tag_editor.UnsupportedTagLayout):
        translations = {
            "Select a comment to edit": "編集するコメントを選択してください。",
            "Select the artwork to edit": "変更する既存画像を選択してください。",
            "Multiple images will be replaced; confirmation is required": "複数の画像を置き換える確認が必要です。",
        }
        detail = translations.get(str(error), f"このファイルは安全に変更できません: {error}")
        return HTTPException(status_code=422, detail=detail)
    if isinstance(error, ValueError):
        return HTTPException(status_code=422, detail=str(error))
    return HTTPException(status_code=500, detail="処理に失敗しました。ログを確認してください。")


class CoverRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        host = (target.hostname or "").lower()
        allowed = host == "archive.org" or host.endswith(".archive.org") or host == "coverartarchive.org"
        if target.scheme != "https" or not allowed or target.username or target.password or target.port not in (None, 443):
            raise urllib.error.HTTPError(newurl, 403, "Unsupported artwork redirect", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@app.get("/api/releases/{release_id}/artwork/{side}")
def online_artwork(release_id: uuid.UUID, side: str, request: Request) -> Response:
    require_local_origin(request, mutation=False)
    if side not in {"front", "back"}:
        raise HTTPException(status_code=400, detail="画像の面が不正です。")
    url = f"https://coverartarchive.org/release/{release_id}/{side}"
    opener = urllib.request.build_opener(CoverRedirectHandler())
    source = urllib.request.Request(url, headers={"User-Agent": lookup.USER_AGENT})
    try:
        with opener.open(source, timeout=20) as response:
            mime = response.headers.get_content_type()
            data = response.read(10 * 1024 * 1024 + 1)
        tag_editor._validate_artwork(tag_editor.ArtworkChange(data, mime))
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        raise HTTPException(status_code=404, detail="画像を取得できませんでした。") from error
    return Response(content=data, media_type=mime, headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/recovery/pending")
def pending_recoveries(request: Request) -> list[dict[str, object]]:
    require_local_origin(request, mutation=False)
    result = []
    for row in backup_store().pending(LIBRARY_ID):
        name = row.get("file_path")
        if not isinstance(name, str):
            continue
        relative = Path(name)
        if relative.is_absolute() or not relative.parts or any(part in {".", ".."} for part in relative.parts):
            continue
        path = ROOT / relative
        if file_key(path) != row["file_id"]:
            continue
        result.append({"id": row["id"], "path": name, "identifier": encode_path(path),
                       "result": row["result"], "recovery_state": row["recovery_state"],
                       "recovery_name": row["recovery_name"]})
    return result


@app.post("/api/files/{identifier}/tags")
def save_tags(identifier: str, body: SaveInput, request: Request) -> dict[str, object]:
    require_local_origin(request)
    path = resolve_id(identifier)
    operation_id = str(uuid.uuid4())
    audit = audit_logger()
    name = str(path.relative_to(ROOT))
    fields = sorted([*body.updates, *(f"artwork_{side}" for side in body.artwork)])
    audit.event("save_started", name, operation_id, fields=fields)
    try:
        store = backup_store()
        key = file_key(path)
        backup_id, version = tag_editor.save_text_updates(
            path, key, body.expected_version, body.updates, store,
            body.comment_ref, body.comment_index, decode_artwork(body.artwork), body.confirm_multiple_artwork, ROOT,
            lambda action, backup_id, recovered: audit.event(action, name, operation_id, backup_id=backup_id, recovered=recovered),
            LIBRARY_ID,
        )
    except HTTPException:
        audit.event("save_failed", name, operation_id, error_type="InvalidInput")
        raise
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        audit.event("save_failed", name, operation_id, backup_id=getattr(error, "backup_id", None), error_type=type(error).__name__, recovered=getattr(error, "recovered", None))
        raise operation_error(error) from error
    audit.event("save_verified", name, operation_id, backup_id=backup_id)
    record = store.get(key, backup_id)
    return {"backup_id": backup_id, "version": version,
            "temporary_copy_remaining": record is not None and record["recovery_name"] is not None}


@app.get("/api/files/{identifier}/history")
def history(identifier: str, request: Request) -> list[dict[str, object]]:
    require_local_origin(request, mutation=False)
    path = resolve_id(identifier)
    rows = backup_store().list(file_key(path))
    for row in rows:
        row["fields"] = json.loads(row["fields"])
    return rows


@app.get("/api/files/{identifier}/recovery")
def recovery_info(identifier: str, request: Request) -> dict[str, object]:
    require_local_origin(request, mutation=False)
    path = resolve_id(identifier)
    try:
        with path.open("rb") as source:
            version = tag_editor.file_version(source)
    except (OSError, tag_editor.UnsupportedTagLayout) as error:
        raise HTTPException(status_code=409, detail="ファイルを再読み込みできません。") from error
    return {"name": path.name, "version": version, "format": path.suffix.lower()[1:], "write_enabled": can_write_file(path)}


@app.post("/api/files/{identifier}/history/{backup_id}/restore")
def restore_tags(identifier: str, backup_id: int, body: RestoreInput, request: Request) -> dict[str, object]:
    require_local_origin(request)
    path = resolve_id(identifier)
    operation_id = str(uuid.uuid4())
    audit = audit_logger()
    name = str(path.relative_to(ROOT))
    audit.event("restore_started", name, operation_id, backup_id=backup_id)
    try:
        store = backup_store()
        key = file_key(path)
        new_id, version = tag_editor.restore_backup(
            path, key, body.expected_version, backup_id, store, ROOT,
            lambda action, new_id, recovered: audit.event(action, name, operation_id, backup_id=new_id, recovered=recovered),
            LIBRARY_ID,
        )
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        audit.event("restore_failed", name, operation_id, backup_id=getattr(error, "backup_id", None), error_type=type(error).__name__, recovered=getattr(error, "recovered", None))
        raise operation_error(error) from error
    audit.event("restore_verified", name, operation_id, backup_id=new_id)
    record = store.get(key, new_id)
    return {"backup_id": new_id, "version": version,
            "temporary_copy_remaining": record is not None and record["recovery_name"] is not None}


@app.delete("/api/files/{identifier}/history/{backup_id}", status_code=204)
def delete_backup(identifier: str, backup_id: int, request: Request) -> Response:
    require_local_origin(request)
    path = resolve_id(identifier)
    name = str(path.relative_to(ROOT))
    operation_id = str(uuid.uuid4())
    audit = audit_logger()
    try:
        with tag_editor.locked_music_file(path, ROOT) as stream:
            tag_editor._verify_path_identity(path, stream)
            store = backup_store()
            key = file_key(path)
            record = store.get(key, backup_id)
            if record is not None and record["recovery_name"] is not None:
                raise HTTPException(status_code=409, detail="一時コピーが残っているため、先に確認してください。")
            if not store.delete(key, backup_id):
                raise HTTPException(status_code=404, detail="バックアップが見つかりません。")
    except (OSError, ValueError, sqlite3.Error) as error:
        audit.event("backup_delete_failed", name, operation_id, backup_id=backup_id, error_type=type(error).__name__)
        raise operation_error(error) from error
    audit.event("backup_deleted", name, operation_id, backup_id=backup_id)
    return Response(status_code=204)


@app.post("/api/files/{identifier}/history/{backup_id}/recover")
def recover_copy(identifier: str, backup_id: int, body: RestoreInput, request: Request) -> dict[str, str]:
    require_local_origin(request)
    path = resolve_id(identifier)
    name = str(path.relative_to(ROOT))
    operation_id = str(uuid.uuid4())
    audit = audit_logger()
    audit.event("copy_recovery_started", name, operation_id, backup_id=backup_id)
    try:
        version = tag_editor.recover_interrupted_copy(path, file_key(path), body.expected_version,
                                                      backup_id, backup_store(), ROOT)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        audit.event("copy_recovery_failed", name, operation_id, backup_id=backup_id, error_type=type(error).__name__)
        raise operation_error(error) from error
    audit.event("copy_recovery_verified", name, operation_id, backup_id=backup_id)
    return {"version": version}


@app.post("/api/files/{identifier}/history/{backup_id}/cleanup", status_code=204)
def cleanup_copy(identifier: str, backup_id: int, request: Request) -> Response:
    require_local_origin(request)
    path = resolve_id(identifier)
    store = backup_store()
    key = file_key(path)
    try:
        with tag_editor.locked_music_file(path, ROOT) as stream:
            record = store.get(key, backup_id)
            if record is None:
                raise HTTPException(status_code=409, detail="この一時コピーは削除できません。")
            if record["recovery_state"] == "copying" and record["result"] in {"pending", "not_applied"}:
                tag_editor._discard_incomplete_copy(path, ROOT, stream, store, key, backup_id)
            elif record["recovery_state"] == "ready" and record["result"] in {"completed", "not_applied", "failed_recovered", "interrupted_restored"}:
                tag_editor._remove_recovery_copy(path, ROOT, stream, store, key, backup_id)
            else:
                raise tag_editor.UnsupportedTagLayout("この一時コピーは削除できません。")
    except (OSError, ValueError, sqlite3.Error) as error:
        raise operation_error(error) from error
    return Response(status_code=204)


@app.post("/api/files/{identifier}/lookup", status_code=202)
def start_lookup(identifier: str) -> dict[str, str]:
    path = resolve_id(identifier)
    try:
        return {"job_id": lookup.start_lookup(path)}
    except lookup.LookupError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


@app.get("/api/lookups/{job_id}")
def lookup_status(job_id: str) -> dict[str, object]:
    job = lookup.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="取得処理が見つかりません。")
    return job


@app.delete("/api/lookups/{job_id}", status_code=204)
def cancel_lookup(job_id: str) -> Response:
    lookup.cancel_job(job_id)
    return Response(status_code=204)


@app.get("/api/recordings/{recording_id}/releases/{release_id}")
def selected_release(recording_id: str, release_id: str) -> dict[str, object]:
    try:
        return lookup.release_details(recording_id, release_id)
    except lookup.LookupError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error


def encode_path(path: Path) -> str:
    return base64.urlsafe_b64encode(str(path.relative_to(ROOT)).encode()).decode().rstrip("=")


def resolve_id(identifier: str, directory: bool = False) -> Path:
    try:
        relative = base64.b64decode(identifier + "=" * (-len(identifier) % 4), altchars=b"-_", validate=True).decode()
        path = (ROOT / relative).resolve()
        if not path.is_relative_to(ROOT) or (not path.is_dir() if directory else not path.is_file() or path.suffix.lower() not in FORMATS):
            raise ValueError("invalid path")
        return path
    except (ValueError, UnicodeError, binascii.Error, OSError):
        raise HTTPException(status_code=404, detail="File or directory not found") from None


def read_tags(path: Path) -> ID3:
    try:
        return ID3(path)
    except ID3NoHeaderError:
        return ID3()
    except (MutagenError, OSError) as error:
        raise HTTPException(status_code=422, detail="Could not read ID3 tags") from error


def read_mp4(path: Path) -> MP4:
    try:
        return MP4(path)
    except (MutagenError, OSError) as error:
        raise HTTPException(status_code=422, detail="Could not read M4A tags") from error


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/files")
def list_files(directory: str = "") -> dict[str, object]:
    path = resolve_id(directory, directory=True)
    try:
        entries = sorted(path.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.casefold()))
        result = []
        for entry in entries:
            resolved = entry.resolve()
            if not resolved.is_relative_to(ROOT):
                continue
            if resolved.is_dir() or (resolved.is_file() and resolved.suffix.lower() in FORMATS):
                result.append({"id": encode_path(resolved), "name": entry.name, "directory": resolved.is_dir()})
        return {"name": path.name, "parent": encode_path(path.parent) if path != ROOT else None, "entries": result}
    except OSError as error:
        raise HTTPException(status_code=403, detail="Could not read directory") from error


@app.get("/api/files/{identifier}")
def file_info(identifier: str) -> dict[str, object]:
    path = resolve_id(identifier)
    try:
        with path.open("rb") as source:
            before = tag_editor.file_version(source)
        result = read_file_info(path)
        with path.open("rb") as source:
            after = tag_editor.file_version(source)
    except (OSError, tag_editor.UnsupportedTagLayout) as error:
        raise HTTPException(status_code=409, detail="ファイルが変更されたため、再読み込みしてください。") from error
    if before != after:
        raise HTTPException(status_code=409, detail="ファイルが変更されたため、再読み込みしてください。")
    result["version"] = after
    result["write_enabled"] = can_write_file(path)
    return result


def read_file_info(path: Path) -> dict[str, object]:
    if path.suffix.lower() == ".m4a":
        mp4 = read_mp4(path)
        mp4_tags = mp4.tags or {}
        values = {key: ", ".join(str(value) for value in mp4_tags.get(atom, [])) for key, atom in MP4_FIELDS.items()}
        tracks = mp4_tags.get("trkn", [])
        values["tracknumber"] = "/".join(str(value) for value in tracks[0] if value) if tracks else ""
        discs = mp4_tags.get("disk", [])
        values["discnumber"] = "/".join(str(value) for value in discs[0] if value) if discs else ""
        tag_values = {key: [str(value) for value in mp4_tags.get(atom, [])] for key, atom in MP4_FIELDS.items() if key != "comment"}
        tag_values["tracknumber"] = ["/".join(str(value) for value in pair if value) for pair in tracks]
        tag_values["discnumber"] = ["/".join(str(value) for value in pair if value) for pair in discs]
        comments = [{"value": str(value)} for value in mp4_tags.get("\u00a9cmt", [])]
        covers = mp4_tags.get("covr", [])
        return {"name": path.name, "tags": values, "tag_values": tag_values, "comments": comments, "artwork": bool(covers), "back_artwork": False, "format": "m4a", "artworks": {"cover": [{"index": index, "sha256": hashlib.sha256(bytes(item)).hexdigest()} for index, item in enumerate(covers)]}}
    tags = read_tags(path)
    values = {key: str(tags.get(frame, "")) for key, frame in FIELDS.items()}
    tag_values = {key: [str(value) for value in getattr(tags.get(frame), "text", [])] for key, frame in FIELDS.items()}
    comments = [{"language": item.lang, "description": item.desc, "value": str(item)} for item in tags.getall("COMM")]
    values["comment"] = "; ".join(item["value"] for item in comments)
    pictures = tags.getall("APIC")
    artworks = {side: [{"description": item.desc, "sha256": hashlib.sha256(item.data).hexdigest()} for item in pictures if item.type == picture_type]
                for side, picture_type in (("front", 3), ("back", 4))}
    return {"name": path.name, "tags": values, "tag_values": tag_values, "comments": comments, "artwork": bool(artworks["front"]), "back_artwork": bool(artworks["back"]), "format": "mp3", "artworks": artworks}


@app.get("/api/files/{identifier}/audio")
def audio(identifier: str) -> FileResponse:
    path = resolve_id(identifier)
    return FileResponse(path, media_type=FORMATS[path.suffix.lower()])


@app.get("/api/files/{identifier}/artwork")
def artwork(identifier: str, side: str = "front", index: int = Query(default=0, ge=0)) -> Response:
    path = resolve_id(identifier)
    if side not in {"front", "back"}:
        raise HTTPException(status_code=400, detail="Invalid artwork side")
    if path.suffix.lower() == ".m4a":
        if side == "back":
            raise HTTPException(status_code=404, detail="Artwork not found")
        mp4 = read_mp4(path)
        pictures = (mp4.tags or {}).get("covr", [])
        if index >= len(pictures):
            raise HTTPException(status_code=404, detail="Artwork not found")
        picture = pictures[index]
        formats = {MP4Cover.FORMAT_JPEG: "image/jpeg", MP4Cover.FORMAT_PNG: "image/png"}
        if picture.imageformat not in formats:
            raise HTTPException(status_code=415, detail="Unsupported artwork format")
        return Response(content=bytes(picture), media_type=formats[picture.imageformat], headers={"X-Content-Type-Options": "nosniff"})
    pictures = read_tags(path).getall("APIC")
    pictures = [item for item in pictures if item.type == (4 if side == "back" else 3)]
    if index >= len(pictures):
        raise HTTPException(status_code=404, detail="Artwork not found")
    picture = pictures[index]
    if picture.mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise HTTPException(status_code=415, detail="Unsupported artwork format")
    return Response(content=picture.data, media_type=picture.mime, headers={"X-Content-Type-Options": "nosniff"})
