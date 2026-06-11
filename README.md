# FM Compare — сравнение версий финансовых моделей (веб-версия)

**Версия:** 0.3.0 · **Язык интерфейса:** русский · **Платформа:** веб-сервис (Docker / Linux) · **Доступ:** по корпоративной сети.

Инструмент для аудита и анализа изменений между версиями финансовой Excel-модели. Веб-сервис: пользователь открывает ссылку в браузере, загружает файлы, проверяет автоопределённые KPI и получает AI-резюме на странице плюс полный Excel-отчёт.

Сравнение выполняется на сервере; внешние вызовы LLM идут **только через корпоративный AI-gateway**. Прямые обращения к публичным LLM-провайдерам не используются.

---

## Возможности

- Сравнение **двух или трёх файлов** Excel (V1 vs V2, опционально + V3) по выбранным листам.
- **Сравнение листов внутри одного файла** — выбор конкретных пар листов одного файла.
- Два режима глубины анализа:
  - **Full audit trail** — полный аудит: значения, формулы, скрытые строки, комментарии, сдвиги периодов.
  - **Quick KPI Check** — быстрая проверка только ключевых KPI.
- **Двухфазная валидация KPI** — пользователь подтверждает/правит найденные адреса ячеек и единицы измерения перед запуском (редактируемая таблица в браузере).
- **Дашборд KPI** — сравнение числовых показателей с Δ (абс.) и Δ% по всем версиям.
- **AI-резюме** — управленческое резюме генерируется через корпоративный gateway. При недоступности gateway — rule-based fallback (graceful degradation).
- **AI-чат на дашборде** — вопросно-ответный чат по данным финансовой модели в реальном времени (SSE-стриминг).
- **Анализ чувствительности** — пересчёт KPI при изменении входных параметров (требует LibreOffice).
- **Иерархия статей и сверка** — автоматическое построение иерархии по кодам статей и cross-sheet check.
- Полный **Excel-отчёт** на скачивание: Executive Summary, KPI Comparison, Top Changes, Business Diff, Formula Changes, Timing Shifts, Warnings, Run Settings.
- Настраиваемый порог материальности (абсолютный и/или процентный).
- Редактируемый бизнес-словарь KPI и листов.
- **Безопасный логгер** — в логи не попадают значения ячеек, формулы, пароли, токены.

---

## Архитектура

```
Браузер ──HTTP──► FastAPI (fm_compare/web) ──► core/ (движок, openpyxl)
                          │
                          ├──► core/llm ──► AI-gateway (chat, summary)
                          └──► core/recalc ──► LibreOffice headless (sensitivity)
```

- `fm_compare/core/` — движок сравнения (excel_reader, kpi_extractor, kpi_resolver, engine, hierarchy, cross_check, recalc, sensitivity).
- `fm_compare/web/` — FastAPI: upload / resolve-preview / run / status / summary / report / sensitivity / chat.
- `fm_compare/core/llm/` — gateway-клиент (config, client, token_provider, chat, summary_llm, errors).
- `fm_compare/core/agent/` — LLM-валидатор KPI-резолюций.

---

## Требования

- **Docker** + **Docker Compose** (целевой способ запуска).
- Либо **Python 3.12+** для локального запуска.
- **LibreOffice** (опционально) — для анализа чувствительности (`sudo apt install libreoffice-calc`).

---

## Запуск через Docker (рекомендуется)

1. Скопируйте `.env.example` в `.env` и заполните:

   ```ini
   APP_PASSWORD=<пароль для доступа к сервису>
   GATEWAY_BASE_URL=http://BA-SRV-AI-APP01.mr-group.ru:8080
   KEYCLOAK_USERNAME=<логин>
   KEYCLOAK_PASSWORD=<пароль>
   LLM_MODEL=openai/gpt-5.5
   ```

   `.env` **не коммитится** (в `.gitignore`). Без `APP_PASSWORD` — сервис открыт без авторизации (только dev).

2. Сборка и запуск:

   ```bash
   docker compose up -d --build
   ```

3. Проверка:

   ```bash
   curl http://localhost:8000/healthz   # -> ok
   ```

4. Откройте в браузере `http://<IP-сервера>:8000`, введите `APP_PASSWORD`.

---

## Локальный запуск без Docker

```bash
pip install -r requirements.txt
# Linux / Mac:
APP_PASSWORD=test uvicorn fm_compare.web.app:app --host 0.0.0.0 --port 8000
# Windows PowerShell:
$env:APP_PASSWORD="test"; uvicorn fm_compare.web.app:app --host 0.0.0.0 --port 8000
```

---

## Пошаговая работа в браузере

1. **Загрузка файлов** — выберите V1 (новая версия) и V2 (старая), опционально V3 (базовая). Нажмите «Загрузить».
2. **Выбор листов** — отметьте листы для сравнения, нажмите «Определить KPI».
3. **Проверка KPI** — таблица с найденными адресами ячеек и единицами измерения. Исправьте при необходимости (формат `Лист!E42`).
4. **Параметры и запуск** — режим (Full / Quick), Top-X, пороги материальности, запуск.
5. **Дашборд KPI** — числовые показатели всех версий с Δ и Δ%. AI-чат для вопросов по модели.
6. **Результат** — блоки AI-резюме + счётчики. Кнопка «Скачать отчёт Excel».

---

## API (основные эндпоинты)

