# 🎮 Dota 2 Meta Bot

Telegram-бот, который показывает топ-10 героев по позиции и рангу на основе актуальных данных OpenDota API.

## 🚀 Деплой на Railway

### 1. Создай бота в Telegram
1. Открой [@BotFather](https://t.me/BotFather) в Telegram
2. Отправь `/newbot`
3. Придумай имя и username
4. Скопируй **токен** (выглядит как `123456:ABC-DEF1234...`)

### 2. Залей код на GitHub
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/ТВО_ИМЯЮ/dota2bot.git
git push -u origin main
```

### 3. Задеплой на Railway
1. Зайди на [railway.app](https://railway.app)
2. Нажми **New Project → Deploy from GitHub repo**
3. Выбери свой репозиторий
4. Зайди в **Variables** и добавь:
   ```
   TELEGRAM_BOT_TOKEN = твой_токен_от_BotFather
   ```
5. Railway сам запустит бота через `Procfile`

### 4. Готово! ✅
Напиши своему боту `/start` в Telegram.

---

## 📁 Структура проекта

```
dota2bot/
├── bot.py            # Основной файл бота (handlers, UI)
├── dotabuff.py       # Модуль получения данных с OpenDota API
├── requirements.txt  # Зависимости Python
├── Procfile          # Команда запуска для Railway
├── railway.toml      # Конфигурация Railway
└── .gitignore
```

## 🤖 Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Приветствие + выбор позиции |
| `/meta`  | Открыть выбор позиции |
| `/help`  | Инструкция по использованию |

## 📊 Данные

- Источник: **OpenDota API** (бесплатный, официальный)
- Кэш: **30 минут** (чтобы не спамить API)
- Обновление: автоматически при следующем запросе после истечения кэша

## ⚙️ Локальный запуск (для разработки)

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="твой_токен"
python bot.py
```
