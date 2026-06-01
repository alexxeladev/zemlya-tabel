# zemlya-tabel

Система учёта рабочего времени для девелоперской группы «Земля МО».

## Запуск backend локально

### 1. Установка зависимостей

```bash
cd backend
pip install -e ".[dev]"
```

### 2. Настройка окружения

```bash
cp .env.example .env
# Заполнить DATABASE_URL и SECRET_KEY в .env
```

### 3. Миграции

```bash
alembic upgrade head
```

### 4. Запуск сервера

```bash
uvicorn app.main:app --reload
```

API доступен на http://localhost:8000  
Документация: http://localhost:8000/docs

### 5. Тесты

```bash
pytest
```
