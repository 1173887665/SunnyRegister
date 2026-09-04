# MoeMail Integration

SunnyRegister keeps the existing domain-mail UI and exposes MoeMail only through the backend adapter.

## Configuration

Set `provider` to `moemail` in the domain mailbox configuration and provide:

- `moemail_api_url`: MoeMail deployment URL
- `moemail_api_key`: server-side OpenAPI key
- `domains`: the existing three domains, one per line
- `pickup_base_url`: the public SunnyRegister URL
- `moemail_webhook_secret`: optional shared secret for the webhook endpoint

The equivalent environment variables are `MOEMAIL_API_URL`, `MOEMAIL_API_KEY`, and `MOEMAIL_WEBHOOK_SECRET`. The API key is never returned by the configuration endpoint or sent to the frontend.

## Endpoints

- `POST /api/sunny/domain-mail/check` validates the MoeMail API and confirms every configured domain is enabled upstream.
- `POST /api/sunny/domain-mail/generate` creates a MoeMail mailbox and a local pickup credential.
- `GET /api/sunny/domain-mail/pickup?email=...&token=...` reads the matching MoeMail mailbox through the backend.
- `POST /api/sunny/domain-mail/webhook` accepts `new_message` callbacks and updates the local latest-mail cache.
- `DELETE /api/sunny/mailboxes/{id}` deletes the remote MoeMail mailbox before deleting the local row.

MoeMail's Cloudflare Email Routing and `email-receiver-worker` remain deployment-side responsibilities. Configure catch-all routing for each of the three domains to that worker, then use the connection check above before enabling registration or rebinding.
