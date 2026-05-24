import json
import os

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

load_dotenv()

VAPI_PUBLIC_KEY = os.getenv("VAPI_PUBLIC_KEY", "")
VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
SARVAM_API_KEY = os.getenv("SARVAM_API_KEY", "")
SARVAM_MODEL = os.getenv("SARVAM_MODEL", "sarvam-m")

REVIEW_PROMPT = """## Objective
Analyze the provided telephone transcript between the health helpline AI and the patient. Extract the reported symptoms, categorize the risk, and output a clean summary.

## Diagnostic Rubric
- EMERGENCY: Sudden high fever with painful red swollen lumps, or sudden severe pain in an old skin patch.
- HIGH RISK: Caller has an old skin patch WITH confirmed loss of sensation (numbness), or reports painless burns/injuries, or clear muscle weakness like dropping slippers or clawed fingers.
- MEDIUM RISK: Caller has long-standing skin patches but sensory loss is unconfirmed, or has family members who previously had leprosy.
- LOW RISK: Caller has itchy rashes, patches since birth, or standard skin allergies with normal sensation.

## Output Format
Return ONLY a raw JSON object with these exact keys and no markdown:

{
  "language_used": "The language spoken during the call (e.g. Hindi, English, Hinglish)",
  "symptoms_found": {
    "skin_patch": "Details of any pale or red marks reported or 'None'",
    "numbness": "Details of any loss of feel or touch or 'None'",
    "hand_foot_weakness": "Details of any weakness, dropping slippers, or eye-closing issues or 'None'"
  },
  "risk_assessment": {
    "level": "LOW or MEDIUM or HIGH or EMERGENCY",
    "reason": "Brief single-sentence explanation for this choice"
  },
  "action_required": {
    "send_health_worker": true or false,
    "patient_sms_advice": "A reassuring text message to send to the patient guiding them to their nearest government PHC"
  }
}
"""

app = FastAPI()


@app.get("/")
def index():
    with open("index.html", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/config")
def config():
    return {"publicKey": VAPI_PUBLIC_KEY, "assistantId": VAPI_ASSISTANT_ID}


def get_transcript(call):
    artifact = call.get("artifact") or {}
    transcript = artifact.get("transcript", "").strip()
    if transcript:
        return transcript

    lines = []
    for msg in artifact.get("messages") or []:
        role = msg.get("role", "")
        text = msg.get("message") or msg.get("content") or ""
        if text:
            label = "Helper" if role in ("assistant", "bot") else "Caller"
            lines.append(f"{label}: {text}")
    return "\n".join(lines)


def parse_review(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 1)[1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.rsplit("```", 1)[0]
    return json.loads(cleaned.strip())


async def analyze_with_sarvam(client, transcript):
    r = await client.post(
        "https://api.sarvam.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {SARVAM_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": SARVAM_MODEL,
            "messages": [
                {"role": "system", "content": REVIEW_PROMPT},
                {"role": "user", "content": f"Here is the call transcript:\n{transcript}"},
            ],
            "temperature": 0.1,
            "max_tokens": 512,
        },
        timeout=60.0,
    )
    r.raise_for_status()
    return parse_review(r.json()["choices"][0]["message"]["content"])


@app.get("/api/review/{call_id}")
async def review(call_id: str):
    if not VAPI_API_KEY or not SARVAM_API_KEY:
        return JSONResponse({"error": "missing api keys"}, status_code=500)

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"https://api.vapi.ai/call/{call_id}",
            headers={"Authorization": f"Bearer {VAPI_API_KEY}"},
        )

        if r.status_code != 200:
            return JSONResponse({"error": "call not found"}, status_code=404)

        call = r.json()
        if call.get("status") != "ended":
            return JSONResponse({"error": "call still active"}, status_code=202)

        transcript = get_transcript(call)
        if not transcript:
            return JSONResponse({"error": "transcript not ready"}, status_code=202)

        try:
            return await analyze_with_sarvam(client, transcript)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
