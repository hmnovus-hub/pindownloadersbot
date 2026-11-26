from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from dotenv import load_dotenv

from countdown import Countdown
from downloader import (
    PinterestDownloadError,
    PinterestDownloader,
    find_pinterest_url,
    temporary_directory,
)
from instagram_downloader import (
    InstagramDownloadError,
    InstagramDownloader,
    find_instagram_url,
)
from snapchat_downloader import (
    SnapchatDownloadError,
    SnapchatDownloader,
    find_snapchat_url,
)
from stats import StatsStore


router = Router()
downloader: PinterestDownloader
instagram_downloader: InstagramDownloader
snapchat_downloader: SnapchatDownloader
download_slots: asyncio.Semaphore
stats_store: StatsStore
countdown: Countdown | None = None
DESCRIPTION_TTL_SECONDS = 24 * 60 * 60
DESCRIPTION_CACHE_LIMIT = 2000


@dataclass(frozen=True)
class DescriptionEntry:
    chat_id: int
    message_id: int
    owner_id: int
    base_caption: str
    description: str
    created_at: float


description_cache: dict[str, DescriptionEntry] = {}


def _clean_description(description: str | None) -> str | None:
    if not isinstance(description, str):
        return None
    cleaned = description.strip()
    return cleaned or None


def _truncate_utf16(text: str, max_units: int) -> str:
    encoded = text.encode("utf-16-le")
    if len(encoded) // 2 <= max_units:
        return text
    return encoded[: max_units * 2].decode("utf-16-le", errors="ignore").rstrip()


def _description_caption(base_caption: str, description: str) -> str:
    prefix = f"{base_caption}\n\n"
    prefix_units = len(prefix.encode("utf-16-le")) // 2
    available = 1024 - prefix_units
    description_units = len(description.encode("utf-16-le")) // 2
    if description_units > available:
        description = _truncate_utf16(description, max(available - 1, 0)) + "…"
    return prefix + description


def _description_button(description: str | None) -> tuple[str | None, InlineKeyboardMarkup | None]:
    if not _clean_description(description):
        return None, None
    token = secrets.token_hex(12)
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text="📝 Показать описание",
                callback_data=f"description:{token}",
            )
        ]]
    )
    return token, keyboard


def _remember_description(
    token: str | None,
    sent_message: Message,
    owner_id: int,
    base_caption: str,
    description: str | None,
) -> None:
    cleaned = _clean_description(description)
    if not token or not cleaned:
        return

    now = time.monotonic()
    expired = [
        key
        for key, entry in description_cache.items()
        if now - entry.created_at > DESCRIPTION_TTL_SECONDS
    ]
    for key in expired:
        description_cache.pop(key, None)
    while len(description_cache) >= DESCRIPTION_CACHE_LIMIT:
        oldest = next(iter(description_cache))
        description_cache.pop(oldest, None)

    description_cache[token] = DescriptionEntry(
        chat_id=sent_message.chat.id,
        message_id=sent_message.message_id,
        owner_id=owner_id,
        base_caption=base_caption,
        description=cleaned,
        created_at=now,
    )


def user_id_for(message: Message) -> int:
    return message.from_user.id if message.from_user else 0


async def record_stats(
    message: Message, platform: str, success: bool, media_count: int = 0
) -> None:
    try:
        await stats_store.record(
            user_id_for(message), platform, success, media_count
        )
    except Exception:
        logging.exception("Failed to record statistics")


@router.message(CommandStart())
async def start(message: Message) -> None:
    await stats_store.touch_user(user_id_for(message))
    await message.answer(
        "Привет! Пришли ссылку Pinterest, Instagram или Snapchat Spotlight — "
        "я скачаю все фото и видео и отправлю их сюда."
    )


@router.message(Command("help"))
async def help_message(message: Message) -> None:
    await message.answer(
        "Отправь ссылку pinterest.com, pin.it, instagram.com или публичный "
        "snapchat.com/spotlight. Карусели Instagram приходят целиком. "
        "Команда /stats показывает статистику бота."
    )


