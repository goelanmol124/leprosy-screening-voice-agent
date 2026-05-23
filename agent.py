import json
import logging
import os

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, TurnHandlingOptions, cli
from livekit.plugins import sarvam

from config import (
    CONVERSATION_MAX_TOKENS,
    CONVERSATION_MODEL,
    CONVERSATION_TEMPERATURE,
    CORE_BRAIN_PROMPT,
    DEFAULT_TTS_LANGUAGE,
    GREETING_TEXT,
    INTERRUPTION_MIN_DURATION,
    STT_LANGUAGE,
    STT_MODE,
    STT_MODEL,
    TRANSCRIPTS_DIR,
    TTS_MODEL,
    TTS_PACE,
    TTS_SPEAKER_HI,
    TURN_MAX_DELAY,
    TURN_MIN_DELAY,
)
from language import detect_language_code, tts_speaker_for_language
from session_store import ensure_storage_dirs

load_dotenv()

logger = logging.getLogger("voice-agent")
logger.setLevel(logging.INFO)


class LeprosyVoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=CORE_BRAIN_PROMPT)


server = AgentServer()


def build_tts(language_code: str = DEFAULT_TTS_LANGUAGE):
    return sarvam.TTS(
        target_language_code=language_code,
        model=TTS_MODEL,
        speaker=tts_speaker_for_language(language_code),
        pace=TTS_PACE,
    )


@server.rtc_session(agent_name="my-agent")
async def entrypoint(ctx: JobContext):
    logger.info("User connecting to room: %s", ctx.room.name)

    room_name = ctx.room.name
    session_id = "unknown"
    if room_name.startswith("room-"):
        session_id = room_name.replace("room-", "", 1)

    session = AgentSession(
        stt=sarvam.STT(
            language=STT_LANGUAGE,
            model=STT_MODEL,
            mode=STT_MODE,
            flush_signal=True,
        ),
        llm=sarvam.LLM(
            model=CONVERSATION_MODEL,
            temperature=CONVERSATION_TEMPERATURE,
            max_tokens=CONVERSATION_MAX_TOKENS,
        ),
        tts=build_tts(),
        turn_handling=TurnHandlingOptions(
            turn_detection="stt",
            endpointing={
                "mode": "fixed",
                "min_delay": TURN_MIN_DELAY,
                "max_delay": TURN_MAX_DELAY,
            },
            interruption={
                "mode": "vad",
                "min_duration": INTERRUPTION_MIN_DURATION,
                "resume_false_interruption": True,
            },
        ),
    )

    ensure_storage_dirs()
    transcript_path = os.path.join(TRANSCRIPTS_DIR, f"{session_id}.json")

    @session.on("user_input_transcribed")
    def on_user_transcribed(event):
        if not event.is_final:
            return
        language_code = detect_language_code(event.transcript)
        session._tts = build_tts(language_code)

    @session.on("close")
    def on_session_close():
        logger.info("Session closed for room %s", room_name)
        try:
            messages = []
            for item in session.chat_ctx.messages:
                if item.content:
                    messages.append({"role": item.role, "content": item.content})

            with open(transcript_path, "w", encoding="utf-8") as handle:
                json.dump(messages, handle, ensure_ascii=False, indent=2)
            logger.info("Successfully saved transcript for session %s", session_id)
        except Exception as exc:
            logger.error("Failed to save transcript: %s", exc)

    await session.start(agent=LeprosyVoiceAgent(), room=ctx.room)
    logger.info("Agent session started in room: %s", room_name)

    await session.say(GREETING_TEXT, allow_interruptions=True)


if __name__ == "__main__":
    cli.run_app(server)
