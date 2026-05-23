import os
import uuid
import base64
import json
import requests
import httpx
import asyncio
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

# Initialize LiveKit Server API
try:
    from livekit import api
except ImportError:
    api = None

load_dotenv()

API_KEY = os.getenv("SARVAM_API_KEY", "")
if not API_KEY:
    raise ValueError("SARVAM_API_KEY is not set in environment or .env file.")

# In-memory stores
sessions = {}
tts_cache = {}
GREETING_TEXT = (
    "नमस्ते! मैं नेशनल हेल्थ हेल्पलाइन से आपकी कम्युनिटी हेल्थ हेल्पर हूँ। "
    "आज मैं आपकी क्या मदद कर सकती हूँ?"
)
cached_greeting_audio = ""

async def call_tts(text: str, client: httpx.AsyncClient = None) -> str:
    """Helper to convert text to speech using Sarvam TTS API and return base64 string"""
    if not text:
        return ""
    if text in tts_cache:
        return tts_cache[text]
        
    url = "https://api.sarvam.ai/text-to-speech"
    headers = {
        "api-subscription-key": API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "inputs": [text], # Bulbul v3 requires inputs as an array of strings
        "target_language_code": "hi-IN", # Ritu voice is warm and optimized for Indic/Hinglish
        "speaker": "ritu",
        "model": "bulbul:v3",
        "speech_sample_rate": 22050,
        "output_audio_codec": "mp3",
        "pace": 1.1  # Set pace to 1.1x to speed up audio delivery and match LiveKit snappy pacing
    }
    try:
        # Use the persistent async client from app state or optional parameter
        if client is None:
            client = app.state.client
        res = await client.post(url, json=payload, headers=headers, timeout=20.0)
        if res.status_code == 200:
            data = res.json()
            if "audios" in data and len(data["audios"]) > 0:
                audio_base64 = data["audios"][0]
                tts_cache[text] = audio_base64
                return audio_base64
        # Return fallback empty or error
        return ""
    except Exception:
        return ""

@asynccontextmanager
async def lifespan(app: FastAPI):
    global cached_greeting_audio
    # Initialize persistent keep-alive async client
    app.state.client = httpx.AsyncClient(timeout=60.0)
    
    # Pre-generate static greeting TTS at startup to warm up cache
    print("Warming up static greeting TTS audio cache...")
    try:
        cached_greeting_audio = await call_tts(GREETING_TEXT, client=app.state.client)
        print("Greeting cache warmed up successfully.")
    except Exception as e:
        print(f"Failed to pre-warm greeting cache: {e}")
        
    yield
    # Cleanup on shutdown
    await app.state.client.aclose()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


