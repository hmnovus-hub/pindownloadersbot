# Telegram-бот для скачивания Pinterest

Бот принимает ссылки Pinterest, Instagram и публичные Snapchat Spotlight, скачивает фото/видео и отправляет их в Telegram. Карусели Instagram отправляются целиком, группами до 10 файлов. Telegram-обновления принимает через защищённый webhook; HTTP `/health` подходит для Render.

Команда `/stats` показывает общее число пользователей, активность за 24 часа и 7 дней, запросы, успешность, количество отправленных файлов и разбивку по платформам. Данные живут только в памяти и обнуляются после сна или перезапуска сервиса; ссылки и тексты сообщений не сохраняются.

## Конфигурация

1. Откройте [@BotFather](https://t.me/BotFather), выполните `/newbot` и скопируйте токен.
2. Создайте конфигурацию:

   ```bash
   cp .env.example .env
   ```

3. Вставьте токен в `.env` вместо примера.
4. Укажите публичный HTTPS-адрес в `WEBHOOK_BASE_URL`. На Render адрес определяется автоматически.

### Docker локально

```bash
docker compose up -d --build
docker compose logs -f
```

### Без Docker

Нужны Python 3.11+ и `ffmpeg`:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```

## Ограничения

- Закрытые, удалённые и требующие авторизации пины недоступны.
- По умолчанию бот не отправляет файлы больше 49 МБ; лимит меняется через `MAX_FILE_SIZE_MB`.
- Pinterest периодически меняет разметку. Обновляйте `yt-dlp`, если скачивание перестало работать: `pip install -U yt-dlp` или пересоберите Docker-образ.
- Скачивайте только материалы, на использование которых у вас есть права.
- Snapchat поддерживается для публичных ссылок вида `snapchat.com/spotlight/...`.

## Instagram и cookies

Instagram часто не отдаёт публикации без входа. Войдите в Instagram в Comet и добавьте в `.env`:

```env
INSTAGRAM_COOKIES_FROM_BROWSER=comet
```

На macOS может потребоваться разрешить VS Code доступ к профилю Comet и Связке ключей. Альтернатива — экспортировать cookies Instagram в Netscape-формате и указать путь через `INSTAGRAM_COOKIES_FILE`. Не публикуйте `.env` и файл cookies: они дают доступ к текущей сессии Instagram.

## Проверка

```bash
pip install pytest
pytest -q
```

## Render + GitHub

Пошаговая инструкция находится в `RENDER_DEPLOY.md`. Репозиторий содержит готовый `render.yaml`; секреты задаются только в Render Dashboard. Там же описана настройка бесплатного внешнего монитора, который обращается к `/health` и не даёт сервису засыпать.
