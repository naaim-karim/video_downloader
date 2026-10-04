"""Small HTTP API for inspecting and downloading publicly available videos."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import ipaddress
import logging
import os
import re
import shutil
import socket
import tempfile
import threading
from time import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

import yt_dlp
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl

MAX_FILE_BYTES = 500 * 1024 * 1024
MAX_URL_LENGTH = 2_000
VIDEO_EXTENSIONS = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi"}
SAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")
logger = logging.getLogger(__name__)
JOB_TTL_SECONDS = 15 * 60

app = FastAPI(title="Video Downloader API", version="0.1.0")

allowed_origins = [
    value.strip()
    for value in os.getenv("FRONTEND_ORIGINS", "http://localhost:8788").split(",")
    if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class VideoRequest(BaseModel):
    url: HttpUrl


class DownloadRequest(VideoRequest):
    format_id: str


@dataclass
class DownloadJob:
    id: str
    directory: str
    state: str = "queued"
    progress: float = 0
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    speed: float | None = None
    eta: int | None = None
    error: str | None = None
    file: Path | None = None
    filename: str | None = None
    created_at: float = field(default_factory=time)


jobs: dict[str, DownloadJob] = {}
jobs_lock = threading.Lock()


def _is_public_url(value: str) -> bool:
    """Reject local and private destinations before handing a URL to yt-dlp."""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False

    hostname = parsed.hostname.rstrip(".")
    if hostname.lower() in {"localhost", "localhost.localdomain"}:
        return False

    try:
        addresses = {entry[4][0] for entry in socket.getaddrinfo(hostname, None)}
    except socket.gaierror:
        # Let yt-dlp report a useful error for unavailable public hosts.
        return True

    for address in addresses:
        ip = ipaddress.ip_address(address)
        if not ip.is_global:
            return False
    return True


def _validate_url(url: str) -> None:
    hostname = (urlparse(url).hostname or "").lower().rstrip(".")
    if hostname == "youtu.be" or hostname == "youtube.com" or hostname.endswith(".youtube.com"):
        raise HTTPException(
            status_code=400,
            detail="YouTube downloads are unavailable because YouTube's Terms do not allow this type of download.",
        )
    if len(url) > MAX_URL_LENGTH or not _is_public_url(url):
        raise HTTPException(status_code=400, detail="Enter a public HTTP or HTTPS video URL.")


def _ydl_options() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "socket_timeout": 20,
        "retries": 2,
        "extractor_retries": 2,
    }


def _extract_info(url: str) -> dict[str, Any]:
    with yt_dlp.YoutubeDL(_ydl_options()) as ydl:
        return ydl.extract_info(url, download=False)


def _content_length(headers: Any) -> int | None:
    content_range = headers.get("Content-Range")
    if content_range:
        match = re.search(r"/(\d+)$", content_range)
        if match:
            return int(match.group(1))
    content_length = headers.get("Content-Length")
    return int(content_length) if content_length and content_length.isdigit() else None


def _remote_format_size(item: dict[str, Any]) -> int | None:
    """Request a single byte to read a media server's declared file size."""
    media_url = item.get("url")
    if not media_url or not _is_public_url(media_url):
        return None
    headers = {"User-Agent": "Mozilla/5.0"}
    headers.update(item.get("http_headers") or {})
    headers["Range"] = "bytes=0-0"
    try:
        request = Request(media_url, headers=headers)
        with urlopen(request, timeout=4) as response:
            return _content_length(response.headers)
    except Exception:
        return None


def _format_size(item: dict[str, Any], duration: float | int | None) -> tuple[int | None, bool]:
    """Return a format's advertised size, or a bitrate-based estimate."""
    size = item.get("filesize")
    if size:
        return int(size), False
    size = item.get("filesize_approx")
    if size:
        return int(size), True
    bitrate = item.get("tbr")
    if duration and bitrate:
        return int(float(duration) * float(bitrate) * 125), True
    return None, False


