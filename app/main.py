import base64
import binascii
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from mutagen import MutagenError
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp4 import MP4, MP4Cover

ROOT = Path(os.environ.get("MUSIC_DIR", "/music")).resolve()
STATIC = Path(__file__).parent / "static"
FIELDS = {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2", "date": "TDRC", "tracknumber": "TRCK", "genre": "TCON"}
MP4_FIELDS = {"title": "\u00a9nam", "artist": "\u00a9ART", "album": "\u00a9alb", "albumartist": "aART", "date": "\u00a9day", "genre": "\u00a9gen"}
FORMATS = {".mp3": "audio/mpeg", ".m4a": "audio/mp4"}
app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC), name="static")


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
    if path.suffix.lower() == ".m4a":
        mp4 = read_mp4(path)
        mp4_tags = mp4.tags or {}
        values = {key: ", ".join(str(value) for value in mp4_tags.get(atom, [])) for key, atom in MP4_FIELDS.items()}
        tracks = mp4_tags.get("trkn", [])
        values["tracknumber"] = "/".join(str(value) for value in tracks[0] if value) if tracks else ""
        return {"name": path.name, "tags": values, "artwork": bool(mp4_tags.get("covr", []))}
    tags = read_tags(path)
    return {"name": path.name, "tags": {key: str(tags.get(frame, "")) for key, frame in FIELDS.items()}, "artwork": bool(tags.getall("APIC"))}


@app.get("/api/files/{identifier}/audio")
def audio(identifier: str) -> FileResponse:
    path = resolve_id(identifier)
    return FileResponse(path, media_type=FORMATS[path.suffix.lower()])


@app.get("/api/files/{identifier}/artwork")
def artwork(identifier: str) -> Response:
    path = resolve_id(identifier)
    if path.suffix.lower() == ".m4a":
        mp4 = read_mp4(path)
        pictures = (mp4.tags or {}).get("covr", [])
        if not pictures:
            raise HTTPException(status_code=404, detail="Artwork not found")
        picture = pictures[0]
        formats = {MP4Cover.FORMAT_JPEG: "image/jpeg", MP4Cover.FORMAT_PNG: "image/png"}
        if picture.imageformat not in formats:
            raise HTTPException(status_code=415, detail="Unsupported artwork format")
        return Response(content=bytes(picture), media_type=formats[picture.imageformat], headers={"X-Content-Type-Options": "nosniff"})
    pictures = read_tags(path).getall("APIC")
    if not pictures:
        raise HTTPException(status_code=404, detail="Artwork not found")
    picture = next((item for item in pictures if item.type == 3), pictures[0])
    if picture.mime not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
        raise HTTPException(status_code=415, detail="Unsupported artwork format")
    return Response(content=picture.data, media_type=picture.mime, headers={"X-Content-Type-Options": "nosniff"})