CORE_BRAIN_PROMPT = """## Role and Objective
You are a warm, compassionate community health helper for the National Health Helpline. Your job is to talk to citizens over the phone to understand if they have skin or nerve issues that require a free checkup at a government clinic. 

## Core Rules for Talking
1. Speak naturally in the language used by the caller (Hindi, English, or mixed Hinglish).
2. Never say everything all at once. Investigate exactly ONE symptom at a time. If the caller mentions three things, pick the most important one to talk about first.
3. Be gentle. If the caller asks directly about Leprosy or suspects it, do not hide it. Acknowledge it calmly and reassure them that it is 100% curable for free. If they do not mention it, talk about "old skin marks" (purana chakta) or "nerve weakness" (naso ki kamzori).
4. Do not be rigid. If the caller tells a long personal story, listen, validate their feelings briefly, and then guide them back to their health symptoms.
5. Output only normal, plain spoken text. Do not use bullet points, stars, or brackets in your response. Keep responses short and conversational.

## Medical Guide & Symptom Filters

### 1. Things that Rule Out Risk (Exclusion Filters)
If the caller describes any of these symptoms, the cause is likely a normal skin issue, not a major nerve disease:
- Marks or spots present since birth.
- Highly itchy rashes or patches that burn constantly.
- Rashes that appear and disappear within a few days.
- Milky-white, completely transparent skin patches.

### 2. Primary Risk Signs (Ask about these through daily life events)
- **Skin Patches:** Pale or reddish marks that have been present for months or years. Ask: "Does this mark feel normal, or does it feel numb when touched?"
- **Sensory Loss:** Not feeling pain or heat. Ask: "If a hot tea cup touches that area, or if a mosquito bites it, do you feel it normally or is the feeling less?"
- **Painless Injuries:** "Have you recently noticed any cuts, blisters, or burns on your hands or feet that didn't hurt at all when they happened?"
- **Nerve Weakness:**
  * "Does your slipper or chappal slip off your foot repeatedly while walking without you realizing it?"
  * "Is it becoming difficult to button your shirt or hold a glass of water tightly?"
  * "Do you find it hard to close one of your eyes completely when washing your face?"

### 3. Emergency Reaction Warning
If the caller reports a sudden high fever accompanied by painful, red, swollen lumps under the skin, or sudden severe pain in an old skin patch, stop normal questions. Tell them immediately to visit the nearest government hospital.

## Government Treatment Facts to Share
- Treatment is completely free at all Government Primary Health Centres (PHCs), Community Health Centres (CHCs), and District Hospitals.
- The medicine comes in simple Blister Calendar Packs (MDT) taken for 6 or 12 months.
- Once treatment starts, the person stops spreading the disease immediately. It does not spread by simple touch.
- Side effects like reddish urine or temporary skin darkening are completely safe and normal.

## Example Conversations and Triage Flow

Example 1 (High Risk / Slipper and Sensation Issues):
Caller: "I feel some weakness in my right foot, and sometimes my chappal just slips off."
AI: "I understand, thank you for sharing this. We must check this carefully. When your slipper slips off, do you realize it immediately, or does it happen without you noticing? Also, do you feel any numbness on the sole of that foot?"
Caller: "No, I don't feel it slipping. I only notice when I look down. There is also a light patch on my leg."
AI: "Thank you for explaining. This nerve weakness is very important to get checked. Even if it is something like leprosy, please don't worry—it is 100% curable for free at government clinics. Let me check: if you touch that light patch on your leg, does it feel completely normal, or does it feel numb compared to other skin?"

Example 2 (Low Risk / Exclusion Filter):
Caller: "I have a rash on my hand that is extremely itchy and it burns a lot when I sweat."
AI: "I hear you. Rashes that are very itchy or burn constantly are usually standard skin allergies or infections rather than a nerve issue. Have you had this since birth, or did it start recently?"
Caller: "It started last week after using a new soap."
AI: "That sounds like a normal reaction to the soap. I recommend visiting a local doctor or applying a simple soothing cream. Since it is highly itchy, it is very unlikely to be any major nerve disease."

Example 3 (Emergency lumps):
Caller: "I have a light patch, but suddenly today I got a very high fever and painful red lumps on my arms. I can't even touch them."
AI: "Please listen to me carefully. This requires immediate medical attention. Since you have a high fever with painful, swollen red lumps, you should visit the nearest government hospital or PHC immediately. Please do not wait. This is urgent, and doctors there will treat you for free right away."
"""

REVIEW_PROMPT = """## Objective
Analyze the provided telephone transcript between the health helpline AI and the patient. Extract the reported symptoms, categorize the risk, and output a clean summary.

## Diagnostic Rubric
- HIGH RISK: Caller has an old skin patch WITH confirmed loss of sensation (numbness), or reports painless burns/injuries, or clear muscle weakness like dropping slippers or clawed fingers.
- MEDIUM RISK: Caller has long-standing skin patches but sensory loss is unconfirmed, or has family members who previously had leprosy.
- LOW RISK: Caller has itchy rashes, patches since birth, or standard skin allergies with normal sensation.

## Output Format
Provide the analysis in a clean JSON data structure with these exact keys:

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

Return ONLY the raw JSON object. Do not wrap it in markdown block quotes or add any extra characters.
"""