@router.message(Command("stats"))
async def stats_message(message: Message) -> None:
    await stats_store.touch_user(user_id_for(message))
    summary = await stats_store.summary()
    names = {"pinterest": "Pinterest", "instagram": "Instagram", "snapchat": "Snapchat"}
    platforms = "\n".join(
        f"  • {names.get(name, name.title())}: {count}"
        for name, count in summary.platform_successes.items()
    ) or "  • Пока нет загрузок"
    await message.answer(
        "📊 Статистика бота\n\n"
        f"👥 Пользователей: {summary.total_users}\n"
        f"⚡ Активны за 24 часа: {summary.active_24h}\n"
        f"📅 Активны за 7 дней: {summary.active_7d}\n\n"
        f"📥 Запросов всего: {summary.total_requests}\n"
        f"🕐 Запросов за 24 часа: {summary.requests_24h}\n"
        f"🗓 Запросов за 7 дней: {summary.requests_7d}\n"
        f"✅ Успешность: {summary.success_rate:.1f}%\n"
        f"📦 Отправлено файлов: {summary.media_files}\n\n"
        f"По платформам:\n{platforms}\n\n"
        f"Статистика собирается с {summary.collecting_since}."
    )


async def send_instagram_media(message: Message, media_items: list) -> None:
    caption = "Скачано из Instagram"
    description = next(
        (item.description for item in media_items if item.description),
        None,
    )
    owner_id = user_id_for(message)
    for start_index in range(0, len(media_items), 10):
        chunk = media_items[start_index : start_index + 10]
        token, keyboard = (
            _description_button(description)
            if start_index == 0
            else (None, None)
        )
        if len(chunk) == 1:
            item = chunk[0]
            upload = FSInputFile(item.path)
            if item.media_type == "video":
                sent = await message.answer_video(
                    upload,
                    caption=caption,
                    supports_streaming=True,
                    reply_markup=keyboard,
                )
            else:
                sent = await message.answer_photo(
                    upload,
                    caption=caption,
                    reply_markup=keyboard,
                )
            _remember_description(token, sent, owner_id, caption, description)
            continue

        group = []
        for index, item in enumerate(chunk):
            item_caption = caption if start_index == 0 and index == 0 else None
            upload = FSInputFile(item.path)
            if item.media_type == "video":
                group.append(InputMediaVideo(media=upload, caption=item_caption, supports_streaming=True))
            else:
                group.append(InputMediaPhoto(media=upload, caption=item_caption))
        sent_group = await message.answer_media_group(group)
        if keyboard and sent_group:
            first = sent_group[0]
            try:
                await message.bot.edit_message_reply_markup(
                    chat_id=first.chat.id,
                    message_id=first.message_id,
                    reply_markup=keyboard,
                )
                _remember_description(token, first, owner_id, caption, description)
            except TelegramBadRequest:
                logging.exception("Telegram rejected a description button for media group")


@router.callback_query(F.data.startswith("description:"))
async def show_description(callback: CallbackQuery) -> None:
    token = (callback.data or "").partition(":")[2]
    entry = description_cache.get(token)
    if not entry or time.monotonic() - entry.created_at > DESCRIPTION_TTL_SECONDS:
        description_cache.pop(token, None)
        await callback.answer(
            "Описание больше недоступно. Пришли ссылку ещё раз.",
            show_alert=True,
        )
        return
    if callback.from_user.id != entry.owner_id:
        await callback.answer("Эта кнопка предназначена автору запроса.", show_alert=True)
        return

    try:
        await callback.bot.edit_message_caption(
            chat_id=entry.chat_id,
            message_id=entry.message_id,
            caption=_description_caption(entry.base_caption, entry.description),
            reply_markup=None,
        )
    except TelegramBadRequest:
        logging.exception("Failed to add media description")
        await callback.answer("Не получилось добавить описание.", show_alert=True)
        return

    description_cache.pop(token, None)
    await callback.answer("Описание добавлено")


@router.message(Command("video"))
async def countdown_video(message: Message) -> None:
    if countdown is not None:
        await countdown.preview(message)