| Метод | Путь | Описание |
|-------|------|----------|
| `POST` | `/api/upload` | Загрузка V1, V2, V3 xlsx |
| `POST` | `/api/{id}/resolve-preview` | Автоопределение KPI-адресов |
| `GET` | `/api/{id}/dashboard` | Дашборд сравнения KPI |
| `POST` | `/api/{id}/run` | Запуск полного сравнения |
| `GET` | `/api/{id}/status` | Статус задачи |
| `GET` | `/api/{id}/summary` | AI-резюме |
| `GET` | `/api/{id}/report.xlsx` | Скачать Excel-отчёт |
| `POST` | `/api/{id}/chat` | SSE-стриминг AI-чата |
| `POST` | `/api/{id}/sensitivity` | Анализ чувствительности |
| `GET` | `/healthz` | Health check |

---

## Конфигурация (переменные окружения)

| Переменная | Назначение |
|---|---|
| `APP_PASSWORD` | Пароль доступа к сервису |
| `FM_COMPARE_DATA_DIR` | Каталог данных (логи/загрузки/словарь) |
| `GATEWAY_BASE_URL` | URL корпоративного AI-gateway |
| `KEYCLOAK_TOKEN_URL` | URL Keycloak token endpoint |
| `KEYCLOAK_CLIENT_ID` | Client ID Keycloak |
| `KEYCLOAK_USERNAME` / `KEYCLOAK_PASSWORD` | Учётные данные сервисного аккаунта |
| `LLM_MODEL` | Модель (например `openai/gpt-5.5`) |
| `LLM_TIMEOUT_S` | Таймаут gateway (по умолчанию 120) |

---

## Тесты

```bash
# Все тесты (112 unit + functional)
pytest fm_compare/tests/ -v

# Живой интеграционный тест против задеплоенного сервиса
FM_COMPARE_URL=http://<IP>:8000 FM_COMPARE_PASSWORD=<pwd> \
  python fm_compare/tests/test_live_app.py
```

---

## Безопасность

- Доступ по единому паролю (`APP_PASSWORD`) + session-cookie.
- LLM только через корпоративный gateway — нет прямых URL публичных провайдеров.
- В логи не попадают: значения ячеек, формулы, имена пользователей, bearer-token, пароли.
- Загрузки хранятся временно (по TTL) в `FM_COMPARE_DATA_DIR/uploads/<job_id>/`.
- `.env`, `*.xlsx` исключены из git и Docker-образа.
- Защита от path traversal в `job_id` (regex `^[0-9a-f]{32}$`).
- Лимит размера файла: 100 МБ на версию.

---

## Структура проекта

```
fm_compare/
├── core/
│   ├── engine.py             — оркестратор run_compare()
│   ├── excel_reader.py       — openpyxl loader
│   ├── kpi_extractor.py      — поиск KPI по паттернам
│   ├── kpi_resolver.py       — парсинг адресов ячеек
│   ├── hierarchy.py          — иерархия статей по кодам
│   ├── cross_check.py        — сверка между листами
│   ├── recalc.py             — пересчёт через LibreOffice
│   ├── sensitivity.py        — анализ чувствительности
│   ├── value_differ.py / formula_differ.py / timing_detector.py
│   ├── summary_generator.py / report_exporter.py
│   ├── business_dictionary.py / app_settings.py / models.py
│   ├── paths.py              — кросс-платформенный data dir
│   ├── agent/kpi_validator.py — LLM-валидатор KPI
│   └── llm/                  — gateway-клиент (config, client, token_provider, chat, summary_llm)
├── web/
│   ├── app.py                — точка входа (uvicorn fm_compare.web.app:app)
│   ├── routes.py             — все HTTP-маршруты
│   ├── auth.py / jobs.py / storage.py / dashboard.py / serialization.py
│   └── static/               — index.html, login.html, app.js, styles.css
├── security/safe_logger.py
├── tests/
│   ├── test_stage1.py / test_stage2.py / test_stage4.py / test_chat.py
│   ├── test_functional.py    — полный workflow через TestClient
│   └── test_live_app.py      — интеграционные тесты против деплоя
└── data/default_dictionary.json
Dockerfile · docker-compose.yml · requirements.txt · .env.example
```

---

## История изменений

### v0.3.0 (текущая)
- **Трёхверсионное сравнение (V3)** — одновременное сравнение V1 vs V2 и V2 vs V3, отдельный Excel-отчёт.
- **AI-чат на дашборде** — SSE-стриминг через корпоративный gateway; контекст дашборда передаётся в первое сообщение.
- **Анализ чувствительности** — API-эндпоинт с LibreOffice headless.
- **Иерархия статей + cross-sheet check** — код-префикс иерархия, сверка дискрепансий между листами.
- **Функциональные тесты** — 112 unit/functional тестов (pytest) + 35 live integration тестов.
- **Security-фиксы** — path traversal guard, upload size limit (100 MB), SSE escaping, temp file cleanup.
- **LLM-валидатор KPI** — агент проверяет автоматически найденные KPI-адреса.

### v0.2.0
- Полный переход на веб (FastAPI), Docker-деплой, LLM-gateway для AI-резюме, авторизация по паролю.

### v0.1.0
- Десктопная версия (tkinter): сравнение двух файлов, двухфазная валидация KPI, Excel-отчёт.

---

*README обновляется при каждом крупном обновлении приложения.*
