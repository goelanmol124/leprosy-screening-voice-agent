import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from config import (
    CLIENT_INTERRUPTION_FRAMES,
    CLIENT_NO_SPEECH_TIMEOUT_MS,
    CLIENT_REVIEW_DELAY_MS,
    CLIENT_SAFETY_TIMEOUT_MS,
    CLIENT_SILENCE_MS,
    CLIENT_SPEECH_FRAMES,
    CLIENT_SPEECH_THRESHOLD_OFFSET,
    FALLBACK_UNHEARD_TEXT,
    GREETING_TEXT,
    LIVEKIT_AGENT_NAME,
    STT_LANGUAGE,
    STT_MODE,
    STT_MODEL,
    TRANSCRIPT_RETRY_COUNT,
    TRANSCRIPT_RETRY_DELAY_S,
    TTS_MODEL,
    TTS_OUTPUT_CODEC,
    TTS_PACE,
    TTS_SPEECH_SAMPLE_RATE,
)
from language import detect_language_code, tts_speaker_for_language
from llm_utils import generate_conversation_reply, generate_review_report
from schemas import safe_parse_review
from session_store import get_or_create_session, load_transcript, save_session, save_transcript

try:
    from livekit import api

    CreateAgentDispatchRequest = api.CreateAgentDispatchRequest
except ImportError:
    api = None
    CreateAgentDispatchRequest = None

load_dotenv()

API_KEY = os.getenv("SARVAM_API_KEY", "")
if not API_KEY:
    raise ValueError("SARVAM_API_KEY is not set in environment or .env file.")

tts_cache: dict[str, str] = {}
cached_greeting_audio = ""


async def call_tts(text: str, client: httpx.AsyncClient | None = None) -> str:
    if not text:
        return ""

    language_code = detect_language_code(text)
    cache_key = f"{language_code}:{text}"
    if cache_key in tts_cache:
        return tts_cache[cache_key]

    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "api-subscription-key": API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": [text],
        "target_language_code": language_code,
        "speaker": tts_speaker_for_language(language_code),
        "model": TTS_MODEL,
        "speech_sample_rate": TTS_SPEECH_SAMPLE_RATE,
        "output_audio_codec": TTS_OUTPUT_CODEC,
        "pace": TTS_PACE,
    }

    try:
        if client is None:
            client = app.state.client
        response = await client.post(url, json=payload, headers=headers, timeout=20.0)
        if response.status_code == 200:
            data = response.json()
            if data.get("audios"):
                audio_base64 = data["audios"][0]
                tts_cache[cache_key] = audio_base64
                return audio_base64
        logger.warning("TTS failed: HTTP %s %s", response.status_code, response.text[:500])
        return ""
    except Exception as exc:
        logger.exception("TTS request error: %s", exc)
        return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    global cached_greeting_audio
    app.state.client = httpx.AsyncClient(timeout=60.0)

    print("Warming up static greeting TTS audio cache...")
    try:
        cached_greeting_audio = await call_tts(GREETING_TEXT, client=app.state.client)
        print("Greeting cache warmed up successfully.")
    except Exception as exc:
        print(f"Failed to pre-warm greeting cache: {exc}")

    yield
    await app.state.client.aclose()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as handle:
            return HTMLResponse(handle.read())
    return HTMLResponse("<h1>Helpline Website is compiling... Please refresh</h1>")


@app.get("/api/voice-config")
def voice_config():
    return JSONResponse(
        {
            "silence_ms": CLIENT_SILENCE_MS,
            "no_speech_timeout_ms": CLIENT_NO_SPEECH_TIMEOUT_MS,
            "safety_timeout_ms": CLIENT_SAFETY_TIMEOUT_MS,
            "interruption_frames": CLIENT_INTERRUPTION_FRAMES,
            "speech_frames": CLIENT_SPEECH_FRAMES,
            "speech_threshold_offset": CLIENT_SPEECH_THRESHOLD_OFFSET,
            "review_delay_ms": CLIENT_REVIEW_DELAY_MS,
        }
    )