def _resolved_format_size(item: dict[str, Any], duration: float | int | None) -> tuple[int | None, bool]:
    size, is_estimate = _format_size(item, duration)
    if size is not None:
        return size, is_estimate
    size = _remote_format_size(item)
    return (size, False) if size is not None else (None, False)


def _public_formats(info: dict[str, Any]) -> list[dict[str, Any]]:
    formats: list[dict[str, Any]] = []
    duration = info.get("duration")
    audio_formats = [
        item
        for item in info.get("formats", [])
        if item.get("vcodec") in {None, "none"} and item.get("acodec") not in {None, "none"}
    ]
    best_audio = max(audio_formats, key=lambda item: item.get("abr") or item.get("tbr") or 0, default=None)
    best_audio_size, best_audio_is_estimate = _resolved_format_size(best_audio, duration) if best_audio else (None, False)

    for item in info.get("formats", []):
        if item.get("vcodec") in {None, "none"}:
            continue
        format_id = item.get("format_id")
        if not format_id:
            continue
        width, height = item.get("width"), item.get("height")
        if not width or not height:
            continue
        has_audio = item.get("acodec") not in {None, "none"}
        formats.append(
            {
                "id": str(format_id),
                "label": item.get("format_note") or f"{height}p",
                "width": width,
                "height": height,
                "fps": item.get("fps"),
                "ext": item.get("ext") or "video",
                "has_audio": has_audio,
                "orientation": "portrait" if height > width else "landscape",
                "source": item,
            }
        )

    # Show one choice per stream size, preferring a format that includes audio.
    preferred: dict[tuple[int, int, int | None], dict[str, Any]] = {}
    for item in formats:
        key = (item["width"], item["height"], item["fps"])
        existing = preferred.get(key)
        if existing is None or (item["has_audio"] and not existing["has_audio"]):
            preferred[key] = item
    public_formats: list[dict[str, Any]] = []
    for item in preferred.values():
        size, size_is_estimate = _resolved_format_size(item["source"], duration)
        if not item["has_audio"] and size is not None and best_audio_size:
            size += best_audio_size
            size_is_estimate = size_is_estimate or best_audio_is_estimate
        if size and size > MAX_FILE_BYTES:
            continue
        item["filesize"] = size
        item["filesize_estimated"] = size_is_estimate
        item.pop("source")
        public_formats.append(item)
    return sorted(public_formats, key=lambda item: (item["height"], item["fps"] or 0), reverse=True)


