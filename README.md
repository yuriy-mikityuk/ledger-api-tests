# ledger-api-tests

Учебный сервис «мини-леджер» и API-тесты к нему. Спецификация API и бизнес-правил в [SERVICE.md](SERVICE.md).

## Как поднять

```bash
docker compose up --build
```

Сервис на порту 8000, миграции применяются при старте. Схема: http://localhost:8000/api/schema/, Swagger UI: http://localhost:8000/api/docs/.

Postgres опубликован на порту 5432. Если порт занят: `LEDGER_DB_PORT=5433 docker compose up --build`.

## Проверка, что сервис живой

```bash
./scripts/smoke.sh
```

Создаёт аккаунт, депозит, шлёт подписанный вебхук и проверяет баланс. Нужны `curl`, `openssl`, `python3`.

## Где секрет

Секрет подписи вебхука это переменная `PROVIDER_WEBHOOK_SECRET`. Дефолт в `docker-compose.yml`: `dev-webhook-secret`. Свой задаётся при запуске:

```bash
PROVIDER_WEBHOOK_SECRET=my-secret docker compose up --build
```

Тот же секрет надо передать тестам и `smoke.sh`.

## Логи

```bash
docker compose logs -f api
docker compose logs api | grep money_operation
```

## Сброс базы

Очистить данные, оставив схему:

```bash
docker compose exec api python manage.py flush --noinput
```

Удалить volume целиком и поднять заново:

```bash
docker compose down -v
docker compose up --build
```

## Тесты

Зависимости сервиса и тестов лежат в группах `service` и `tests` файла `pyproject.toml`:

```bash
uv sync
```

Окружение создаётся в `.venv`, либо там, куда указывает `UV_PROJECT_ENVIRONMENT`.

Переменные окружения для тестов:

```bash
export LEDGER_BASE_URL=http://localhost:8000
export LEDGER_DB_DSN=postgres://ledger:ledger@localhost:5432/ledger
export PROVIDER_WEBHOOK_SECRET=dev-webhook-secret
```
