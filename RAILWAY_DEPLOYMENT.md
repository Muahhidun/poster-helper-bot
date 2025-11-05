# Railway Deployment Guide

## Проблема: Application failed to respond

Если видите ошибку "Application failed to respond", это означает что веб-сервер не запустился.

### Решение

Проект теперь использует `start_server.py` который запускает:
1. Telegram бота (в фоновом потоке)
2. Flask веб-сервер (в главном потоке)

## Procfile

```
web: python start_server.py
```

**Важно**: Используйте `web:` а не `worker:` - Railway требует web процесс для HTTP запросов.

## Как это работает

`start_server.py`:
- Запускает `bot.py` как subprocess
- Запускает Flask из `web_app.py` на порту из `$PORT`
- Flask обслуживает:
  - Mini App статику: `/mini-app`
  - API endpoints: `/api/*`
  - Веб-интерфейс aliases: `/aliases`

## Проверка deployment

1. **Логи Railway должны показать:**
   ```
   🚀 Starting Poster Helper Bot + Web App
   🤖 Starting Telegram bot...
   🌐 Starting Flask web app...
   * Running on http://0.0.0.0:XXXX
   ```

2. **Откройте URL приложения:**
   ```
   https://your-app.railway.app/
   ```

   Должен редиректить на `/aliases`

3. **Проверьте Mini App:**
   ```
   https://your-app.railway.app/mini-app
   ```

   Должна загрузиться React приложение

## Environment Variables

Убедитесь что установлены на Railway:

```
TELEGRAM_TOKEN=your_bot_token
DATABASE_URL=(автоматически от Railway PostgreSQL)
FLASK_SECRET_KEY=your_secret_key
PORT=(автоматически от Railway)
```

## Build Process

Railway автоматически выполнит:
1. `pip install -r requirements.txt`
2. `npm run build:miniapp` (если определено в package.json)
3. Создаст `mini_app/dist/`

Если `mini_app/dist/` не создается, проверьте логи build:
```
cd mini_app && npm install && npm run build
```

## Troubleshooting

### Bot запускается, но веб-сервер нет

Проверьте что Procfile содержит:
```
web: python start_server.py
```

### Mini App возвращает 404

1. Проверьте что `mini_app/dist/` создан:
   ```bash
   ls -la mini_app/dist/
   ```

2. Выполните build вручную:
   ```bash
   cd mini_app
   npm install
   npm run build
   ```

3. Проверьте Railway logs на наличие ошибок при build

### API возвращает 401 Unauthorized

1. Убедитесь что `TELEGRAM_TOKEN` установлен
2. Проверьте что Mini App открыт через Telegram (не напрямую в браузере)
3. В development mode валидация отключена если `TELEGRAM_TOKEN` пуст

## Локальное тестирование

```bash
# Terminal 1: Start combined server
python start_server.py

# Terminal 2: Check if it's running
curl http://localhost:5000/
curl http://localhost:5000/mini-app
```

## Health Checks

Railway автоматически проверяет здоровье через HTTP запросы к root (`/`).

Flask должен отвечать на порту `$PORT` который Railway предоставляет.

## Дополнительные ресурсы

- [Railway Docs](https://docs.railway.app)
- [Telegram Mini Apps](https://core.telegram.org/bots/webapps)
- [Flask Deployment](https://flask.palletsprojects.com/en/2.3.x/deploying/)
