from __future__ import annotations

import asyncio
import html
import ipaddress
import json
import mimetypes
import re
import socket
import ssl
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
import certifi
from bs4 import BeautifulSoup
from imageio_ffmpeg import get_ffmpeg_exe
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError


PINTEREST_HOSTS = {"pinterest.com", "pin.it"}
MEDIA_HOST_SUFFIX = ".pinimg.com"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
)


def _ffmpeg_executable() -> str:
    return shutil.which("ffmpeg") or get_ffmpeg_exe()


class PinterestDownloadError(RuntimeError):
    pass


class _QuietYtDlpLogger:
    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


@dataclass(frozen=True)
class DownloadedMedia:
    path: Path
    media_type: str
    title: str | None = None


def _normalized_host(url: str) -> str:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host.removeprefix("www.")


def is_pinterest_url(url: str) -> bool:
    parsed = urlparse(url)
    host = _normalized_host(url)
    return parsed.scheme in {"http", "https"} and any(
        host == allowed or host.endswith("." + allowed) for allowed in PINTEREST_HOSTS
    )


def _is_pinimg_url(url: str) -> bool:
    parsed = urlparse(url)
    host = _normalized_host(url)
    return parsed.scheme == "https" and (host == "pinimg.com" or host.endswith(MEDIA_HOST_SUFFIX))


async def _reject_private_dns(url: str) -> None:
    host = urlparse(url).hostname
    if not host:
        raise PinterestDownloadError("Некорректный адрес файла.")
    loop = asyncio.get_running_loop()
    try:
        records = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise PinterestDownloadError("Не удалось найти сервер Pinterest.") from exc
    for record in records:
        address = ipaddress.ip_address(record[4][0])
        if not address.is_global:
            raise PinterestDownloadError("Небезопасный адрес файла отклонён.")


def _extract_meta(html_text: str) -> tuple[str | None, str | None, str | None]:
    soup = BeautifulSoup(html_text, "html.parser")

    def content(*keys: str) -> str | None:
        for key in keys:
            tag = soup.find("meta", attrs={"property": key}) or soup.find(
                "meta", attrs={"name": key}
            )
            if tag and tag.get("content"):
                return html.unescape(str(tag["content"]))
        return None

    video = content("og:video:secure_url", "og:video", "twitter:player:stream")
    image = content("og:image:secure_url", "og:image", "twitter:image")
    title = content("og:description", "og:title", "twitter:title")
    return video, image, title


def _media_type_for_file(path: Path, info: dict | None = None) -> str:
    if path.suffix.lower() in VIDEO_EXTENSIONS:
        return "video"
    if info and info.get("vcodec") not in {None, "none"}:
        return "video"
    return "photo"


def _video_candidates(data: object) -> list[dict]:
    candidates: list[dict] = []
    if isinstance(data, dict):
        video_list = data.get("video_list")
        if isinstance(video_list, dict):
            candidates.extend(
                item
                for item in video_list.values()
                if isinstance(item, dict) and isinstance(item.get("url"), str)
            )
        for value in data.values():
            candidates.extend(_video_candidates(value))
    elif isinstance(data, list):
        for value in data:
            candidates.extend(_video_candidates(value))
    return candidates


def _best_video_url(data: object) -> str | None:
    unique: dict[str, dict] = {}
    for item in _video_candidates(data):
        unique[item["url"]] = item
    if not unique:
        return None

    def quality(item: dict) -> tuple[int, int]:
        url = item["url"]
        direct_mp4 = int(urlparse(url).path.lower().endswith(".mp4"))
        pixels = int(item.get("width") or 0) * int(item.get("height") or 0)
        return direct_mp4, pixels

    return max(unique.values(), key=quality)["url"]