@router.message(F.text)
async def handle_link(message: Message) -> None:
    pinterest_url = find_pinterest_url(message.text or "")
    instagram_url = find_instagram_url(message.text or "")
    snapchat_url = find_snapchat_url(message.text or "")
    if not pinterest_url and not instagram_url and not snapchat_url:
        await message.answer("Не вижу ссылки Pinterest, Instagram или Snapchat Spotlight.")
        return

    platform = "instagram" if instagram_url else "snapchat" if snapchat_url else "pinterest"
    media_count = 0

    status = await message.answer("Скачиваю медиа…")
    try:
        async with download_slots:
            with temporary_directory() as temp_dir:
                if instagram_url:
                    media_items = await asyncio.to_thread(
                        instagram_downloader.download, instagram_url, Path(temp_dir)
                    )
                    await send_instagram_media(message, media_items)
                    media_count = len(media_items)
                elif snapchat_url:
                    media = await asyncio.to_thread(
                        snapchat_downloader.download, snapchat_url, Path(temp_dir)
                    )
                    caption = "Скачано из Snapchat"
                    token, keyboard = _description_button(media.description)
                    sent = await message.answer_video(
                        FSInputFile(media.path),
                        caption=caption,
                        supports_streaming=True,
                        reply_markup=keyboard,
                    )
                    _remember_description(
                        token,
                        sent,
                        user_id_for(message),
                        caption,
                        media.description,
                    )
                    media_count = 1
                else:
                    media = await downloader.download(pinterest_url, Path(temp_dir))
                    upload = FSInputFile(media.path)
                    caption = "Скачано с Pinterest"
                    token, keyboard = _description_button(media.title)
                    if media.media_type == "video":
                        sent = await message.answer_video(
                            upload,
                            caption=caption,
                            supports_streaming=True,
                            reply_markup=keyboard,
                        )
                    else:
                        try:
                            sent = await message.answer_photo(
                                upload,
                                caption=caption,
                                reply_markup=keyboard,
                            )
                        except Exception:
                            sent = await message.answer_document(
                                FSInputFile(media.path),
                                caption=caption,
                                reply_markup=keyboard,
                            )
                    _remember_description(
                        token,
                        sent,
                        user_id_for(message),
                        caption,
                        media.title,
                    )
                    media_count = 1
        await record_stats(message, platform, True, media_count)
        await status.delete()
    except (PinterestDownloadError, InstagramDownloadError, SnapchatDownloadError) as exc:
        await record_stats(message, platform, False)
        await status.edit_text(f"Не получилось скачать: {exc}")
    except Exception:
        await record_stats(message, platform, False)
        logging.exception("Unexpected download error")
        await status.edit_text("Произошла ошибка при скачивании. Попробуй другую ссылку чуть позже.")


@router.message()
async def unsupported(message: Message) -> None:
    await message.answer(
        "Пришли ссылку Pinterest, Instagram или Snapchat обычным текстовым сообщением."
    )


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


def main() -> None:
    global downloader, instagram_downloader, snapchat_downloader, download_slots, stats_store
    global countdown
    load_dotenv()
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example в .env и добавьте токен.")

    max_size = int(os.getenv("MAX_FILE_SIZE_MB", "49"))
    concurrency = int(os.getenv("MAX_CONCURRENT_DOWNLOADS", "3"))
    downloader = PinterestDownloader(max_file_size_mb=max_size)
    instagram_downloader = InstagramDownloader(
        max_file_size_mb=max_size,
        cookies_file=os.getenv("INSTAGRAM_COOKIES_FILE"),
        cookies_from_browser=os.getenv("INSTAGRAM_COOKIES_FROM_BROWSER"),
    )
    snapchat_downloader = SnapchatDownloader(max_file_size_mb=max_size)
    stats_store = StatsStore()
    download_slots = asyncio.Semaphore(concurrency)

    bot = Bot(token=token)
    countdown = Countdown.from_env(bot)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    base_url = (os.getenv("WEBHOOK_BASE_URL") or "").strip().rstrip("/")
    if not base_url:
        render_host = (os.getenv("RENDER_EXTERNAL_HOSTNAME") or "").strip()
        if render_host:
            base_url = f"https://{render_host}"
    if not base_url.startswith("https://"):
        raise RuntimeError(
            "WEBHOOK_BASE_URL должен быть HTTPS-адресом Render, например "
            "https://my-bot.onrender.com"
        )

    webhook_path = "/" + os.getenv("WEBHOOK_PATH", "telegram/webhook").strip("/")
    webhook_secret = os.getenv("WEBHOOK_SECRET") or secrets.token_urlsafe(32)

    async def register_webhook(bot: Bot) -> None:
        await bot.set_webhook(
            f"{base_url}{webhook_path}",
            secret_token=webhook_secret,
            allowed_updates=dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        logging.info("Webhook registered at %s%s", base_url, webhook_path)

    dispatcher.startup.register(register_webhook)

    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    if countdown is not None:
        app.router.add_post("/daily", countdown.daily)
    SimpleRequestHandler(
        dispatcher=dispatcher,
        bot=bot,
        handle_in_background=True,
        secret_token=webhook_secret,
    ).register(app, path=webhook_path)
    setup_application(app, dispatcher, bot=bot)

    web.run_app(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    main()
