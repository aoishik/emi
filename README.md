# Master Alchemist

Master Alchemist is a Slack bot + HTTP API for the Hack Club YSWS **Alchemize**.

It provides a small FastAPI server that:
- Receives Slack Events at `POST /slack/events`
- Lets trusted clients “ship” projects and send review outcomes via authenticated API endpoints
- Sends fulfillment / order status updates via DMs

## Features

- **Health check** endpoint (`GET /healthz`).
- **Slack Events** receiver (`POST /slack/events`) using Slack Bolt.
- **Project shipping** (`POST /ship`) posts to a ship channel + DMs the submitter.
- **Project review** endpoints:
  - `POST /review-accept`
  - `POST /review-reject`
  These fetch the reviewer’s Slack profile and post a “spoofed” channel message using the reviewer’s display name + avatar, then DM the submitter with feedback.
- **Order fulfillment DM updates**:
  - `POST /fulfill_pending`
  - `POST /fulfill_approved`
  - `POST /fulfill_reject`
  - `POST /fulfill_fullfilled`
- **Custom message relay** (`POST /custom`) to a user DM (`U…`) or channel (`C…`/`G…`).
- Optional **heartbeat logging**: if `LOGGING_CHANNEL_ID` is set, the bot posts `:alchemist: Bot is online!` every 5 minutes.

## Authentication

All non-Slack endpoints (everything except `GET /healthz` and `POST /slack/events`) require a bearer token:

- Header: `Authorization: Bearer <token>`
- The server compares `<token>` to the configured `AUTH_BEARER_TOKEN`.

## Configuration (Environment Variables)

The server prefers real environment variables. If a key is missing, it will try to load it from a local `.env` file (see `.env.sample`).

Required for full functionality:
- `AUTH_BEARER_TOKEN`
- `SLACK_BOT_TOKEN`
- `SLACK_SIGNING_SECRET`
- `SHIP_CHANNEL_ID`

Optional:
- `APP_NAME` (default: `Master Alchemist`)
- `API_HOST` (default: `0.0.0.0`)
- `API_PORT` (default: `8000`)
- `LOGGING_CHANNEL_ID` (enables heartbeat)

## Run locally (Linux/macOS)

1) Create a virtualenv and install dependencies:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

2) Configure environment variables:

```bash
cp .env.sample .env
# edit .env with your real values
```

3) Start the server:

```bash
python Master_Alchemist/main.py
```

Server listens on `http://API_HOST:API_PORT` (defaults to `http://0.0.0.0:8000`).

## Run locally (Windows)

PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.sample .env
# edit .env with your real values

py .\Master_Alchemist\main.py
```

If you don’t want to use `.env`, you can set environment variables directly:

```powershell
$env:AUTH_BEARER_TOKEN = "replace-me"
$env:SLACK_BOT_TOKEN = "xoxb-..."
$env:SLACK_SIGNING_SECRET = "..."
$env:SHIP_CHANNEL_ID = "C..."
py .\Master_Alchemist\main.py
```

## Deploy (production)

Minimal production guidance:

- Put the server behind HTTPS (reverse proxy like Nginx/Caddy) if it’s reachable from the internet.
- Store secrets in your hosting provider’s secret manager / environment configuration.
- Ensure `SLACK_SIGNING_SECRET` is set so Slack requests can be verified.

Typical start command (works anywhere you can run Python):

```bash
python Master_Alchemist/main.py
```

You can also run via Uvicorn directly:

```bash
uvicorn Master_Alchemist.main:app --host 0.0.0.0 --port 8000
```

## Run with Docker

Build the image:

```bash
docker build -t m_alc .
```

Run the container:

```bash
docker run -d -p 8000:8000 --restart always --name my-bot-service \
  -e AUTH_BEARER_TOKEN="$AUTH_BEARER_TOKEN" \
  -e SLACK_BOT_TOKEN="$SLACK_BOT_TOKEN" \
  -e SLACK_SIGNING_SECRET="$SLACK_SIGNING_SECRET" \
  -e SHIP_CHANNEL_ID="$SHIP_CHANNEL_ID" \
  -e LOGGING_CHANNEL_ID="$LOGGING_CHANNEL_ID" \
  m_alc
