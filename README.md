# Image AI Telegram Bot — Durable v8

A FastAPI + Telegram bot for Pixelcut-powered image processing.

## Included

- Remove Background
- Upscale 2× / 4×
- Expand with preset ratios and custom dimensions
- JPG / PNG input and output
- User settings via `/us`
- Screenshot-style admin UI via `/bs`
- Pixelcut API key rotation, failover, enable/disable and delete
- Encrypted API credentials
- `/uptime`, `/ping`, `/log` monitoring commands
- `/api/healthz`, `/api/status`, `/api/ping`, `/api/logs`
- Fast Render health-check startup
- Durable MongoDB GridFS image storage
- Durable MongoDB job records
- Automatic recovery of queued, processing and ready-to-send jobs after restart/redeploy
- Persistent image sessions: callback buttons continue to work after a process restart

## Durable restart behavior

Images are stored in MongoDB GridFS instead of the Render instance's temporary filesystem.
Every image-processing request has a MongoDB job record with its current state.

```text
Telegram upload
   ↓
MongoDB GridFS input file
   ↓
MongoDB durable job
   ↓
awaiting_action
   ↓
queued
   ↓
processing
   ↓
ready_to_send
   ↓
Telegram result
   ↓
completed
```

If the process restarts while a job is `queued`, `processing` or `ready_to_send`, startup recovery requeues it automatically. A job that already has a saved output can be delivered without running Pixelcut again.

Jobs and media expire after 24 hours unless completed earlier. Intermediate files expire quickly and are deleted after the operation.

## Monitoring commands

- `/uptime` — process uptime, service state, active jobs and worker count.
- `/ping` — application, MongoDB and Telegram connectivity.
- `/log` — recent runtime logs (admin only).
- `/log 50` — request up to 50 recent log entries.

HTTP endpoints:

```text
GET /api/healthz
GET /api/status
GET /api/ping
GET /api/logs
GET /media/{file_id}
POST /telegram/webhook
```

`/api/healthz` is deliberately lightweight so Render's 5-second health check is not blocked by MongoDB or Telegram startup.

## `/bs` admin UI

The admin interface uses a two-column, paginated Config Variables keyboard with:

- `Config Variables | Page: N`
- variable buttons
- numeric page navigation
- `Back` / `Close`
- variable detail screen
- `Edit Value`

`PIXELCUT_TIMEOUT` is editable from `/bs` and is stored in MongoDB. Valid values are 10–600 seconds. `MAX_UPLOAD_MB` and `OUTPUT_QUALITY` are also editable. Boolean controls can be toggled from the detail screen.

Environment secrets such as `TELEGRAM_BOT_TOKEN`, `MONGODB_URI`, `SETTINGS_ENCRYPTION_KEY`, `PUBLIC_BASE_URL` and `WEBHOOK_SECRET` are never exposed by `/bs`.

## Pixelcut

The project uses the Pixelcut API endpoints already used by the repository:

```text
POST /v1/upscale
POST /v1/remove-background
POST /v1/outpaint
```

The Pixelcut service uses `X-API-Key` authentication and returns a result URL. Multiple encrypted keys are supported with rotation and retryable failover.

## Koyeb

This repository includes first-class Koyeb deployment support through a production `Dockerfile`, a buildpack-compatible `Procfile`, a `.dockerignore`, a safe environment template, and a Koyeb CLI deployment helper.

Koyeb Web Services provide a `PORT` environment variable, and the application binds to that value instead of hard-coding a platform port. The Docker image exposes port `8000`, so Koyeb can derive the service port when `PORT` is not explicitly overridden. citeturn0search0turn1search0

### Recommended Koyeb configuration

1. Push this repository to GitHub and create a **Web Service** in Koyeb.
2. Select the repository and choose **Dockerfile** as the builder. Koyeb supports Dockerfile-based Git deployments and automatically redeploys when the configured production branch changes. citeturn0search2turn1search4
3. Expose **port 8000 / HTTP** and route `/` to port `8000`.
4. Configure an **HTTP health check** on `/api/healthz`. Koyeb supports custom HTTP health checks; the endpoint in this project is intentionally lightweight and does not wait for MongoDB or Telegram startup. citeturn0search5
5. Add the required environment variables below. Koyeb exposes environment variables at runtime and supplies `PORT` automatically for Web Services. citeturn0search0
6. For stable processing, keep at least one Web Service instance running when your Koyeb plan/configuration supports a minimum scale of 1. Koyeb can otherwise use scale-to-zero/sleep behavior depending on service configuration. citeturn0search4

### Koyeb CLI deployment

From the project root, after installing and authenticating the Koyeb CLI:

```bash
koyeb login
./deploy/koyeb-deploy.sh image-ai-bot image-ai-bot
```

The helper configures Docker build, HTTP port `8000`, the root route, and `/api/healthz` as the HTTP health check. Koyeb's CLI supports these port, route, and health-check settings for Web Services. citeturn0search1turn1search3

The helper does **not** contain Telegram, MongoDB, encryption, or API-key secrets. Add those through Koyeb environment variables/secrets.

### Koyeb environment variables

Copy `koyeb.env.example` and configure:

```text
TELEGRAM_BOT_TOKEN
MONGODB_URI
ADMIN_IDS
SETTINGS_ENCRYPTION_KEY
PUBLIC_BASE_URL
WEBHOOK_SECRET
```

Optional:

```text
MONGODB_DATABASE
MAX_UPLOAD_MB
PIXELCUT_TIMEOUT
UPTIME_URL
UPTIME_INTERVAL
```

After the first deployment, set `PUBLIC_BASE_URL` to the actual Koyeb public service URL and redeploy so Telegram receives the correct webhook URL.

### Why this avoids the previous health-check problem

`GET /api/healthz` returns immediately and does not require a MongoDB connection or Telegram initialization. Koyeb's health checks are used to determine whether an instance is ready and healthy, so keeping this endpoint lightweight prevents external health probes from being blocked by application startup/recovery work. citeturn0search5

The application itself starts its durable service/recovery loop in the FastAPI lifespan while the HTTP server becomes available immediately. MongoDB/Telegram failures are retried in the background rather than preventing the health endpoint from responding.

## Render

Build:

```bash
pip install -r requirements.txt
```

Start:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Health check:

```text
/api/healthz
```

Required environment variables:

```text
TELEGRAM_BOT_TOKEN
MONGODB_URI
ADMIN_IDS
SETTINGS_ENCRYPTION_KEY
PUBLIC_BASE_URL
WEBHOOK_SECRET
```

Optional:

```text
MONGODB_DATABASE
MAX_UPLOAD_MB
PIXELCUT_TIMEOUT
UPTIME_URL
UPTIME_INTERVAL
```

`UPTIME_URL` is an optional external self-ping target. It cannot prevent a hosting platform from sleeping or replacing a free instance by itself; use the platform's supported uptime/cron mechanism when continuous availability is required.

## Project structure

```text
app/
├── main.py
├── config.py
├── database.py
├── job_manager.py
├── monitoring.py
├── security.py
├── temp_storage.py
├── image_utils.py
├── keyboards.py
├── telegram.py
├── handlers/
│   ├── start.py
│   ├── image.py
│   ├── settings.py
│   └── admin.py
└── services/
    ├── pixelcut.py
    ├── upscale.py
    ├── remove_bg.py
    └── expand.py
```

## Security

Pixelcut API keys are encrypted before storage using `SETTINGS_ENCRYPTION_KEY`. Ordinary users never receive them. API-key display is disabled while encryption display is enabled.

Do not commit real credentials to Git.