@app.get("/")
def index():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Helpline Website is compiling... Please refresh</h1>")

@app.post("/api/start-call")
async def start_call():
    session_id = uuid.uuid4().hex
    sessions[session_id] = [{"role": "assistant", "content": GREETING_TEXT}]
    
    # 1. LiveKit Token generation support
    token_jwt = ""
    livekit_url = os.getenv("LIVEKIT_URL", "")
    livekit_api_key = os.getenv("LIVEKIT_API_KEY", "")
    livekit_api_secret = os.getenv("LIVEKIT_API_SECRET", "")
    
    if api and livekit_api_key and livekit_api_secret and "your-project" not in livekit_url:
        try:
            room_name = f"room-{session_id}"
            token = api.AccessToken(
                livekit_api_key,
                livekit_api_secret
            ).with_identity(f"user-{uuid.uuid4().hex}").with_grants(
                api.VideoGrants(room_join=True, room=room_name)
            )
            token_jwt = token.to_jwt()
            print(f"Generated LiveKit token for session {session_id} in room {room_name}")
        except Exception as e:
            print(f"Failed to generate LiveKit token: {e}")
            
    return JSONResponse({
        "session_id": session_id,
        "text": GREETING_TEXT,
        "audio": cached_greeting_audio,
        # LiveKit real-time parameters
        "livekit_token": token_jwt,
        "livekit_url": livekit_url
    })

@app.post("/api/voice-turn")
async def voice_turn(file: UploadFile = File(...), session_id: str = Form(...)):
    if session_id not in sessions:
        sessions[session_id] = []
        
    audio_bytes = await file.read()
    
    # 1. Speech-to-Text via Sarvam REST API
    stt_url = "https://api.sarvam.ai/speech-to-text"
    headers = {"api-subscription-key": API_KEY}
    files = {"file": (file.filename, audio_bytes, file.content_type)}
    data = {
        "model": "saaras:v3",
        "mode": "transcribe",
        "language": "unknown"  # Enable multilingual auto-detection to match LiveKit agent behavior
    }
    
    try:
        client = app.state.client
        stt_res = await client.post(stt_url, files=files, data=data, headers=headers, timeout=25.0)
        stt_data = stt_res.json()
        transcript = stt_data.get("transcript", "").strip()
    except Exception as e:
        transcript = ""
        
    if not transcript:
        # User spoke nothing or error occurred
        fallback_text = "I am sorry, I couldn't hear that clearly. Could you please repeat? माफ़ कीजिये, मुझे स्पष्ट सुनाई नहीं दिया। क्या आप फिर से कह सकते हैं?"
        audio_base64 = await call_tts(fallback_text)
        return JSONResponse({
            "user_transcript": "",
            "ai_response": fallback_text,
            "audio": audio_base64
        })
        
    # Append transcript to history
    sessions[session_id].append({"role": "user", "content": transcript})
    
    # 2. Core Brain: Chat Completions using GPT-4o-mini (if OpenAI API Key present) or Sarvam-30B
    openai_key = os.getenv("OPENAI_API_KEY", "")
    use_openai = openai_key and not openai_key.startswith("sk-proj-xxxx")
    
    if use_openai:
        llm_url = "https://api.openai.com/v1/chat/completions"
        llm_headers = {
            "Authorization": f"Bearer {openai_key}",
            "Content-Type": "application/json"
        }
        llm_payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": CORE_BRAIN_PROMPT},
                *sessions[session_id]
            ],
            "temperature": 0.6
        }
    else:
        llm_url = "https://api.sarvam.ai/v1/chat/completions"
        llm_headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }
        llm_payload = {
            "model": "sarvam-30b",
            "messages": [
                {"role": "system", "content": CORE_BRAIN_PROMPT},
                *sessions[session_id]
            ],
            "temperature": 0.6
        }
    
    try:
        client = app.state.client
        llm_res = await client.post(llm_url, json=llm_payload, headers=llm_headers, timeout=50.0)
        llm_data = llm_res.json()
        ai_response = llm_data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"LLM API Call failed: {e}")
        ai_response = "I see. Please tell me more about it so I can guide you properly."
        
    # Append assistant response to history
    sessions[session_id].append({"role": "assistant", "content": ai_response})
    
    # 3. Text-to-Speech response
    audio_base64 = await call_tts(ai_response)
    
    return JSONResponse({
        "user_transcript": transcript,
        "ai_response": ai_response,
        "audio": audio_base64
    })