```

View logs:

```bash
docker logs my-bot-service
```

## API

Base URL in examples:

```bash
BASE_URL="http://${API_HOST:-0.0.0.0}:${API_PORT:-8000}"
AUTH="${AUTH_BEARER_TOKEN}"
```

### GET /healthz

```bash
curl -sS "$BASE_URL/healthz"
```

### POST /slack/events

This endpoint is called by Slack. Slack signs requests; a plain `curl` without a correct `X-Slack-Signature` will not work.
The handler always acknowledges with HTTP 200 to prevent Slack retries.

To test it locally, use the provided test script with `--test-slack-events`.

### POST /ship

```bash
curl -sS -X POST "$BASE_URL/ship" \
  -H "Authorization: Bearer $AUTH" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"U123ABCD","project_name":"Awesome Widget","project_link":"https://example.com"}'
```

### POST /review-accept

```bash
curl -sS -X POST "$BASE_URL/review-accept" \
  -H "Authorization: Bearer $AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id":"U123ABCD",
    "project_name":"Awesome Widget",
    "project_link":"https://example.com",
    "reviewer_id":"U987ZYXW",
    "feedback":"Excellent implementation.",
    "currencies":"100 Gold, 50 Silver"
  }'
```

### POST /review-reject

```bash
curl -sS -X POST "$BASE_URL/review-reject" \
  -H "Authorization: Bearer $AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id":"U123ABCD",
    "project_name":"Awesome Widget",
    "project_link":"https://example.com",
    "reviewer_id":"U987ZYXW",
    "feedback":"Please revise and resubmit."
  }'
```

### POST /fulfill_pending

```bash
curl -sS -X POST "$BASE_URL/fulfill_pending" \
  -H "Authorization: Bearer $AUTH" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"U123ABCD","order_id":"1001","item_name":"Shiny Relic","qty":"1","cost":"67 potions"}'
```

### POST /fulfill_approved

```bash
curl -sS -X POST "$BASE_URL/fulfill_approved" \
  -H "Authorization: Bearer $AUTH" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"U123ABCD","order_id":"1002","item_name":"Shiny Relic","qty":"1","cost":"67 potions"}'
```

### POST /fulfill_reject

```bash
curl -sS -X POST "$BASE_URL/fulfill_reject" \
  -H "Authorization: Bearer $AUTH" \
  -H "Content-Type: application/json" \
  -d '{"user_id":"U123ABCD","order_id":"1002","item_name":"Shiny Relic","qty":"1","cost":"67 potions","comment":"Out of stock this week."}'
```

### POST /fulfill_fullfilled

```bash
curl -sS -X POST "$BASE_URL/fulfill_fullfilled" \
  -H "Authorization: Bearer $AUTH" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id":"U123ABCD",
    "order_id":"1003",
    "item_name":"Shiny Relic",
    "qty":"1",
    "cost":"67 potions",
    "fulfilled_by":"The Utkarsh",
    "tracking_details":"Tracking #TRACK-1003"
  }'
```

### POST /custom

Send to a user (DM):

```bash
curl -sS -X POST "$BASE_URL/custom" \
  -H "Authorization: Bearer $AUTH" \
  -H "Content-Type: application/json" \
  -d '{"target_id":"U123ABCD","message":"Hello!"}'
```

Send to a channel:

```bash
curl -sS -X POST "$BASE_URL/custom" \
  -H "Authorization: Bearer $AUTH" \
  -H "Content-Type: application/json" \
  -d '{"target_id":"C12345678","message":"Hello channel!"}'
```

## Endpoint test scripts

- Linux/macOS (Bash): `./test_endpoints.sh` or the compatibility wrapper `./test_endpoint.sh`
- Windows (PowerShell): `./test_endpoints.ps1`

Examples:

```bash
./test_endpoints.sh --base-url http://127.0.0.1:8000 --auth-token "$AUTH" U_REVIEWER U_USER1 U_USER2
```

To include Slack Events testing:

```bash
./test_endpoints.sh --base-url http://127.0.0.1:8000 --auth-token "$AUTH" --test-slack-events --slack-signing-secret "$SLACK_SIGNING_SECRET" U_REVIEWER U_USER1
```

## Security notes

- Treat `AUTH_BEARER_TOKEN`, `SLACK_BOT_TOKEN`, and `SLACK_SIGNING_SECRET` as secrets.
- Anyone with the bearer token can post messages via this API.
