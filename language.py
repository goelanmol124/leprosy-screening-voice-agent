from config import DEFAULT_TTS_LANGUAGE, TTS_SPEAKER_EN, TTS_SPEAKER_HI


def detect_language_code(text: str) -> str:
    if not text or not text.strip():
        return DEFAULT_TTS_LANGUAGE

    devanagari = sum(1 for char in text if "\u0900" <= char <= "\u097F")
    latin = sum(1 for char in text if char.isascii() and char.isalpha())

    if latin > devanagari * 1.5 and latin >= 8:
        return "en-IN"
    return "hi-IN"


def tts_speaker_for_language(language_code: str) -> str:
    if language_code == "en-IN":
        return TTS_SPEAKER_EN
    return TTS_SPEAKER_HI
