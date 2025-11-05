# Telegram Mini App для Poster Helper Bot

## Описание

Полноценное веб-приложение внутри Telegram для управления накладными и aliases.

## Функциональность

- **Dashboard** - статистика, графики точности, проблемные товары
- **История поставок** - просмотр всех накладных с фильтрацией и пагинацией
- **Детали поставки** - подробная информация о каждой накладной
- **Управление Aliases** - добавление, редактирование, удаление aliases
- **Адаптивный дизайн** - работает на мобильных и десктопных клиентах
- **Интеграция с Telegram** - использует theme colors, haptic feedback

## Технологии

### Frontend
- React 18
- TypeScript
- Vite
- Tailwind CSS
- React Router DOM
- Chart.js
- Telegram WebApp SDK

### Backend
- Flask (Python)
- PostgreSQL/SQLite
- Telegram WebApp Data Validation

## Структура проекта

```
mini_app/
├── public/
├── src/
│   ├── api/
│   │   └── client.ts          # API клиент для связи с backend
│   ├── components/
│   │   ├── Header.tsx          # Шапка приложения
│   │   ├── StatCard.tsx        # Карточка статистики
│   │   ├── SupplyCard.tsx      # Карточка поставки
│   │   ├── AliasCard.tsx       # Карточка alias
│   │   ├── Chart.tsx           # График
│   │   ├── Loading.tsx         # Индикатор загрузки
│   │   └── ErrorMessage.tsx    # Сообщение об ошибке
│   ├── hooks/
│   │   ├── useTelegram.ts      # Хук для работы с Telegram API
│   │   └── useApi.ts           # Хук для API запросов
│   ├── pages/
│   │   ├── Dashboard.tsx       # Главная страница
│   │   ├── SupplyHistory.tsx   # История поставок
│   │   ├── SupplyDetail.tsx    # Детали поставки
│   │   ├── Aliases.tsx         # Список aliases
│   │   └── AliasForm.tsx       # Форма alias
│   ├── types/
│   │   └── index.ts            # TypeScript типы
│   ├── styles/
│   │   └── index.css           # Глобальные стили
│   ├── App.tsx                 # Главный компонент
│   └── main.tsx                # Точка входа
├── package.json
├── tsconfig.json
├── vite.config.ts
└── tailwind.config.js
```

## Локальная разработка

### Шаг 1: Установка зависимостей

```bash
cd mini_app
npm install
```

### Шаг 2: Настройка окружения

Создайте `.env` файл в `mini_app/`:

```env
VITE_API_URL=http://localhost:5000
```

### Шаг 3: Запуск dev сервера

```bash
npm run dev
```

Frontend будет доступен на `http://localhost:3000`

### Шаг 4: Запуск backend

В другом терминале:

```bash
cd ..
python web_app.py
```

Backend будет доступен на `http://localhost:5000`

## Production Build

### Локально

```bash
cd mini_app
npm run build
```

Build будет создан в `mini_app/dist/`

### На Railway

Railway автоматически запустит build при deploy:

1. Добавьте npm build script в корневой `package.json`
2. Railway выполнит `npm run build:miniapp` перед запуском
3. Flask будет обслуживать статику из `mini_app/dist/`

## Регистрация в Telegram

1. Откройте @BotFather в Telegram
2. Выполните команды:
   ```
   /mybots
   → [Выберите вашего бота]
   → Bot Settings
   → Menu Button
   → Configure Menu Button
   → URL: https://your-app.railway.app/mini-app
   → Button text: Открыть панель
   ```

3. Теперь в боте появится кнопка Menu (☰) внизу
4. Нажмите её чтобы открыть Mini App

## API Endpoints

### Dashboard
- `GET /api/dashboard` - статистика и графики

### Supplies
- `GET /api/supplies?page=1&limit=20` - список поставок с пагинацией
- `GET /api/supplies/:id` - детали поставки

### Aliases
- `GET /api/aliases?search=&source=` - список aliases с фильтрацией
- `POST /api/aliases` - создать alias
- `PUT /api/aliases/:id` - обновить alias
- `DELETE /api/aliases/:id` - удалить alias

### Items
- `GET /api/items/search?q=query&source=ingredient` - поиск товаров для autocomplete

## Безопасность

Все API запросы валидируются через Telegram WebApp initData:

```typescript
const headers = {
  'X-Telegram-Init-Data': webApp.initData
}
```

Backend проверяет подпись через HMAC SHA256.

## Особенности

### Telegram Theme Integration

Приложение автоматически адаптируется к теме Telegram (light/dark):

```typescript
const { themeParams } = useTelegram()

// Используйте цвета из Telegram
<div style={{
  backgroundColor: themeParams.bg_color,
  color: themeParams.text_color
}}>
```

### Haptic Feedback

```typescript
const haptic = useHaptic()

// При нажатии кнопки
haptic.impact('medium')

// При успехе/ошибке
haptic.notification('success')
haptic.notification('error')

// При выборе
haptic.selection()
```

### Back Button

```typescript
webApp.BackButton.show()
webApp.BackButton.onClick(() => {
  navigate(-1)
})
```

## Troubleshooting

### Build не создается

Убедитесь что установлены все зависимости:
```bash
cd mini_app
rm -rf node_modules package-lock.json
npm install
npm run build
```

### API запросы возвращают 401

Проверьте что:
1. `TELEGRAM_TOKEN` установлен в `.env`
2. Приложение открыто через Telegram (не напрямую в браузере)
3. initData корректно передается в headers

### Стили не применяются

Убедитесь что Tailwind CSS правильно настроен:
```bash
# Проверьте что postcss.config.js существует
cat postcss.config.js
```

## TODO

- [ ] Implement real statistics service (currently using mock data)
- [ ] Add supply history to database
- [ ] Add pull-to-refresh
- [ ] Add offline mode with caching
- [ ] Add export to CSV/Excel
- [ ] Add dark mode toggle
- [ ] Add animations/transitions

## Автор

Создано для Poster Helper Bot - Telegram бот для обработки накладных.

## Лицензия

MIT
