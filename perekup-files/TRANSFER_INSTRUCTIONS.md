# 📦 Инструкция по переносу файлов в репозиторий perekup

## Шаг 1: Откройте терминал

```bash
cd ~/poster-helper-bot/perekup-files
```

## Шаг 2: Клонируйте репозиторий perekup

```bash
cd ~
git clone https://github.com/Muahhidun/perekup.git
cd perekup
```

## Шаг 3: Скопируйте все файлы

```bash
# Копируем все файлы из perekup-files в perekup
cp -r ~/poster-helper-bot/perekup-files/* .
cp ~/poster-helper-bot/perekup-files/.env .
cp ~/poster-helper-bot/perekup-files/.env.example .
cp ~/poster-helper-bot/perekup-files/.gitignore .
```

## Шаг 4: Проверьте что всё скопировалось

```bash
ls -la
```

Должны увидеть:
- main.py
- requirements.txt
- Dockerfile
- README.md
- .env
- .env.example
- .gitignore
- src/
- migrations/

## Шаг 5: Добавьте в git

```bash
git add .
git status
```

## Шаг 6: Сделайте коммит

```bash
git commit -m "Initial commit: Telegram bot for auto resale tracking"
```

## Шаг 7: Запушьте в GitHub

```bash
git push origin main
```

---

## ✅ Готово!

Теперь файлы в репозитории `perekup` и можно деплоить на Railway!

---

## 🚀 Следующие шаги - деплой на Railway

### 1. Создайте новый проект
- Зайдите на [railway.app](https://railway.app)
- Dashboard → New Project

### 2. Добавьте PostgreSQL
- Add Service → Database → PostgreSQL
- Railway автоматически создаст `DATABASE_URL`

### 3. Добавьте бот
- Add Service → GitHub Repo → выберите `Muahhidun/perekup`
- Railway обнаружит Dockerfile и начнет деплой

### 4. Добавьте переменные окружения

В настройках сервиса бота (не PostgreSQL!) добавьте:

```
TELEGRAM_BOT_TOKEN=8524103967:AAG2azpPa8QEUWukqbrxtLw5hThrrQa50Sc
ALLOWED_USER_IDS=167084307,296406246
INITIAL_CAPITAL=2000000
DAILY_REPORT_TIME=22:00
TIMEZONE=Asia/Almaty
USE_WEBHOOK=false
```

**ВАЖНО:** `DATABASE_URL` добавится автоматически из PostgreSQL сервиса!

### 5. Подключите PostgreSQL к боту

В Railway:
- Кликните на сервис бота
- Variables → Add Reference
- Выберите PostgreSQL → DATABASE_URL

### 6. Задеплойте

Railway автоматически задеплоит после добавления переменных.

---

## 🎉 Готово! Бот работает!

Откройте Telegram → @Muwarakabot → /start
