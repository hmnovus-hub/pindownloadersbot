from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from imageio_ffmpeg import get_ffmpeg_exe
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
SPOTLIGHT_RE = re.compile(r"^/spotlight/[A-Za-z0-9_-]+/?$")


class SnapchatDownloadError(RuntimeError):
    pass


class _QuietLogger:
    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


@dataclass(frozen=True)
class SnapchatMedia:
    path: Path
    media_type: str = "video"
    description: str | None = None


def is_snapchat_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".").removeprefix("www.")
    return (
        parsed.scheme in {"http", "https"}
        and host == "snapchat.com"
        and SPOTLIGHT_RE.match(parsed.path) is not None
    )


def find_snapchat_url(text: str) -> str | None:
    for candidate in URL_RE.findall(text):
        cleaned = candidate.rstrip(".,!?;:)'\"]}")
        if is_snapchat_url(cleaned):
            return cleaned
    return None


class SnapchatDownloader:
    def __init__(self, max_file_size_mb: int = 49) -> None:
        self.max_bytes = max_file_size_mb * 1024 * 1024

    def download(self, url: str, directory: Path) -> SnapchatMedia:
        if not is_snapchat_url(url):
            raise SnapchatDownloadError("Пришли ссылку на публичный Snapchat Spotlight.")
        options = {
            "outtmpl": str(directory / "snapchat_%(id)s.%(ext)s"),
            "max_filesize": self.max_bytes,
            "ffmpeg_location": shutil.which("ffmpeg") or get_ffmpeg_exe(),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "logger": _QuietLogger(),
            "noplaylist": True,
        }
        try:
            with YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
        except DownloadError as exc:
            raise SnapchatDownloadError(
                "Не удалось скачать Spotlight. Возможно, он удалён или недоступен."
            ) from exc
        files = [path for path in directory.iterdir() if path.is_file()]
        if not files:
            raise SnapchatDownloadError("Snapchat не вернул видеофайл.")
        path = max(files, key=lambda item: item.stat().st_mtime)
        if path.stat().st_size > self.max_bytes:
            raise SnapchatDownloadError("Видео слишком большое для отправки в Telegram.")
        metadata = info if isinstance(info, dict) else {}
        description = metadata.get("description") or metadata.get("title")
        if isinstance(description, str):
            description = description.strip() or None
        else:
            description = None
        return SnapchatMedia(path=path, description=description)
