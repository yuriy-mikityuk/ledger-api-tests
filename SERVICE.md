# Mini-ledger: спецификация API

Базовый префикс: `/api/v1/`. Схема OpenAPI: `/api/schema/`, Swagger UI: `/api/docs/`.

Авторизация: `Authorization: Token <token>`. Без токена работают только `POST /accounts/` и `POST /webhooks/provider/`.

## Суммы и точность

Все суммы в запросах и ответах это JSON-строки. JSON-число в поле суммы даёт 400. Внутри сервиса только `Decimal`, `float` запрещён.

| Валюта | Знаков после запятой | Шаг | Минимум операции | Максимум операции | Формат в ответе |
|---|---|---|---|---|---|
| USD | 2 | 0.01 | 10.00 | 100000.00 | `"10.00"` |
| EUR | 2 | 0.01 | 10.00 | 100000.00 | `"10.00"` |
| USDT | 6 | 0.000001 | 10.000000 | 100000.000000 | `"10.000000"` |

Округление везде `ROUND_HALF_UP`. На входе округления нет: больше знаков, чем допускает валюта, даёт 400 (`"10.100"` для USD тоже 400, считается запись, а не значение).

Допустимый формат строки: `-?[0-9]+(\.[0-9]+)?`. Экспонента, пробелы, `NaN`, `Infinity` и не-ASCII цифры дают 400.

## Коды ошибок

Формат DRF по умолчанию: `{"amount": ["..."]}` для ошибок по полям, `{"detail": "..."}` для остальных.

| Код | Когда |
|---|---|
| 400 | Ошибка валидации, нет `Idempotency-Key`, недостаточно средств |
| 401 | Нет или неверный токен, неверная подпись вебхука |
| 403 | Чужой кошелёк, чужая котировка, чужой аккаунт |
| 404 | Объект не найден, неизвестный `provider_reference` |
| 409 | Конфликт состояния: котировка истекла или использована, вебхук к транзакции не в статусе `pending`, тип события не подходит транзакции |
| 422 | Тот же `Idempotency-Key` с другим запросом |

## Идемпотентность

Заголовок `Idempotency-Key` обязателен для `POST /deposits/`, `/withdrawals/`, `/exchanges/`. Нет заголовка, пустой или длиннее 255 символов: 400 с ошибкой по полю `Idempotency-Key`.

Порядок: токен (401), заголовок (400), поиск ключа, затем сама операция.

- Ключ уникален в пределах аккаунта, у разных аккаунтов ключи независимы.
- Отпечаток запроса: `sha256(метод + путь + сырое тело)`. Тело сравнивается побайтово, другой порядок полей в JSON считается другим телом.
- Тот же ключ и тот же запрос: тот же статус и побайтово то же тело, новой транзакции нет.
- Тот же ключ и другой запрос (другое тело или другой эндпоинт): 422.
- Сохраняется любой ответ, полученный после авторизации, включая 400, 403, 404, 409. Повтор с тем же ключом вернёт сохранённую ошибку, даже если условия изменились. Для новой попытки нужен новый ключ.
- Запись ключа и движение денег коммитятся в одной транзакции базы. Параллельный повтор ждёт завершения первого запроса.

У обмена две транзакции и один ключ, поэтому `idempotency_key` записан только на ноге `exchange_out`, у `exchange_in` он `NULL`.

## Эндпоинты

### POST /accounts/

Без авторизации. Тело: `{"email": "a@example.com"}`.

201:

```json
{
  "id": 1,
  "email": "a@example.com",
  "token": "9944b09199c62bcf9418ad846dd0e4bbdfc6ee4b",
  "created_at": "2026-11-09T12:00:00.000000Z",
  "wallets": [
    {"id": 1, "currency": "USD", "balance": "0.00"},
    {"id": 2, "currency": "EUR", "balance": "0.00"},
    {"id": 3, "currency": "USDT", "balance": "0.000000"}
  ]
}
```

Ошибки: 400 по полю `email` (невалидный или уже занят). Сравнение email регистрозависимое.

### DELETE /accounts/{id}/

Тестовая поддержка. Работает только при `TEST_SUPPORT_ENABLED=1`, иначе всегда 404, даже без токена.

Требует токен этого же аккаунта. Удаляет аккаунт, токен, кошельки, транзакции, котировки, записи идемпотентности и вебхук-события по транзакциям аккаунта.

204. Ошибки: 401, 403 (чужой аккаунт), 404.

### GET /wallets/

200: список `{"id", "currency", "balance"}` текущего аккаунта. Ошибки: 401.

### POST /deposits/

Заголовок `Idempotency-Key`. Тело: `{"wallet_id": 1, "amount": "100.00"}`.

