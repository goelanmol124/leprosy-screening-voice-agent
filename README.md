# Leprosy Helpline — voice triage prototype

Python **FastAPI** app ([`app.py`](app.py)) serves the UI and REST voice/review APIs. Optional **LiveKit** real-time agent runs from [`agent.py`](agent.py).

## Setup

```bash
python -m venv .venv
.\.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

Create a `.env` in the repo root (copy from your secrets store; see variables used in [`config.py`](config.py) and `app.py`): `SARVAM_API_KEY`, and for LiveKit mode `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, optional `LIVEKIT_AGENT_NAME`, `APP_BASE_URL` (agent transcript sync).

Run the web app:

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

Run the voice agent worker (separate process, for LiveKit):

```bash
python agent.py dev
```

## Smoke checks (optional)

From the repo root:

```bash
python scripts/smoke_checks.py local
python scripts/smoke_checks.py sarvam              # needs network + SARVAM_API_KEY
python scripts/smoke_checks.py sarvam sample.webm
python scripts/smoke_checks.py livekit             # needs uvicorn + credentials
```

## Deploy

[Railway](https://railway.app) entry: [`railway.toml`](railway.toml) and [`start.sh`](start.sh).

`nixpacks.toml` may be used by other Nixpacks-based builders; safe to remove only if you exclusively use Railway Railpack.

Runtime data under `sessions/` and `transcripts/` is listed in [`.gitignore`](.gitignore).
