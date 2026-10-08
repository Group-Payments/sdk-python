# group-payments

Клиент Group Pay API для Python и консольная утилита `gp`. Python 3.10+, одна зависимость: `httpx`.

## Установка

```bash
pip install group-payments
```

## Быстрый старт

```python
from group_payments import Client

gp = Client("gp_test_...", base_url="https://<адрес API>/api/v1")
payment = gp.payments.create(amount="100.00", description="Заказ 42", order_id="o-42", expires_in_minutes=30)
print(payment["paymentUrl"])

for p in gp.paginate("/payments", limit=50):
    print(p["id"], p["status"])
```

`AsyncClient` повторяет тот же API: `await gp.payments.get(...)`, `async for ... in gp.paginate(...)`, `await gp.aclose()`.

- Именованные аргументы уходят в API в camelCase: `order_id` → `orderId`.
- Каждый POST получает `Idempotency-Key`, общий для всех повторов. Свой ключ: `idempotency_key=`.
- До 3 попыток с экспоненциальной паузой, `Retry-After` учитывается.
- Ошибки типизированы (`NotFoundError`, `RateLimitError`, ...) и содержат `status`, `code`, `request_id`, `param`, `doc_url`.
- Адрес API обязателен: аргумент `base_url=` или переменная `GP_BASE_URL`.

## Вебхуки

```python
from group_payments import webhook

event = webhook.verify(raw_body, request_headers, secret)
```

Передавайте исходное тело запроса, а не пересобранный JSON. При неверной подписи выбрасывается `webhook.WebhookError`.

## Утилита gp

```bash
gp login --key gp_test_... --base-url https://<адрес API>/api/v1
gp trigger payment.succeeded
gp listen --forward-to http://localhost:3000/hook
gp events resend EVENT_ID
```

Ключ хранится в `$GP_CONFIG` или `~/.config/gp/config.json`.

## Лицензия

MIT