@app.post("/api/review")
async def review_call(payload: dict):
    session_id = payload.get("session_id", "")
    if not session_id:
        return JSONResponse({"error": "session_id required"}, status_code=400)
        
    history = []
    
    # 1. First attempt to read transcript from LiveKit shared JSON file
    transcripts_dir = os.path.join(os.path.dirname(__file__), "transcripts")
    transcript_path = os.path.join(transcripts_dir, f"{session_id}.json")
    
    # Retry loop to wait for LiveKit worker to finish writing the file
    for _ in range(5):
        if os.path.exists(transcript_path):
            break
        await asyncio.sleep(0.3)
        
    if os.path.exists(transcript_path):
        try:
            with open(transcript_path, "r", encoding="utf-8") as f:
                history = json.load(f)
            print(f"Loaded LiveKit session transcript for review from {transcript_path}")
        except Exception as e:
            print(f"Failed to read LiveKit transcript file: {e}")
            
    # 2. Backward compatibility fallback: read from in-memory REST sessions
    if not history and session_id in sessions:
        history = sessions[session_id]
        print(f"Loaded REST session transcript for review from memory")
        
    if not history:
        return JSONResponse({"error": "Conversation history not found"}, status_code=404)
        
    transcript_str = ""
    for msg in history:
        role = "Helper" if msg["role"] == "assistant" else "Caller"
        transcript_str += f"{role}: {msg['content']}\n"
        
    # Call Sarvam-30B to run the Review analysis
    llm_url = "https://api.sarvam.ai/v1/chat/completions"
    llm_headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    analysis_payload = {
        "model": "sarvam-30b",
        "messages": [
            {"role": "system", "content": REVIEW_PROMPT},
            {"role": "user", "content": f"Here is the call transcript:\n{transcript_str}"}
        ],
        "temperature": 0.1
    }
    
    try:
        client = app.state.client
        res = await client.post(llm_url, json=analysis_payload, headers=llm_headers, timeout=20.0)
        report_text = res.json()["choices"][0]["message"]["content"].strip()
        # Clean any markdown block fences in case the model ignored rules
        if report_text.startswith("```json"):
            report_text = report_text.replace("```json", "", 1).replace("```", "", -1).strip()
        elif report_text.startswith("```"):
            report_text = report_text.replace("```", "", 1).replace("```", "", -1).strip()
            
        report = json.loads(report_text)
    except Exception as e:
        print(f"Failed to parse model review: {e}")
        # Fallback triage report if API fails
        report = {
            "language_used": "Undetermined",
            "symptoms_found": {
                "skin_patch": "Analysis failed to process transcript",
                "numbness": "Analysis failed to process transcript",
                "hand_foot_weakness": "Analysis failed to process transcript"
            },
            "risk_assessment": {
                "level": "MEDIUM",
                "reason": "Post-call review completed with general fallbacks due to formatting error."
            },
            "action_required": {
                "send_health_worker": True,
                "patient_sms_advice": "Thank you for contacting the National Health Helpline. We have scheduled a free check-up for you at your nearest government Primary Health Centre. Free treatment is 100% available."
            }
        }
        
    return JSONResponse(report)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
