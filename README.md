# Система автоматизации извещений об изменении


[![CI](https://github.com/sokolser/diplom/actions/workflows/ci.yml/badge.svg)](https://github.com/sokolser/diplom/actions/workflows/ci.yml)
![Coverage](https://img.shields.io/badge/coverage-%3E%3D70%25-brightgreen)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

Веб-сервис для анализа двух версий PDF конструкторской документации, формирования текстового блока извещения об изменении и выгрузки извещения в PDF.

Состав проекта:
- `backend/` — FastAPI API, парсер PDF, сравнение версий, генерация текста извещения и PDF-документа
- `frontend/` — React/Vite интерфейс для загрузки PDF, редактирования блоков и скачивания результата
- `tests/` — pytest-тесты для agent/masking/API
- `.github/workflows/ci.yml` — автопрогон тестов и сборка фронта при push/pull request

## Пошаговая инструкция для запуска проекта

### 1. Клонируйте удалённый репозиторий на локальный компьютер:
```bash
git clone https://github.com/sokolser/diplom.git
cd diplom
```

### 2.Настройте переменные окружения

Скопировать пример:

```bash
cp .env.example .env
```

Затем заполнить переменные окружения:

```env
# Backend 
GIGACHAT_CLIENT_ID= Ваш client id
GIGACHAT_CLIENT_SECRET= Ваш client secret
GIGACHAT_SCOPE=GIGACHAT_API_PERS
GIGACHAT_MODEL= модель GigaChat
GIGACHAT_VERIFY_SSL_CERTS=true
GIGACHAT_CA_BUNDLE_FILE=/app/certs/сертификат Минцифр
GIGACHAT_TIMEOUT=60

GIGACHAT_OAUTH_URL=https://ngw.devices.sberbank.ru:9443/api/v2/oauth
GIGACHAT_BASE_URL=https://gigachat.devices.sberbank.ru/api

# ГОСТовский шрифт
# Положите TTF-шрифт в backend/fonts/GOST_A.TTF.
# В Docker backend видит эту папку как /app/fonts.

CHANGE_NOTICE_PDF_FONT_REGULAR=/app/fonts/GOST_A.TTF
CHANGE_NOTICE_PDF_FONT_BOLD=/app/fonts/GOST_A.TTF

# Frontend
VITE_API_BASE_URL=http://localhost:8000
```
### 3 Запуск программы
### 3.1 Быстрый запуск через Docker
В трминале в корневой папке проекта прописать команду запуска, предварительно открыв Docker Desktop
```bash
docker compose up --build
```

После успешного запуска открыть приложение:

```text
http://localhost:5173
```

Backend API будет доступен здесь:

```text
http://localhost:8000
```

### 3.2 Локальный запуск без Docker

Запуск Backend:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cd backend
uvicorn main:app --reload
```

Запуск Frontend в отдельном терминале:

```bash
cd frontend
npm ci
npm run dev
```

Открыть:

```text
http://localhost:5173
```

## Тесты

Запуск backend-тестов:

```bash
pytest --cov=backend --cov-report=term-missing --cov-report=xml
```

Порог покрытия задан в `.coveragerc`:

```text
fail_under = 70
```

Текущий набор тестов закрывает три критичных слоя:

- `agent` — fallback-генерация извещения без внешних GigaChat-ключей
- `masking` — безопасная маскировка секретов в диагностике
- `API` — `/health`, валидация PDF-загрузок, `/api/v1/generate-change-notice-json`

## CI/CD

GitHub Actions запускается при `push` и `pull_request`:

1. установка Python-зависимостей;
2. запуск `pytest` с coverage gate `>=70%`;
3. выгрузка `coverage.xml` как artifact;
4. установка Node.js-зависимостей;
5. сборка frontend через `npm run build`.


## Лицензия

Проект распространяется по лицензии MIT. Текст лицензии находится в файле `LICENSE`.