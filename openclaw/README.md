# OpenClaw Wiring Notes

This project supports two modes:

1. Use an external OpenClaw framework against the FastAPI endpoints.
2. Run the included local worker service (`openclaw/service.py`).

## Which OpenClaw

Use either:

- External framework (example repository often referenced as `openclaw/openclaw`), or
- Included local worker service in this repo.

## Connection points

- Sync files from watched folder:
  - `POST /api/openclaw/sync-folder`
- Ask question from chat connector:
  - `POST /api/openclaw/ask`
- Handshake/auth check:
  - `GET /api/openclaw/handshake`

## Required headers

If `OPENCLAW_SHARED_SECRET` is set in `.env`, send:

- `x-openclaw-secret: <same-secret>`
- If RBAC is enabled, send role header (default): `x-user-role: admin`

## Local worker service

Start watcher:

```bash
cd /Users/cpandirlapali/chaithanya-docs
source .venv311/bin/activate
python openclaw/service.py \
  --base-url http://127.0.0.1:8000 \
  --secret "<OPENCLAW_SHARED_SECRET>" \
  --watch-dir "/Users/cpandirlapali/chaithanya-docs/clinical_policies"
```

One-shot ask:

```bash
python openclaw/service.py \
  --base-url http://127.0.0.1:8000 \
  --secret "<OPENCLAW_SHARED_SECRET>" \
  --watch-dir "/Users/cpandirlapali/chaithanya-docs/clinical_policies" \
  ask --question "Summarize compliance gaps with citations." --benchmark
```

## Payload examples

### 0) Handshake

`GET /api/openclaw/handshake` with `x-openclaw-secret` header (if enabled)

### 1) Folder sync

```json
{
  "folder_path": "/Users/you/HCA_Clinical_Protocols",
  "extensions": [".pdf", ".txt", ".md"]
}
```

### 2) Ask mode

```json
{
  "question": "Does our ICU sepsis policy conflict with CMS 2026 updates?",
  "mode": "long_context",
  "top_k": 8,
  "benchmark": false
}
```

### 3) Benchmark mode

```json
{
  "question": "Does our ICU sepsis policy conflict with CMS 2026 updates?",
  "mode": "long_context",
  "top_k": 8,
  "benchmark": true
}
```

## Suggested OpenClaw flow

1. Watch local policy folder.
2. On change, call `/api/openclaw/sync-folder`.
3. On chat message, call `/api/openclaw/ask`.
4. Route answer + citations back to Slack/WhatsApp.