201, транзакция `pending`, баланс не меняется до вебхука:

```json
{
  "id": 10,
  "wallet_id": 1,
  "type": "deposit",
  "amount": "100.00",
  "fee": "0.00",
  "status": "pending",
  "provider_reference": "2f6c1c1e-3a0e-4f0b-9f57-2f6f0a3f1c11",
  "created_at": "2026-11-09T12:00:00.000000Z",
  "updated_at": "2026-11-09T12:00:00.000000Z"
}
```

Ошибки: 400 (`amount`, `wallet_id`, ключ), 401, 403, 404, 422.

Лимиты включительные, таблица выше.

### POST /withdrawals/

Заголовок `Idempotency-Key`. Тело: `{"wallet_id": 1, "amount": "100.00", "address": "..."}`.

201, транзакция `pending`, формат как у депозита, `provider_reference` заполнен. `amount + fee` списывается с баланса сразу, в одной транзакции базы с `select_for_update` на кошельке.

Комиссия:

| Валюта | Комиссия |
|---|---|
| USD, EUR | `max(ROUND_HALF_UP(amount * 0.005, 2 знака), 1.00)` |
| USDT | `1.000000` фиксированно |

Лимиты как у депозита, применяются к `amount` без комиссии.

`address`: непустая строка (пробелы по краям обрезаются), для USDT начинается с `T` и длина ровно 34. Адрес проверяется, но не хранится и в ответе не возвращается: в модели `Transaction` такого поля нет.

Ошибки: 400 по `amount` или `address`, 401, 403, 404, 422. При нехватке средств 400 по полю `amount`, текст содержит доступную сумму:

```json
{"amount": ["Insufficient funds: available 101.00 USD, required 101.01 including fee 1.00."]}
```

### POST /quotes/

Заголовок идемпотентности не нужен. Тело: `{"from_wallet_id": 1, "to_wallet_id": 2, "amount": "100.00"}`.

201:

```json
{
  "id": 5,
  "from_wallet_id": 1,
  "to_wallet_id": 2,
  "amount_from": "100.00",
  "amount_to": "91.54",
  "rate": "0.9154000000",
  "spread": "0.005",
  "expires_at": "2026-11-09T12:00:30.000000Z",
  "used": false
}
```

Расчёт: `rate = ROUND_HALF_UP(base_rate * (1 - 0.005), 10 знаков)`, `amount_to = ROUND_HALF_UP(amount * rate, точность валюты получения)`. `expires_at = now + 30 секунд`.

Базовые курсы по умолчанию (переопределяются `EXCHANGE_RATES`):

| Пара | Курс |
|---|---|
| USD -> EUR | 0.92 |
| EUR -> USD | 1.08 |
| USD -> USDT | 1.00 |
| USDT -> USD | 1.00 |
| EUR -> USDT | 1.08 |
| USDT -> EUR | 0.92 |

`amount`: больше нуля, точность валюты списания. Лимитов депозита здесь нет. Если `amount_to` округляется до нуля: 400 по `amount`.

Ошибки: 400 (одинаковые валюты по полю `to_wallet_id`, `amount`), 401, 403, 404.

### POST /exchanges/

Заголовок `Idempotency-Key`. Тело: `{"quote_id": 5}`.

201, обе ноги в одной транзакции базы, с блокировкой обоих кошельков в порядке их `id`:

```json
{
  "quote_id": 5,
  "exchange_out": {"id": 11, "wallet_id": 1, "type": "exchange_out", "amount": "100.00", "fee": "0.00", "status": "completed", "provider_reference": null, "created_at": "...", "updated_at": "..."},
  "exchange_in": {"id": 12, "wallet_id": 2, "type": "exchange_in", "amount": "91.54", "fee": "0.00", "status": "completed", "provider_reference": null, "created_at": "...", "updated_at": "..."}
}
```

Порядок проверок: 404 котировка, 403 чужая, 409 использована, 409 истекла, 400 недостаточно средств (`non_field_errors`, текст содержит доступную сумму). Комиссии нет, доход платформы заложен в `rate`.

Ошибки: 400, 401, 403, 404, 409, 422.

### GET /transactions/?wallet_id=

`wallet_id` необязателен, без него отдаются все транзакции аккаунта. Новые первыми (`created_at`, затем `id`). Пагинации нет.

200: список транзакций. Ошибки: 400 (`wallet_id` не число), 401, 403, 404.

### POST /webhooks/provider/

Без токена, с заголовком `X-Provider-Signature: sha256=<hex>`, где `hex` это HMAC-SHA256 от сырого тела запроса с секретом `PROVIDER_WEBHOOK_SECRET`.

Тело:

