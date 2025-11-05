# Настройка Telegram Mini App

## Шаг 1: Build Mini App

### Локально (для тестирования)

```bash
cd mini_app
npm install
npm run build
```

### На Railway (автоматически)

Railway автоматически выполнит build при deploy через:
```bash
npm run build:miniapp
```

## Шаг 2: Deploy на Railway

1. Убедитесь что код залит на GitHub
2. Подключите Railway к вашему репозиторию
3. Railway автоматически:
   - Установит Python зависимости
   - Установит Node.js зависимости
   - Выполнит build Mini App
   - Запустит Flask сервер

## Шаг 3: Получить URL приложения

После deploy на Railway:

1. Откройте ваш проект на Railway
2. Перейдите в Settings → Domains
3. Скопируйте URL, например: `https://your-app.railway.app`

## Шаг 4: Настроить Menu Button в BotFather

1. Откройте Telegram и найдите @BotFather
2. Отправьте команду `/mybots`
3. Выберите вашего бота из списка
4. Выберите `Bot Settings`
5. Выберите `Menu Button`
6. Выберите `Configure Menu Button` или `Edit Menu Button URL`
7. Введите URL:
   ```
   https://your-app.railway.app/mini-app
   ```
8. Введите текст кнопки (например):
   ```
   Открыть панель
   ```
   или
   ```
   📊 Dashboard
   ```

## Шаг 5: Проверка

1. Откройте вашего бота в Telegram
2. Внизу должна появиться кнопка Menu (☰)
3. Нажмите на неё
4. Должен открыться Mini App с Dashboard

## Альтернативный способ: Inline кнопка

Если хотите открывать Mini App через inline кнопку в сообщении:

```python
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

keyboard = [
    [InlineKeyboardButton(
        "📊 Открыть Dashboard",
        web_app=WebAppInfo(url="https://your-app.railway.app/mini-app")
    )]
]
reply_markup = InlineKeyboardMarkup(keyboard)

await update.message.reply_text(
    "Откройте Mini App:",
    reply_markup=reply_markup
)
```

## Проверка валидации

Mini App использует Telegram WebApp initData для аутентификации.

Убедитесь что:
1. `TELEGRAM_TOKEN` установлен в Railway Environment Variables
2. URL правильный (должен начинаться с `https://`)
3. Mini App открывается **только** через Telegram (не напрямую в браузере)

## Debugging

### Mini App не открывается

1. Проверьте что build создан успешно:
   ```bash
   ls -la mini_app/dist/
   ```

2. Проверьте логи Railway на наличие ошибок

3. Откройте URL в браузере напрямую:
   ```
   https://your-app.railway.app/mini-app
   ```

   Должна загрузиться страница (но API запросы будут падать без Telegram)

### API запросы возвращают 401

1. Проверьте что `TELEGRAM_TOKEN` установлен в Railway
2. Убедитесь что Mini App открыт через Telegram (не в браузере)
3. Проверьте в DevTools что headers содержат `X-Telegram-Init-Data`

### Styling не работает

1. Убедитесь что все CSS файлы скопированы в dist:
   ```bash
   cd mini_app
   npm run build
   ls -la dist/assets/
   ```

2. Проверьте что Vite правильно настроил base path:
   ```typescript
   // vite.config.ts
   base: '/mini-app/'
   ```

## Environment Variables на Railway

Убедитесь что установлены:

```
TELEGRAM_TOKEN=your_bot_token_here
FLASK_SECRET_KEY=your_secret_key_here
DATABASE_URL=(автоматически от Railway)
```

## Дополнительные возможности

### Кастомизация цветов

Приложение автоматически использует цветовую схему Telegram.

Для кастомизации создайте theme в BotFather:
```
/mybots → [Ваш бот] → Bot Settings → Theme
```

### Добавление в главное меню

Пользователи увидят кнопку Menu автоматически после настройки в BotFather.

### MainButton API

Можно использовать Telegram MainButton для действий:

```typescript
webApp.MainButton.setText('Сохранить')
webApp.MainButton.show()
webApp.MainButton.onClick(() => {
  // Действие
})
```

## Полезные ссылки

- [Telegram Mini Apps Documentation](https://core.telegram.org/bots/webapps)
- [Telegram WebApp SDK Reference](https://core.telegram.org/bots/webapps#initializing-mini-apps)
- [BotFather Commands](https://core.telegram.org/bots#botfather)

## Поддержка

Если возникли проблемы:
1. Проверьте логи Railway
2. Проверьте console в DevTools браузера
3. Убедитесь что все зависимости установлены
4. Пересоздайте build: `cd mini_app && npm run build`
