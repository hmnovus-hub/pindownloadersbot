from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import urlparse


INSTAGRAM_HOSTS = {"instagram.com", "instagr.am"}
MEDIA_EXTENSIONS = {
    ".jpg": "photo",
    ".jpeg": "photo",
    ".png": "photo",
    ".webp": "photo",
    ".mp4": "video",
    ".mov": "video",
    ".m4v": "video",
    ".webm": "video",
}
URL_RE = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)


class InstagramDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class InstagramMedia:
    path: Path
    media_type: str
    description: str | None = None


def _read_description(directory: Path) -> str | None:
    for metadata_path in sorted(directory.rglob("*.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        description = metadata.get("description")
        if isinstance(description, str) and description.strip():
            return description.strip()
    return None


def _normalized_host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().rstrip(".").removeprefix("www.")


def is_instagram_url(url: str) -> bool:
    parsed = urlparse(url)
    host = _normalized_host(url)
    valid_path = re.match(r"^/(?:p|reel|reels)/[A-Za-z0-9_-]+/?", parsed.path)
    return (
        parsed.scheme in {"http", "https"}
        and any(host == allowed or host.endswith("." + allowed) for allowed in INSTAGRAM_HOSTS)
        and valid_path is not None
    )


def find_instagram_url(text: str) -> str | None:
    for candidate in URL_RE.findall(text):
        cleaned = candidate.rstrip(".,!?;:)'\"]}")
        if is_instagram_url(cleaned):
            return cleaned
    return None


class InstagramDownloader:
    def __init__(
        self,
        max_file_size_mb: int = 49,
        cookies_file: str | None = None,
        cookies_from_browser: str | None = None,
    ) -> None:
        self.max_bytes = max_file_size_mb * 1024 * 1024
        self.cookies_file = cookies_file.strip() if cookies_file else None
        self.cookies_from_browser = (
            cookies_from_browser.strip().lower() if cookies_from_browser else None
        )

    def _command(self, url: str, directory: Path) -> list[str]:
        command = [
            sys.executable,
            "-m",
            "gallery_dl",
            "--destination",
            str(directory),
            "--filename",
            "{num:>02}_{id}.{extension}",
            "--filesize-max",
            str(self.max_bytes),
            "--option",
            "extractor.instagram.videos=merged",
            "--option",
            "extractor.instagram.order-files=asc",
            "--write-metadata",
        ]
        if self.cookies_file:
            cookie_path = Path(os.path.expandvars(os.path.expanduser(self.cookies_file)))
            if not cookie_path.is_file():
                raise InstagramDownloadError(
                    "Файл Instagram cookies не найден. Проверь переменную "
                    "INSTAGRAM_COOKIES_FILE."
                )
            command.extend(["--cookies", str(cookie_path)])
        elif self.cookies_from_browser == "comet":
            cookie_path = directory / ".comet_instagram_cookies.txt"
            self._export_comet_cookies(cookie_path)
            command.extend(["--cookies", str(cookie_path)])
        elif self.cookies_from_browser:
            command.extend(["--cookies-from-browser", self.cookies_from_browser])
        command.append(url)
        return command

    @staticmethod
    def _export_comet_cookies(target: Path) -> None:
        if sys.platform != "darwin":
            raise InstagramDownloadError(
                "Автоматическое чтение Comet сейчас настроено только для macOS."
            )
        try:
            from gallery_dl import cookies as gallery_cookies

            original_settings = gallery_cookies._chromium_browser_settings

            def comet_settings(browser_name: str) -> dict:
                if browser_name == "comet":
                    return {
                        "browser": "comet",
                        "directory": str(
                            Path.home() / "Library/Application Support/Comet"
                        ),
                        "keyring": "Comet",
                        "profiles": True,
                    }
                return original_settings(browser_name)

            gallery_cookies.SUPPORTED_BROWSERS_CHROMIUM.add("comet")
            gallery_cookies.SUPPORTED_BROWSERS.add("comet")
            gallery_cookies._chromium_browser_settings = comet_settings
            try:
                source = gallery_cookies.load_cookies(
                    ("comet", None, None, None, ".instagram.com")
                )
            finally:
                gallery_cookies._chromium_browser_settings = original_settings
                gallery_cookies.SUPPORTED_BROWSERS_CHROMIUM.discard("comet")
                gallery_cookies.SUPPORTED_BROWSERS.discard("comet")

            exported = MozillaCookieJar(str(target))
            for cookie in source:
                exported.set_cookie(cookie)
            if not list(exported):
                raise InstagramDownloadError(
                    "В Comet не найдена активная сессия Instagram. Сначала войди в Instagram."
                )
            exported.save(ignore_discard=True, ignore_expires=True)
        except InstagramDownloadError:
            raise
        except Exception as exc:
            raise InstagramDownloadError(
                "Не удалось прочитать cookies Comet. Закрой Comet и разреши доступ "
                "к Связке ключей, затем попробуй снова."
            ) from exc

    def download(self, url: str, directory: Path) -> list[InstagramMedia]:
        if not is_instagram_url(url):
            raise InstagramDownloadError("Пришли ссылку на пост или Reel из Instagram.")
        try:
            result = subprocess.run(
                self._command(url, directory),
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise InstagramDownloadError("Instagram слишком долго не отвечает.") from exc

        files = [
            path
            for path in directory.rglob("*")
            if path.is_file() and path.suffix.lower() in MEDIA_EXTENSIONS
        ]
        files.sort(key=lambda path: (path.name, str(path)))
        oversized = [path for path in files if path.stat().st_size > self.max_bytes]
        files = [path for path in files if path not in oversized]
        if not files:
            error = (result.stderr or result.stdout or "").lower()
            if any(word in error for word in ("login", "cookie", "401", "unauthorized")):
                raise InstagramDownloadError(
                    "Instagram требует вход. Войди в Instagram в Comet и укажи "
                    "INSTAGRAM_COOKIES_FROM_BROWSER=comet в .env."
                )
            if oversized:
                raise InstagramDownloadError("Все файлы в публикации слишком большие.")
            raise InstagramDownloadError(
                "Не удалось получить публикацию. Возможно, она закрытая, удалена или требует входа."
            )

        description = _read_description(directory)
        return [
            InstagramMedia(
                path=path,
                media_type=MEDIA_EXTENSIONS[path.suffix.lower()],
                description=description,
            )
            for path in files
        ]