```json
{
  "event_id": "evt-1",
  "event": "deposit.completed",
  "provider_reference": "2f6c1c1e-3a0e-4f0b-9f57-2f6f0a3f1c11",
  "amount": "100.00"
}
```

`event`: `deposit.completed`, `withdrawal.completed`, `withdrawal.failed`. 200: `{"status": "ok"}`.

Порядок обработки:

1. Подпись неверна или её нет: 401, состояние не меняется.
2. Невалидный JSON или поля: 400.
3. `event_id` уже обработан: 200, изменений нет.
4. `provider_reference` неизвестен (в том числе не UUID): 404, `event_id` не сохраняется.
5. Тип события не подходит транзакции: 409, `event_id` не сохраняется.
6. Транзакция не `pending`: 409, `event_id` не сохраняется.
7. Применение события, `event_id` сохраняется.

| Событие | Эффект |
|---|---|
| `deposit.completed`, `amount` равен сумме транзакции | `completed`, баланс увеличивается на `amount` |
| `deposit.completed`, `amount` не равен | `failed`, баланс не меняется, ответ 200 |
| `withdrawal.completed` | `completed`, баланс уже списан |
| `withdrawal.failed` | `failed`, `amount + fee` возвращаются на баланс |

Суммы сравниваются как `Decimal`: `"100"` равно `"100.00"`. У `withdrawal.*` поле `amount` обязательно по формату, но с транзакцией не сверяется.

Пример подписи на Python. Подписывать надо ровно те байты, которые уйдут в запрос, поэтому `data=`, а не `json=`:

```python
import hashlib
import hmac
import json

import requests

secret = "dev-webhook-secret"
body = json.dumps(
    {
        "event_id": "evt-1",
        "event": "deposit.completed",
        "provider_reference": "2f6c1c1e-3a0e-4f0b-9f57-2f6f0a3f1c11",
        "amount": "100.00",
    }
).encode()
signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

requests.post(
    "http://localhost:8000/api/v1/webhooks/provider/",
    data=body,
    headers={"X-Provider-Signature": signature, "Content-Type": "application/json"},
)
```

## Модели

| Модель | Поля |
|---|---|
| Account | `email` (unique), `created_at`, токен DRF `auth_token` |
| Wallet | `account`, `currency`, `balance` Decimal(20,6), unique (`account`, `currency`) |
| Transaction | `account`, `wallet`, `type`, `amount` Decimal(20,6), `fee` Decimal(20,6), `status`, `provider_reference` (uuid, unique, nullable), `idempotency_key` (nullable, unique вместе с `account`), `created_at`, `updated_at` |
| Quote | `from_wallet`, `to_wallet`, `amount_from`, `amount_to`, `rate` Decimal(20,10), `spread` Decimal(6,4), `expires_at`, `used` |
| WebhookEvent | `event_id` (unique), `payload`, `received_at` |
| IdempotencyRecord | `account`, `key`, `request_hash`, `response_status`, `response_body` (текст), unique (`account`, `key`) |

В базе суммы всех валют хранятся с шестью знаками: USD `10.00` лежит как `10.000000`. Форматирование по валюте только в ответе API.

## Настройки

| Переменная | Значение |
|---|---|
| `DATABASE_URL` | DSN базы |
| `PROVIDER_WEBHOOK_SECRET` | Секрет подписи вебхука, без него сервис не стартует |
| `TEST_SUPPORT_ENABLED` | `1` включает `DELETE /accounts/{id}/` |
| `EXCHANGE_RATES` | JSON вида `{"USD_EUR": "0.92", ...}`, иначе дефолт из кода |

## Логи

Каждая денежная операция пишет в stdout одну строку после коммита транзакции базы:

```
money_operation op=withdrawal tx_id=42 wallet_id=7 currency=USD amount=100.00 fee=1.00 status=pending
```

`op`: `deposit`, `withdrawal`, `exchange_out`, `exchange_in` или имя события вебхука.

## Решения там, где постановка молчала

- `Transaction.account` добавлен для уникальности `idempotency_key` в пределах аккаунта.
- `idempotency_key` хранится только на `exchange_out`.
- `DELETE /accounts/{id}/` требует токен этого аккаунта.
- Идемпотентность сохраняет и ошибочные ответы, тело сравнивается побайтово.
- `address` не хранится.
- Вебхук в не-`pending` транзакцию или не того типа даёт 409.
- `amount` у `withdrawal.*` не сверяется.
- Нечисловой `provider_reference` считается неизвестным, 404.
- Лимитов суммы у обмена нет, кроме `amount > 0`, точности валюты и вместимости колонки.
- Нехватка средств при обмене приходит в `non_field_errors`.
- `GET /transactions/` без `wallet_id` отдаёт всё по аккаунту.
