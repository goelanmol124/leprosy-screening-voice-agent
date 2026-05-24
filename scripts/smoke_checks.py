"""
Manual smoke checks for Sarvam APIs, FastAPI TestClient, and LiveKit dispatch.
Run from repository root:

  python scripts/smoke_checks.py sarvam
  python scripts/smoke_checks.py sarvam sample.webm
  python scripts/smoke_checks.py local
  python scripts/smoke_checks.py livekit http://127.0.0.1:8000
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import httpx  # noqa: E402
from dotenv import load_dotenv  # noqa: E402


def _cmd_sarvam(args: argparse.Namespace) -> None:
    load_dotenv()
    asyncio.run(_run_sarvam(args.audio_sample))


async def _run_sarvam(sample_path: str | None) -> None:
    k = os.getenv("SARVAM_API_KEY", "").strip()
    if not k:
        print("ERROR: SARVAM_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    from config import (  # noqa: E402
        CONVERSATION_MODEL,
        CORE_BRAIN_PROMPT,
        GREETING_TEXT,
        STT_LANGUAGE,
        STT_MODE,
        STT_MODEL,
        TTS_MODEL,
        TTS_OUTPUT_CODEC,
        TTS_PACE,
        TTS_SPEECH_SAMPLE_RATE,
    )
    from language import detect_language_code, tts_speaker_for_language  # noqa: E402
    from llm_utils import chat_completion  # noqa: E402

    async with httpx.AsyncClient() as client:
        for text in ("Hello", GREETING_TEXT[:80]):
            language_code = detect_language_code(text)
            payload = {
                "inputs": [text],
                "target_language_code": language_code,
                "speaker": tts_speaker_for_language(language_code),
                "model": TTS_MODEL,
                "speech_sample_rate": TTS_SPEECH_SAMPLE_RATE,
                "output_audio_codec": TTS_OUTPUT_CODEC,
                "pace": TTS_PACE,
            }
            r = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                json=payload,
                headers={
                    "api-subscription-key": k,
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
            r.raise_for_status()
            data = r.json()
            b64 = (data.get("audios") or [None])[0]
            raw = base64.b64decode(b64)
            assert len(raw) > 500
            print(f"OK TTS ({language_code}): {len(raw)} bytes synthesized")

        if sample_path and os.path.isfile(sample_path):
            with open(sample_path, "rb") as f:
                body = f.read()
            fname = os.path.basename(sample_path)
            ct = "audio/wav" if fname.lower().endswith(".wav") else "audio/webm"
            for field_name in ("language", "language_code"):
                files = {"file": (fname, body, ct)}
                data_body = {"model": STT_MODEL, "mode": STT_MODE, field_name: STT_LANGUAGE}
                r = await client.post(
                    "https://api.sarvam.ai/speech-to-text",
                    files=files,
                    data=data_body,
                    headers={"api-subscription-key": k},
                    timeout=40.0,
                )
                if r.status_code == 200:
                    tr = r.json().get("transcript", "")
                    print(f"OK STT field={field_name!r}: transcript={tr[:120]!r}...")
                else:
                    print(f"STT field={field_name!r}: HTTP {r.status_code} {r.text[:300]}")
        else:
            print("SKIP STT file test (pass path as second argument)")

        messages = [
            {"role": "system", "content": CORE_BRAIN_PROMPT},
            {
                "role": "user",
                "content": "Meri taang par ek halka dhba hai aur wahan kam suann mehsoos hota hai.",
            },
        ]
        reply = await chat_completion(
            client,
            messages=messages,
            temperature=0.4,
            max_tokens=120,
            model=CONVERSATION_MODEL,
            sarvam_api_key=k,
        )
        assert reply.strip()
        print(f"OK LLM ({CONVERSATION_MODEL}): {reply[:200]!r}...")


def _cmd_local(_: argparse.Namespace) -> None:
    load_dotenv()
    if not os.getenv("SARVAM_API_KEY"):
        print("WARN: SARVAM_API_KEY unset; app import may fail", file=sys.stderr)

    from fastapi.testclient import TestClient  # noqa: E402

    import app as app_module  # noqa: E402

    client = TestClient(app_module.app)
    r = client.get("/")
    assert r.status_code == 200, r.text
    r = client.get("/api/voice-config")
    assert r.status_code == 200
    assert "silence_ms" in r.json()
    r = client.post("/api/start-call")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "session_id" in body
    assert body.get("voice_mode") in ("livekit", "rest")
    print("OK TestClient: /, /api/voice-config, /api/start-call")


async def _run_livekit(base: str) -> None:
    load_dotenv()
    from config import LIVEKIT_AGENT_NAME  # noqa: E402

    async with httpx.AsyncClient() as client:
        r = await client.post(f"{base.rstrip('/')}/api/start-call", timeout=30.0)
        r.raise_for_status()
        data = r.json()
        print("start-call:", {k: data[k] for k in ("session_id", "voice_mode", "livekit_url") if k in data})

    mode = data.get("voice_mode", "rest")
    if mode != "livekit":
        print("voice_mode is not livekit; check LIVEKIT_* env and agent worker.")
        return
    if not data.get("livekit_token"):
        print("ERROR: livekit_token empty while voice_mode=livekit", file=sys.stderr)
        sys.exit(1)

    try:
        from livekit import api
    except ImportError:
        print("livekit-api not installed; skipping dispatch list")
        return

    url = os.getenv("LIVEKIT_URL", "")
    key = os.getenv("LIVEKIT_API_KEY", "")
    secret = os.getenv("LIVEKIT_API_SECRET", "")
    session_id = data["session_id"]
    room_name = f"room-{session_id}"

    lk = api.LiveKitAPI(url, key, secret)
    try:
        dispatches = await lk.agent_dispatch.list_dispatch(room_name=room_name)
        print(f"Agent dispatches in {room_name}: {len(dispatches)}")
        for d in dispatches:
            print(" ", d)
    finally:
        await lk.aclose()

    print(f"OK: agent {LIVEKIT_AGENT_NAME!r} dispatched to room {room_name!r}")


def _cmd_livekit(args: argparse.Namespace) -> None:
    asyncio.run(_run_livekit(args.base))


def main() -> None:
    parser = argparse.ArgumentParser(description="Leprosy Helpline smoke checks")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_s = sub.add_parser("sarvam", help="Sarvam TTS / optional STT / LLM")
    p_s.add_argument(
        "audio_sample",
        nargs="?",
        default=None,
        help="Optional .wav/.webm for STT comparison",
    )
    p_s.set_defaults(func=_cmd_sarvam)

    p_l = sub.add_parser("local", help="FastAPI TestClient smoke (needs .env SARVAM key)")
    p_l.set_defaults(func=_cmd_local)

    p_k = sub.add_parser("livekit", help="POST start-call against running server + dispatch list")
    p_k.add_argument(
        "base",
        nargs="?",
        default="http://127.0.0.1:8000",
        help="Base URL of running FastAPI",
    )
    p_k.set_defaults(func=_cmd_livekit)

    ns = parser.parse_args()
    ns.func(ns)


if __name__ == "__main__":
    main()