def _download_video_url(
    media_url: str, directory: Path, max_bytes: int, title: str | None
) -> DownloadedMedia:
    options = {
        "outtmpl": str(directory / "pinterest_video.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "ffmpeg_location": _ffmpeg_executable(),
        "max_filesize": max_bytes,
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietYtDlpLogger(),
        "http_headers": {"User-Agent": USER_AGENT},
    }
    before = set(directory.iterdir())
    try:
        with YoutubeDL(options) as ydl:
            ydl.extract_info(media_url, download=True)
    except DownloadError as exc:
        raise PinterestDownloadError("Не удалось скачать видеопоток Pinterest.") from exc
    created = [p for p in directory.iterdir() if p not in before and p.is_file()]
    if not created:
        raise PinterestDownloadError("Pinterest не вернул видеофайл.")
    path = max(created, key=lambda p: p.stat().st_mtime)
    if path.stat().st_size > max_bytes:
        raise PinterestDownloadError("Файл слишком большой для отправки в Telegram.")
    return DownloadedMedia(path=path, media_type="video", title=title)


def _download_with_ytdlp(url: str, directory: Path, max_bytes: int) -> DownloadedMedia:
    output = str(directory / "pinterest_%(id)s.%(ext)s")
    options = {
        "outtmpl": output,
        "format": "bestvideo[ext=mp4]+bestaudio/best[ext=mp4]/best",
        "merge_output_format": "mp4",
        "ffmpeg_location": _ffmpeg_executable(),
        "max_filesize": max_bytes,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "logger": _QuietYtDlpLogger(),
        "http_headers": {"User-Agent": USER_AGENT},
        "restrictfilenames": True,
    }
    before = set(directory.iterdir())
    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except DownloadError as exc:
        raise PinterestDownloadError("yt-dlp не смог извлечь медиа.") from exc

    created = [p for p in directory.iterdir() if p not in before and p.is_file()]
    if not created:
        raise PinterestDownloadError("Pinterest не вернул медиафайл.")
    path = max(created, key=lambda p: p.stat().st_mtime)
    media_type = _media_type_for_file(path, info)
    if path.stat().st_size > max_bytes:
        raise PinterestDownloadError("Файл слишком большой для отправки в Telegram.")
    return DownloadedMedia(path=path, media_type=media_type, title=(info or {}).get("title"))


async def _metadata_fallback(
    url: str, directory: Path, max_bytes: int
) -> DownloadedMedia:
    timeout = aiohttp.ClientTimeout(total=45, connect=10, sock_read=30)
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"}
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    connector = aiohttp.TCPConnector(ssl=ssl_context)
    async with aiohttp.ClientSession(
        timeout=timeout, headers=headers, connector=connector
    ) as session:
        async with session.get(url, allow_redirects=True, max_redirects=5) as response:
            if not is_pinterest_url(str(response.url)):
                raise PinterestDownloadError("Ссылка перенаправила за пределы Pinterest.")
            response.raise_for_status()
            page = await response.text(errors="replace")

        pin_match = re.search(r"/pin/(?:[\w-]+--)?(\d+)", urlparse(url).path)
        if pin_match:
            pin_id = pin_match.group(1)
            api_data = json.dumps(
                {
                    "options": {
                        "field_set_key": "unauth_react_main_pin",
                        "id": pin_id,
                    }
                },
                separators=(",", ":"),
            )
            try:
                async with session.get(
                    "https://www.pinterest.com/resource/PinResource/get/",
                    params={"data": api_data},
                    headers={"X-Pinterest-PWS-Handler": "www/[username].js"},
                ) as api_response:
                    api_response.raise_for_status()
                    payload = await api_response.json(content_type=None)
                pin_data = payload.get("resource_response", {}).get("data", {})
                video_url = _best_video_url(pin_data)
                if video_url and _is_pinimg_url(video_url):
                    await _reject_private_dns(video_url)
                    title = pin_data.get("title") or pin_data.get("grid_title")
                    return await asyncio.to_thread(
                        _download_video_url,
                        video_url,
                        directory,
                        max_bytes,
                        title,
                    )
            except (aiohttp.ClientError, ValueError, TypeError):
                pass

        video_url, image_url, title = _extract_meta(page)
        media_url = video_url or image_url
        media_type = "video" if video_url else "photo"
        if not media_url or not _is_pinimg_url(media_url):
            raise PinterestDownloadError("Не удалось найти фото или видео в этом пине.")

        await _reject_private_dns(media_url)
        async with session.get(media_url, allow_redirects=False) as response:
            response.raise_for_status()
            declared = int(response.headers.get("Content-Length", "0") or 0)
            if declared > max_bytes:
                raise PinterestDownloadError("Файл слишком большой для отправки в Telegram.")
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0]
            extension = mimetypes.guess_extension(content_type) or (".mp4" if video_url else ".jpg")
            if extension == ".jpe":
                extension = ".jpg"
            path = directory / f"pinterest{extension}"
            size = 0
            with path.open("wb") as target:
                async for chunk in response.content.iter_chunked(256 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise PinterestDownloadError("Файл слишком большой для отправки в Telegram.")
                    target.write(chunk)
        return DownloadedMedia(path=path, media_type=media_type, title=title)


class PinterestDownloader:
    def __init__(self, max_file_size_mb: int = 49) -> None:
        self.max_bytes = max_file_size_mb * 1024 * 1024

    async def download(self, url: str, directory: Path) -> DownloadedMedia:
        if not is_pinterest_url(url):
            raise PinterestDownloadError("Пришлите ссылку с pinterest.com или pin.it.")
        try:
            return await asyncio.to_thread(_download_with_ytdlp, url, directory, self.max_bytes)
        except PinterestDownloadError:
            return await _metadata_fallback(url, directory, self.max_bytes)


URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


def find_pinterest_url(text: str) -> str | None:
    for candidate in URL_RE.findall(text):
        cleaned = candidate.rstrip(".,!?;:)'\"]}")
        if is_pinterest_url(cleaned):
            return cleaned
    return None


def temporary_directory() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(prefix="pinterest_bot_")
