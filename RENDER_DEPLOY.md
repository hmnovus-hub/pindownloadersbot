# Размещение бота на Render через GitHub

## 1. GitHub

В репозиторий загружаются исходники, `Dockerfile`, `render.yaml` и `.env.example`. Никогда не загружайте `.env`, каталог `secrets` или `instagram_cookies.txt`.

Перед отправкой проверьте:

```bash
git status
git check-ignore .env
```

Команда `git check-ignore .env` должна вывести `.env`.

## 2. Создание сервиса Render

1. В Render нажмите **New → Blueprint**.
2. Подключите GitHub и выберите репозиторий бота.
3. Render прочитает `render.yaml` и создаст бесплатный Docker Web Service.
4. Когда Render попросит значение `BOT_TOKEN`, вставьте токен BotFather.
5. Нажмите **Apply** и дождитесь окончания сборки.

Публичный адрес определяется через `RENDER_EXTERNAL_HOSTNAME`, поэтому вручную задавать `WEBHOOK_BASE_URL` не требуется. После запуска бот зарегистрирует адрес вида `https://<service>.onrender.com/telegram/webhook` в Telegram.

## 3. Instagram cookies

На Mac в папке проекта выполните:

```bash
source .venv/bin/activate
python export_instagram_cookies.py
```

Откройте созданный файл `secrets/instagram_cookies.txt`. В Render откройте сервис:

1. **Environment → Secret Files → Add Secret File**.
2. Filename: `instagram_cookies.txt`.
3. В Contents вставьте содержимое локального файла.
4. Сохраните с повторным деплоем.

Render предоставит файл по пути `/etc/secrets/instagram_cookies.txt`; этот путь уже указан в `render.yaml`.

## 4. Проверка

Откройте в браузере:

```text
https://<service>.onrender.com/health
```

Ожидаемый ответ:

```json
{"status": "ok"}
```

В Render Logs должна появиться строка `Webhook registered`. После этого отправьте боту `/start`.

## Ограничения бесплатного Render

- Сервис засыпает после 15 минут без входящих HTTP-запросов. Telegram повторяет webhook-запросы после неуспешной доставки, но первое сообщение после сна может обрабатываться с задержкой.
- Статистика `/stats` хранится только в памяти и сбрасывается после сна, перезапуска или нового деплоя.
- Временные фото и видео удаляются сразу после отправки пользователю.
- Если Instagram перестал скачиваться, повторно экспортируйте cookies и обновите Secret File.