@app.post("/api/start-call")
async def start_call():
    session_id = uuid.uuid4().hex
    greeting = [{"role": "assistant", "content": GREETING_TEXT}]
    get_or_create_session(session_id, greeting)

    token_jwt = ""
    voice_mode = "rest"
    livekit_url = os.getenv("LIVEKIT_URL", "")
    livekit_api_key = os.getenv("LIVEKIT_API_KEY", "")
    livekit_api_secret = os.getenv("LIVEKIT_API_SECRET", "")

    if (
        api
        and CreateAgentDispatchRequest is not None
        and livekit_api_key
        and livekit_api_secret
        and "your-project" not in livekit_url
    ):
        room_name = f"room-{session_id}"
        try:
            lkapi = api.LiveKitAPI(livekit_url, livekit_api_key, livekit_api_secret)
            try:
                await lkapi.agent_dispatch.create_dispatch(
                    CreateAgentDispatchRequest(
                        agent_name=LIVEKIT_AGENT_NAME,
                        room=room_name,
                    )
                )
            finally:
                await lkapi.aclose()

            token = (
                api.AccessToken(livekit_api_key, livekit_api_secret)
                .with_identity(f"user-{uuid.uuid4().hex}")
                .with_grants(api.VideoGrants(room_join=True, room=room_name))
            )
            token_jwt = token.to_jwt()
            voice_mode = "livekit"
            print(
                f"LiveKit ok: dispatched {LIVEKIT_AGENT_NAME}, token for "
                f"session {session_id} room {room_name}"
            )
        except Exception as exc:
            logger.exception("LiveKit setup failed (using REST fallback): %s", exc)

    return JSONResponse(
        {
            "session_id": session_id,
            "voice_mode": voice_mode,
            "text": GREETING_TEXT,
            "audio": cached_greeting_audio,
            "livekit_token": token_jwt,
            "livekit_url": livekit_url,
        }
    )


@app.post("/api/voice-turn")
async def voice_turn(file: UploadFile = File(...), session_id: str = Form(...)):
    history = get_or_create_session(session_id)
    audio_bytes = await file.read()

    stt_url = "https://api.sarvam.ai/speech-to-text"
    headers = {"api-subscription-key": API_KEY}
    files = {"file": (file.filename, audio_bytes, file.content_type or "audio/webm")}
    data = {
        "model": STT_MODEL,
        "mode": STT_MODE,
        "language_code": STT_LANGUAGE,
    }

    transcript = ""
    try:
        client = app.state.client
        stt_response = await client.post(
            stt_url, files=files, data=data, headers=headers, timeout=25.0
        )
        stt_response.raise_for_status()
        transcript = stt_response.json().get("transcript", "").strip()
    except Exception as exc:
        logger.exception("STT failed: %s", exc)
        transcript = ""

    if not transcript:
        audio_base64 = await call_tts(FALLBACK_UNHEARD_TEXT)
        return JSONResponse(
            {
                "user_transcript": "",
                "ai_response": FALLBACK_UNHEARD_TEXT,
                "audio": audio_base64,
            }
        )

    history.append({"role": "user", "content": transcript})

    try:
        client = app.state.client
        ai_response = await generate_conversation_reply(client, history, API_KEY)
    except Exception as exc:
        logger.exception("LLM API call failed: %s", exc)
        ai_response = (
            "I see. Please tell me more about it so I can guide you properly."
        )

    history.append({"role": "assistant", "content": ai_response})
    save_session(session_id, history)

    audio_base64 = await call_tts(ai_response)
    return JSONResponse(
        {
            "user_transcript": transcript,
            "ai_response": ai_response,
            "audio": audio_base64,
        }
    )


@app.post("/api/transcript")
async def store_transcript(payload: dict):
    session_id = payload.get("session_id", "")
    messages = payload.get("messages")
    if not session_id or not isinstance(messages, list):
        return JSONResponse({"error": "session_id and messages required"}, status_code=400)

    save_transcript(session_id, messages)
    return JSONResponse({"ok": True})


@app.post("/api/review")
async def review_call(payload: dict):
    session_id = payload.get("session_id", "")
    if not session_id:
        return JSONResponse({"error": "session_id required"}, status_code=400)

    voice_mode = str(payload.get("voice_mode") or "rest")

    history = None
    if voice_mode == "livekit":
        for _ in range(TRANSCRIPT_RETRY_COUNT):
            history = load_transcript(session_id)
            if history:
                break
            await asyncio.sleep(TRANSCRIPT_RETRY_DELAY_S)
    else:
        history = load_transcript(session_id)

    if not history:
        history = get_or_create_session(session_id)
        if not history:
            return JSONResponse({"error": "Conversation history not found"}, status_code=404)

    transcript_str = ""
    for message in history:
        role = "Helper" if message["role"] == "assistant" else "Caller"
        transcript_str += f"{role}: {message['content']}\n"

    try:
        client = app.state.client
        report_text = await generate_review_report(client, transcript_str, API_KEY)
        report = safe_parse_review(report_text, transcript_str)
    except Exception as exc:
        logger.exception("Failed to generate review: %s", exc)
        report = safe_parse_review("", transcript_str)

    return JSONResponse(report.model_dump())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
