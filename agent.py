import os
import logging
import json
from dotenv import load_dotenv
from livekit.agents import AgentServer, AgentSession, Agent, JobContext, TurnHandlingOptions, cli
from livekit.plugins import openai, sarvam

# Load environment variables
load_dotenv()

# Set up logging
logger = logging.getLogger("voice-agent")
logger.setLevel(logging.INFO)

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
- Side effects like reddish urine or temporary skin darkening are safe and normal.

## Example Conversations and Triage Flow

Example 1 (High Risk / Slipper and Sensation Issues):
Caller: "I feel some weakness in my right foot, and sometimes my chappal just slips off."
AI: "I understand, thank you for sharing this. We must check this carefully. When your slipper slips off, do you realize it immediately, or does it happen without you noticing? Also, do you feel any numbness on the sole of that foot?"
Caller: "No, I don't feel it slipping. I only notice when I look down. There is also a light patch on my leg."
AI: "Thank you for explaining. This nerve weakness is very important to get checked. Even if it is something like leprosy, please don't worry—it is 100% curable for free at government clinics. Let me check: if you touch that light patch on your leg, does it feel completely normal, or does it feel numb compared to other skin?"

Example 2 (Low Risk / Exclusion Filter):
Caller: "I have a rash on my hand that is extremely itchy and it burns a lot when I sweat."
AI: "I hear you. Rashes that are very itchy or burn constantly are usually allergies or infections rather than a nerve issue. Have you had this since birth, or did it start recently?"
Caller: "It started last week after using a new soap."
AI: "That sounds like a normal reaction to the soap. I recommend visiting a local doctor or applying a simple soothing cream. Since it is highly itchy, it is very unlikely to be any major nerve disease."

Example 3 (Emergency lumps):
Caller: "I have a light patch, but suddenly today I got a very high fever and painful red lumps on my arms. I can't even touch them."
AI: "Please listen to me carefully. This requires immediate medical attention. Since you have a high fever with painful, swollen red lumps, you should visit the nearest government hospital or PHC immediately. Please do not wait. This is urgent, and doctors there will treat you right away."
"""

class LeprosyVoiceAgent(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=CORE_BRAIN_PROMPT)

server = AgentServer()

@server.rtc_session(agent_name="my-agent")
async def entrypoint(ctx: JobContext):
    logger.info(f"User connecting to room: {ctx.room.name}")
    
    # Extract session_id from room name (format: room-<session_id>)
    room_name = ctx.room.name
    session_id = "unknown"
    if room_name.startswith("room-"):
        session_id = room_name.replace("room-", "", 1)
        
    session = AgentSession(
        # Saaras v3 STT - Converts speech to text with best practices
        stt=sarvam.STT(
            language="unknown",  # Auto-detect language (Hindi, English, etc.)
            model="saaras:v3",
            mode="transcribe",
            flush_signal=True  # Enables start/end of speech events
        ),
        
        # OpenAI LLM - The "brain" that processes and generates responses
        llm=openai.LLM(model="gpt-4o-mini"),
        
        # Bulbul TTS - Converts text to speech using warm 'ritu' female voice
        tts=sarvam.TTS(
            target_language_code="hi-IN",
            model="bulbul:v3",
            speaker="ritu",
            pace=1.1  # Set pace to 1.1x for snappier and more natural delivery speed
        ),
        
        # Advanced turn handling using latest TurnHandlingOptions
        turn_handling=TurnHandlingOptions(
            turn_detection="stt",  # Rely on speech-to-text end-of-utterance cues
            endpointing={
                "mode": "fixed",
                "min_delay": 0.8,  # Increased to 0.8s for natural conversational pauses
                "max_delay": 5.0,  # Prevent agent from waiting indefinitely
            },
            interruption={
                "mode": "vad",  # Use Voice Activity Detection for instant interruptions
                "min_duration": 0.4,  # Require 400ms of user speech to interrupt
                "resume_false_interruption": True,
            }
        )
    )
    
    # Save transcripts to a shared local transcripts folder
    transcripts_dir = os.path.join(os.path.dirname(__file__), "transcripts")
    os.makedirs(transcripts_dir, exist_ok=True)
    transcript_path = os.path.join(transcripts_dir, f"{session_id}.json")
    
    # Listen to session close event to save the conversation history
    @session.on("close")
    def on_session_close():
        logger.info(f"Session closed for room {room_name}")
        try:
            # Gather transcript from chat context
            messages = []
            for item in session.chat_ctx.messages:
                role = item.role
                content = item.content
                if content:
                    messages.append({"role": role, "content": content})
            
            # Write to transcript file
            with open(transcript_path, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            logger.info(f"Successfully saved transcript for session {session_id}")
        except Exception as e:
            logger.error(f"Failed to save transcript: {e}")
            
    # Start the agent session
    await session.start(
        agent=LeprosyVoiceAgent(),
        room=ctx.room
    )
    logger.info(f"Agent session started in room: {room_name}")
    
    # Speak the warm greeting message instantly upon connecting
    await session.say(
        "नमस्ते! मैं नेशनल हेल्थ हेल्पलाइन से आपकी कम्युनिटी हेल्थ हेल्पर हूँ। आज मैं आपकी क्या मदद कर सकती हूँ?",
        allow_interruptions=True
    )

if __name__ == "__main__":
    cli.run_app(server)
