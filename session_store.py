import json
import os

from config import SESSIONS_DIR, TRANSCRIPTS_DIR


def ensure_storage_dirs() -> None:
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.makedirs(TRANSCRIPTS_DIR, exist_ok=True)


def _session_path(session_id: str) -> str:
    return os.path.join(SESSIONS_DIR, f"{session_id}.json")


def load_session(session_id: str) -> list[dict] | None:
    path = _session_path(session_id)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def save_session(session_id: str, messages: list[dict]) -> None:
    ensure_storage_dirs()
    with open(_session_path(session_id), "w", encoding="utf-8") as handle:
        json.dump(messages, handle, ensure_ascii=False, indent=2)


def save_transcript(session_id: str, messages: list[dict]) -> None:
    ensure_storage_dirs()
    path = os.path.join(TRANSCRIPTS_DIR, f"{session_id}.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(messages, handle, ensure_ascii=False, indent=2)


def get_or_create_session(session_id: str, greeting: list[dict] | None = None) -> list[dict]:
    existing = load_session(session_id)
    if existing is not None:
        return existing
    messages = greeting or []
    save_session(session_id, messages)
    return messages


def load_transcript(session_id: str) -> list[dict] | None:
    path = os.path.join(TRANSCRIPTS_DIR, f"{session_id}.json")
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)
