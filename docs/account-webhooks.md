# Account Webhooks

Account callbacks live in `backend/account_webhooks.go` and are independent of
wallet, registration, SMS, and payment protocol code.

## API

All endpoints use the existing SunnyRegister session authentication:

- `GET /api/sunny/webhooks` lists configurations and supported events.
- `POST /api/sunny/webhooks` creates a configuration. Leave `secret` empty to generate one; the generated value is returned once.
- `PUT /api/sunny/webhooks/{id}` updates a configuration; an empty `secret` keeps the existing value.
- `DELETE /api/sunny/webhooks/{id}` removes the configuration and future deliveries.
- `POST /api/sunny/webhooks/{id}/test` sends a signed test event immediately.
- `GET /api/sunny/webhook-deliveries?limit=80` reads the outbox log.
- `POST /api/sunny/webhook-deliveries/{id}/retry` requeues a failed delivery.

Supported events are `account.registered`, `account.updated`,
`account.status_changed`, `account.token_refreshed`, `account.trial_changed`,
`account.subscription_changed`, and `account.payment_changed`.

## Request verification

Each delivery is an HTTP POST with JSON body and these headers:

```text
X-Sunny-Event
X-Sunny-Delivery
X-Sunny-Timestamp
X-Sunny-Signature: sha256=<hex HMAC-SHA256>
```

The signed bytes are `timestamp + "." + rawBody`. Account payloads contain
identifiers and status fields only; credentials, cookies, tokens, PINs, and
session material are excluded.

## Delivery behavior

Events are written to PostgreSQL before delivery. A 2xx response marks a
delivery successful. Other responses and network errors are retried after
5 seconds, 30 seconds, 2 minutes, 10 minutes, and 1 hour (bounded by the
configured maximum attempts). Delivery responses and errors remain queryable.
