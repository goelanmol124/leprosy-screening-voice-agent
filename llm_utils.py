import httpx

from config import (
    CONVERSATION_MAX_TOKENS,
    CONVERSATION_MODEL,
    CONVERSATION_TEMPERATURE,
    CORE_BRAIN_PROMPT,
    LLM_REASONING_EFFORT,
    REVIEW_MAX_TOKENS,
    REVIEW_MODEL,
    REVIEW_PROMPT,
    REVIEW_TEMPERATURE,
)


def extract_message_content(message: dict) -> str:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    raise ValueError("Sarvam chat completion returned no message content")


async def chat_completion(
    client: httpx.AsyncClient,
    *,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    model: str,
    sarvam_api_key: str,
) -> str:
    url = "https://api.sarvam.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {sarvam_api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning_effort": LLM_REASONING_EFFORT,
    }

    response = await client.post(url, json=payload, headers=headers, timeout=50.0)
    response.raise_for_status()
    data = response.json()
    message = data["choices"][0]["message"]
    return extract_message_content(message)


async def generate_conversation_reply(
    client: httpx.AsyncClient,
    history: list[dict],
    sarvam_api_key: str,
) -> str:
    messages = [{"role": "system", "content": CORE_BRAIN_PROMPT}, *history]
    return await chat_completion(
        client,
        messages=messages,
        temperature=CONVERSATION_TEMPERATURE,
        max_tokens=CONVERSATION_MAX_TOKENS,
        model=CONVERSATION_MODEL,
        sarvam_api_key=sarvam_api_key,
    )


async def generate_review_report(
    client: httpx.AsyncClient,
    transcript: str,
    sarvam_api_key: str,
) -> str:
    messages = [
        {"role": "system", "content": REVIEW_PROMPT},
        {"role": "user", "content": f"Here is the call transcript:\n{transcript}"},
    ]
    return await chat_completion(
        client,
        messages=messages,
        temperature=REVIEW_TEMPERATURE,
        max_tokens=REVIEW_MAX_TOKENS,
        model=REVIEW_MODEL,
        sarvam_api_key=sarvam_api_key,
    )
