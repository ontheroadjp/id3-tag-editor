import json
import logging
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

LOGGER = logging.getLogger(__name__)
USER_AGENT = "id3-tag-editor/0.1 (https://github.com/ontheroadjp/id3-tag-editor)"
MUSICBRAINZ = "https://musicbrainz.org/ws/2"
ACOUSTID = "https://api.acoustid.org/v2/lookup"
REQUEST_TIMEOUT = 20
FINGERPRINT_TIMEOUT = 120
_rate_lock = threading.Lock()
_last_request: dict[str, float] = {}
_jobs_lock = threading.Lock()
_jobs: dict[str, dict[str, object]] = {}
_cancellations: dict[str, threading.Event] = {}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lookup")
MAX_JOBS = 100
RETRYABLE_HTTP = {429, 502, 503, 504}
MAX_ATTEMPTS = 3


class LookupError(Exception):
    pass


class LookupCancelled(Exception):
    pass


def check_cancelled(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise LookupCancelled()


def request_json(url: str, *, data: dict[str, object] | None = None, service: str = "musicbrainz") -> dict:
    interval = 1.1 if service == "musicbrainz" else 0.4
    payload = urllib.parse.urlencode(data).encode() if data else None
    request = urllib.request.Request(url, data=payload, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    service_name = "MusicBrainz" if service == "musicbrainz" else "AcoustID"
    for attempt in range(MAX_ATTEMPTS):
        try:
            with _rate_lock:
                delay = interval - (time.monotonic() - _last_request.get(service, 0.0))
                if delay > 0:
                    time.sleep(delay)
                _last_request[service] = time.monotonic()
                with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
                    result = json.load(response)
            if not isinstance(result, dict):
                raise LookupError(f"{service_name} から不正な応答がありました。")
            return result
        except urllib.error.HTTPError as error:
            LOGGER.warning("%s returned HTTP %s (attempt %s)", service_name, error.code, attempt + 1)
            if error.code not in RETRYABLE_HTTP or attempt == MAX_ATTEMPTS - 1:
                raise LookupError(f"{service_name} が HTTP {error.code} を返しました。") from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            LOGGER.warning("%s connection failed (attempt %s)", service_name, attempt + 1)
            if attempt == MAX_ATTEMPTS - 1:
                raise LookupError(f"{service_name} に接続できませんでした。") from error
        except ValueError as error:
            raise LookupError(f"{service_name} から不正な応答がありました。") from error
        time.sleep(2 ** attempt)
    raise LookupError(f"{service_name} への取得に失敗しました。")


def fingerprint(path: Path) -> tuple[int, str]:
    try:
        completed = subprocess.run(["fpcalc", "-json", str(path)], capture_output=True, text=True, timeout=FINGERPRINT_TIMEOUT, check=True)
        result = json.loads(completed.stdout)
        duration = round(float(result["duration"]))
        value = result["fingerprint"]
        if duration < 1 or not isinstance(value, str) or not value:
            raise ValueError("empty fingerprint")
        return duration, value
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as error:
        raise LookupError("音声指紋を生成できませんでした。ファイルを確認してください。") from error


def artist_credit(credits: list[object]) -> str:
    return "".join((str(item.get("name", "")) + str(item.get("joinphrase", ""))) if isinstance(item, dict) else str(item) for item in credits)


def canonical_artist(credits: list[object]) -> str:
    return "".join((str((item.get("artist") or {}).get("name") or item.get("name", "")) + str(item.get("joinphrase", ""))) if isinstance(item, dict) else str(item) for item in credits)


def find_recordings(path: Path, api_key: str, warnings: list[str] | None = None, cancel: threading.Event | None = None) -> list[dict[str, object]]:
    check_cancelled(cancel)
    duration, value = fingerprint(path)
    check_cancelled(cancel)
    result = request_json(ACOUSTID, data={"client": api_key, "duration": duration, "fingerprint": value, "meta": "recordingids"}, service="acoustid")
    if result.get("status") != "ok":
        raise LookupError("AcoustID が照合を受け付けませんでした。API キーを確認してください。")
    recordings: list[dict[str, object]] = []
    seen: set[str] = set()
    for match in result.get("results", []):
        for recording in match.get("recordings", []):
            check_cancelled(cancel)
            try:
                recording_id = str(uuid.UUID(recording["id"]))
            except (ValueError, KeyError, TypeError):
                continue
            if recording_id in seen:
                continue
            seen.add(recording_id)
            try:
                details = request_json(f"{MUSICBRAINZ}/recording/{recording_id}?inc=artists&fmt=json")
                check_cancelled(cancel)
                releases: list[dict[str, str]] = []
                while True:
                    check_cancelled(cancel)
                    page = request_json(f"{MUSICBRAINZ}/release?recording={recording_id}&limit=100&offset={len(releases)}&fmt=json")
                    check_cancelled(cancel)
                    entries = page.get("releases", [])
                    releases.extend({"id": release.get("id", ""), "title": release.get("title", ""), "date": release.get("date", ""), "country": release.get("country", ""), "language": (release.get("text-representation") or {}).get("language", "")} for release in entries)
                    if not entries or len(releases) >= page.get("release-count", len(releases)):
                        break
            except LookupError as error:
                if not recordings or warnings is None:
                    raise
                warnings.append(f"一部の候補を取得できませんでした: {error}")
                return recordings
            credits = details.get("artist-credit", [])
            recordings.append({"id": recording_id, "score": match.get("score"), "title": details.get("title", ""), "artist": artist_credit(credits), "canonical_artist": canonical_artist(credits), "releases": releases})
    return recordings


def release_details(recording_id: str, release_id: str) -> dict[str, object]:
    try:
        recording_id = str(uuid.UUID(recording_id))
        release_id = str(uuid.UUID(release_id))
    except (ValueError, TypeError) as error:
        raise LookupError("候補の ID が無効です。") from error
    release = request_json(f"{MUSICBRAINZ}/release/{release_id}?inc=recordings+artists+artist-credits&fmt=json")
    for medium in release.get("media", []):
        for track in medium.get("tracks", []):
            recording = track.get("recording", {})
            if recording.get("id") == recording_id:
                number = str(track.get("number", ""))
                total = medium.get("track-count")
                tracknumber = f"{number}/{total}" if number.isdecimal() and isinstance(total, int) and total > 0 else number
                position = medium.get("position")
                medium_count = release.get("medium-count")
                discnumber = str(position) if isinstance(position, int) and position > 0 else ""
                if discnumber and isinstance(medium_count, int) and medium_count > 0:
                    discnumber += f"/{medium_count}"
                return {"title": track.get("title") or recording.get("title", ""), "artist": artist_credit(track.get("artist-credit") or recording.get("artist-credit") or []), "album": release.get("title", ""), "albumartist": artist_credit(release.get("artist-credit", [])), "date": release.get("date", ""), "tracknumber": tracknumber, "discnumber": discnumber, "genre": "", "artwork_url": f"https://coverartarchive.org/release/{release_id}/front-250", "artwork_back_url": f"https://coverartarchive.org/release/{release_id}/back-250"}
    raise LookupError("選択したリリースに録音が見つかりませんでした。")


def _run_job(job_id: str, path: Path, api_key: str, cancel: threading.Event) -> None:
    try:
        warnings: list[str] = []
        result = find_recordings(path, api_key, warnings, cancel)
        check_cancelled(cancel)
        with _jobs_lock:
            _jobs[job_id] = {"status": "complete", "recordings": result, "warnings": warnings}
    except LookupCancelled:
        with _jobs_lock:
            _jobs[job_id] = {"status": "cancelled"}
    except LookupError as error:
        with _jobs_lock:
            _jobs[job_id] = {"status": "error", "error": str(error)}
    except Exception:
        LOGGER.exception("Unexpected lookup failure")
        with _jobs_lock:
            _jobs[job_id] = {"status": "error", "error": "取得中に予期しないエラーが発生しました。"}
    finally:
        with _jobs_lock:
            _cancellations.pop(job_id, None)


def start_lookup(path: Path) -> str:
    api_key = os.environ.get("ACOUSTID_API_KEY", "").strip()
    if not api_key:
        raise LookupError("ACOUSTID_API_KEY が設定されていません。")
    with _jobs_lock:
        for event in _cancellations.values():
            event.set()
        if len(_jobs) >= MAX_JOBS:
            finished = next((key for key, job in _jobs.items() if job["status"] != "running"), None)
            if finished is None:
                raise LookupError("取得処理が混み合っています。しばらくして再試行してください。")
            del _jobs[finished]
        job_id = str(uuid.uuid4())
        cancel = threading.Event()
        _cancellations[job_id] = cancel
        _jobs[job_id] = {"status": "running"}
    _executor.submit(_run_job, job_id, path, api_key, cancel)
    return job_id


def get_job(job_id: str) -> dict[str, object] | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def cancel_job(job_id: str) -> None:
    with _jobs_lock:
        event = _cancellations.get(job_id)
        if event is not None:
            event.set()
