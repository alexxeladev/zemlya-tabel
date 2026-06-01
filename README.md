# zemlya-tabel

Система учёта рабочего времени для девелоперской группы «Земля МО».

## Запуск backend локально

### 1. База данных (PostgreSQL через Docker)

```bash
cd backend
docker compose -f docker-compose.dev.yml up -d
```

### 2. Установка зависимостей

```bash
cd backend
pip install -e ".[dev]"
```

### 3. Настройка окружения

```bash
cp .env.example .env
# Заполнить SECRET_KEY (остальные значения из .env.example уже рабочие для docker-compose.dev.yml)
```

### 4. Миграции

```bash
cd backend
alembic upgrade head
```

### 5. Создание первого администратора

```bash
cd backend
python -m app.cli create-admin --email admin@example.com --password changeme --full-name "Admin"
```

### 6. Запуск сервера

```bash
cd backend
uvicorn app.main:app --reload
```

API доступен на http://localhost:8000  
Документация: http://localhost:8000/docs

### 7. Тесты

```bash
cd backend
pytest
# или
pytest -v --tb=short
```