def _inspect(url: str) -> dict[str, Any]:
    info = _extract_info(url)
    if info.get("_type") == "playlist":
        raise ValueError("Playlists are not supported. Paste a single video URL.")
    formats = _public_formats(info)
    if not formats:
        raise ValueError("No downloadable video formats were found for this URL.")
    return {
        "title": info.get("title") or "Untitled video",
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration"),
        "formats": formats,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/inspect")
async def inspect_video(request: VideoRequest) -> dict[str, Any]:
    url = str(request.url)
    _validate_url(url)
    try:
        return await asyncio.to_thread(_inspect, url)
    except HTTPException:
        raise
    except Exception as error:
        logger.exception("Video inspection failed")
        raise HTTPException(status_code=422, detail="Could not inspect this video. It may be unavailable or unsupported.") from error


def _download(url: str, format_id: str, directory: str, progress_hook: Any | None = None) -> tuple[Path, str]:
    info = _extract_info(url)
    available_ids = {str(item.get("format_id")) for item in info.get("formats", [])}
    if format_id not in available_ids:
        raise ValueError("This format is no longer available. Inspect the video again.")

    options = {
        **_ydl_options(),
        "format": f"{format_id}+bestaudio/{format_id}",
        "outtmpl": str(Path(directory) / "video.%(ext)s"),
        "merge_output_format": "mp4",
        "max_filesize": MAX_FILE_BYTES,
        "restrictfilenames": True,
    }
    if progress_hook:
        options["progress_hooks"] = [progress_hook]
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.download([url])

    files = [file for file in Path(directory).iterdir() if file.suffix.lower() in VIDEO_EXTENSIONS]
    if not files:
        raise ValueError("The video could not be prepared for download.")
    file = max(files, key=lambda item: item.stat().st_size)
    if file.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("This video exceeds the 500 MB limit.")
    title = SAFE_FILENAME.sub("", info.get("title") or "video").strip(" .") or "video"
    return file, f"{title[:120]}{file.suffix.lower()}"


def _job_payload(job: DownloadJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "state": job.state,
        "progress": round(job.progress, 1),
        "downloaded_bytes": job.downloaded_bytes,
        "total_bytes": job.total_bytes,
        "speed": job.speed,
        "eta": job.eta,
        "error": job.error,
    }


def _update_job(job_id: str, **values: Any) -> None:
    with jobs_lock:
        job = jobs.get(job_id)
        if job:
            for key, value in values.items():
                setattr(job, key, value)


def _remove_job(job_id: str, directory: str) -> None:
    with jobs_lock:
        jobs.pop(job_id, None)
    shutil.rmtree(directory, ignore_errors=True)


async def _run_download_job(job_id: str, url: str, format_id: str, directory: str) -> None:
    def progress_hook(data: dict[str, Any]) -> None:
        status = data.get("status")
        if status == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            progress = (downloaded / total * 100) if total else 0
            _update_job(
                job_id,
                state="downloading",
                progress=min(progress, 99.9),
                downloaded_bytes=downloaded,
                total_bytes=total,
                speed=data.get("speed"),
                eta=data.get("eta"),
            )
        elif status == "finished":
            _update_job(job_id, state="processing", progress=100, eta=None)

    try:
        _update_job(job_id, state="preparing")
        file, filename = await asyncio.to_thread(_download, url, format_id, directory, progress_hook)
    except Exception:
        logger.exception("Background video download failed")
        _update_job(job_id, state="failed", error="Could not download this format. Please try another one.")
        await asyncio.sleep(JOB_TTL_SECONDS)
        _remove_job(job_id, directory)
        return

    _update_job(job_id, state="ready", progress=100, file=file, filename=filename, eta=None)
    await asyncio.sleep(JOB_TTL_SECONDS)
    with jobs_lock:
        should_expire = job_id in jobs and jobs[job_id].state == "ready"
    if should_expire:
        _remove_job(job_id, directory)


@app.post("/api/jobs")
async def create_download_job(request: DownloadRequest) -> dict[str, Any]:
    url = str(request.url)
    _validate_url(url)
    directory = tempfile.mkdtemp(prefix="video-download-")
    job = DownloadJob(id=uuid4().hex, directory=directory)
    with jobs_lock:
        jobs[job.id] = job
    asyncio.create_task(_run_download_job(job.id, url, request.format_id, directory))
    return _job_payload(job)


@app.get("/api/jobs/{job_id}")
async def get_download_job(job_id: str) -> dict[str, Any]:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="This download has expired. Start it again.")
        return _job_payload(job)


@app.get("/api/jobs/{job_id}/file")
async def get_download_file(job_id: str, background_tasks: BackgroundTasks) -> FileResponse:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="This download has expired. Start it again.")
        if job.state != "ready" or not job.file or not job.filename:
            raise HTTPException(status_code=409, detail="This download is not ready yet.")
        file, filename, directory = job.file, job.filename, job.directory

    background_tasks.add_task(_remove_job, job_id, directory)
    return FileResponse(file, media_type="application/octet-stream", filename=filename, background=background_tasks)


@app.get("/api/download")
async def download_video(url: HttpUrl, format_id: str, background_tasks: BackgroundTasks) -> FileResponse:
    url = str(url)
    _validate_url(url)
    directory = tempfile.mkdtemp(prefix="video-download-")
    try:
        file, filename = await asyncio.to_thread(_download, url, format_id, directory)
    except Exception as error:
        shutil.rmtree(directory, ignore_errors=True)
        logger.exception("Video download failed")
        raise HTTPException(status_code=422, detail="Could not download this format. Please choose another one.") from error

    background_tasks.add_task(shutil.rmtree, directory, True)
    return FileResponse(file, media_type="application/octet-stream", filename=filename, background=background_tasks)
