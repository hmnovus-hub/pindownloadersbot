from __future__ import annotations

import asyncio
import base64
import hmac
import logging
import os
import subprocess
from datetime import date, datetime, time
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

from aiohttp import web
from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaVideo, Message

TARGET = date(2027, 2, 1)
SEND_AT = time(7)
MOSCOW = ZoneInfo("Europe/Moscow")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
ASSETS = Path("/etc/secrets/countdown-videos.b64")
VIDEO_COUNT = 2


def day_word(days: int) -> str:
    if 11 <= days % 100 <= 14:
        return "дней"
    return {1: "день", 2: "дня", 3: "дня", 4: "дня"}.get(days % 10, "дней")


def render_assets(assets: Path, directory: Path, today: date) -> list[Path]:
    sources = assets.read_bytes().splitlines()
    if len(sources) != VIDEO_COUNT:
        raise ValueError("Требуются два исходных ролика")

    days = max(0, (TARGET - today).days)
    title = "ФЕВРАЛЬ НАСТУПИЛ" if days == 0 else "ДО 1 ФЕВРАЛЯ"
    counter = f"{days} {day_word(days)}"
    overlay = ",".join([
        f"drawtext=fontfile='{FONT}':text='{title}':fontsize=h*0.024:y=h*0.771:x=(w-tw)/2:fontcolor=0xDBDDE8",
        f"drawtext=fontfile='{FONT}':text='{counter}':fontsize=h*0.069:y=h*0.81:x=(w-tw)/2:fontcolor=white",
    ])
    videos = []
    for index, source in enumerate(sources, start=1):
        template = directory / f"template-{index}.mp4"
        output = directory / f"countdown-{index}.mp4"
        template.write_bytes(base64.b64decode(source, validate=True))
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(template),
            "-map", "0:v:0", "-map", "0:a:0", "-filter_threads", "1",
            "-vf", overlay, "-c:v", "libx264", "-preset", "ultrafast", "-threads", "1",
            "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "copy",
            "-movflags", "+faststart", str(output),
        ], check=True, timeout=120)
        videos.append(output)
    return videos


class Countdown:
    def __init__(self, bot: Bot, chat_id: int, secret: str, assets: Path) -> None:
        self.bot = bot
        self.chat_id = chat_id
        self.secret = secret
        self.assets = assets
        self.lock = asyncio.Lock()
        self.last_sent: date | None = None

    @classmethod
    def from_env(cls, bot: Bot) -> Countdown | None:
        chat_id = os.getenv("COUNTDOWN_CHAT_ID", "")
        secret = os.getenv("COUNTDOWN_TRIGGER_SECRET", "")
        if not chat_id and not secret:
            return None
        if not chat_id.isascii() or not chat_id.isdecimal() or not secret or not ASSETS.is_file():
            logging.warning("Countdown configuration incomplete; media downloading remains available")
            return None
        return cls(bot, int(chat_id), secret, ASSETS)

    async def send(self, today: date) -> None:
        with TemporaryDirectory(prefix="countdown-") as temp:
            paths = await asyncio.to_thread(render_assets, self.assets, Path(temp), today)
            days = max(0, (TARGET - today).days)
            caption = f"До 1 февраля — {days} {day_word(days)}." if days else "Февраль наступил ❄️"
            await self.bot.send_media_group(
                chat_id=self.chat_id,
                media=[
                    InputMediaVideo(
                        media=FSInputFile(path),
                        caption=caption if index == 0 else None,
                        supports_streaming=True,
                    )
                    for index, path in enumerate(paths)
                ],
            )

    async def preview(self, message: Message) -> None:
        if message.chat.type != "private" or message.chat.id != self.chat_id:
            return
        try:
            async with self.lock:
                await self.send(datetime.now(MOSCOW).date())
        except Exception:
            logging.exception("Countdown preview failed")
            await message.answer("Не получилось собрать отсчёт. Попробуй чуть позже.")

    async def daily(self, request: web.Request) -> web.Response:
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {self.secret}".encode()):
            return web.Response(status=403)
        async with self.lock:
            now = datetime.now(MOSCOW)
            if now.date() > TARGET or self.last_sent == now.date():
                return web.Response(status=204)
            if now.time() < SEND_AT:
                return web.Response(status=409)
            try:
                await self.send(now.date())
            except Exception:
                logging.exception("Daily countdown failed")
                return web.Response(status=503)
            self.last_sent = now.date()
            logging.info("Both countdown videos sent for %s", self.last_sent)
            return web.Response(status=204)
