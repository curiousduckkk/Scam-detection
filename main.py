import asyncio
import websockets
import json
import base64
import pyaudio
import os
import subprocess
import argparse
from dotenv import load_dotenv
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
import json
from datetime import datetime
import firebase_admin
from firebase_admin import messaging, credentials
import asyncio
from db import save_call_data

user_uuid = "user-1234"
# ---------------- Load .env ----------------
load_dotenv()

# Load configuration
with open('other/config.json', 'r') as f:
    config = json.load(f)

# Initialize Firebase Admin SDK (only once)
cred = credentials.Certificate(config['service_account_key'])
if not firebase_admin._apps:  # Only initialize if no app exists
    firebase_admin.initialize_app(cred)


OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
URI = "wss://api.openai.com/v1/realtime?model=gpt-realtime"

# Audio configuration
CHUNK = 1024
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 24000

SYSTEM_INSTRUCTIONS = """You are an AI scam detection assistant monitoring a live conversation in real-time. Your role is to:

1. Listen carefully to the entire conversation as it unfolds
2. Continuously assess the scam likelihood on a scale of 1-10 (where 10 = definitely a scam)
3. Update your assessment as new information emerges.
4. Identify red flags such as:
- Urgency or pressure tactics
- Requests for money, gift cards, or personal information
- Impersonation of officials, banks, or trusted organizations - Too-good-to-be-true offers
- Threats or fear-based manipulation
- Requests to keep things secret
5. Ranges are: Not a Scam (1-3), Possible Scam(4-7), Definitely Scam(8-10)

Do not explain or say anything else, just respond with "Not a Scam", "Possible Scam" or "Definitely Scam" along with scam score in a JSON format {"response":"Not a Scam/Possible Scam/Definitely Scam", "score":x}.
When you detect concerning patterns, update your assessment as per the conversation proceeds.
Your goal is to help the user recognize deceptive tactics and make informed decisions to protect themselves."""

def get_scam_category(score):
    """Determine scam category based on score"""
    if 1 <= score <= 3:
        return {
            "category": "Not a Scam",
            "emoji": "✅",
            "color": "#4CAF50",  # Green
            "priority": "default",
            "vibration": [100, 100]  # Short vibration
        }
    elif 4 <= score <= 7:
        return {
            "category": "Possible Scam",
            "emoji": "⚠",
            "color": "#FFC107",  # Yellow/Orange
            "priority": "high",
            "vibration": [200, 200, 200, 200]  # Medium vibration
        }
    elif 8 <= score <= 10:
        return {
            "category": "Definitely Scam",
            "emoji": "🚨",
            "color": "#F44336",  # Red
            "priority": "max",
            "vibration": [500, 200, 500, 200, 500]  # Strong vibration pattern
        }
    else:
        return {
            "category": "Unknown",
            "emoji": "❓",
            "color": "#9E9E9E",  # Gray
            "priority": "default",
            "vibration": [100, 100]
        }

# ---------------- Realtime Client ----------------
class RealtimeClient:
    def __init__(self, source="mic", instructions=SYSTEM_INSTRUCTIONS):
        self.source = source
        self.audio = pyaudio.PyAudio()
        self.input_stream = None
        self.output_stream = None
        self.ws = None
        self.arecord_process = None
        self.SYSTEM_INSTRUCTIONS = instructions

    async def start(self):
        """Connect to OpenAI Realtime API"""
        try:
            async with websockets.connect(
                URI,
                additional_headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "realtime=v1"
                },
                ping_interval=20,
                ping_timeout=20,
            ) as ws:
                self.ws = ws
                print("Connected to OpenAI Realtime API")

                # Initialize audio streams
                if self.source == "mic":
                    self.input_stream = self.audio.open(
                        format=FORMAT,
                        channels=CHANNELS,
                        rate=RATE,
                        input=True,
                        frames_per_buffer=CHUNK
                    )
                elif self.source == "arecord":
                    self.arecord_process = subprocess.Popen(
                        ["arecord", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL
                    )

                self.output_stream = self.audio.open(
                    format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    output=True,
                    frames_per_buffer=CHUNK
                )

                # Configure session
                await self.configure_session()

                # Start concurrent tasks
                await asyncio.gather(
                    self.send_audio(),
                    self.receive_messages()
                )
        except Exception as e:
            print(f"Realtime connection error: {e}")
        finally:
            await self.close_ws()
            self.cleanup()

    async def send_notification(self, phone_number, scam_score, response_text):
        """
        Send scam alert notification to Android device

        Args:
            phone_number: Phone number that called
            scam_score: Scam score (1-10)
            response_text: Description of the scam
        """

        # Get category info based on score
        category_info = get_scam_category(scam_score)

        # Create title with emoji and category
        title = f"{category_info['emoji']} {category_info['category']}"

        # Create body with score
        body = f"{response_text}\nScore: {scam_score}/10"

        # Create message with both notification and data payload
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body
            ),
            data={
                "phone_number": phone_number,
                "scam_score": str(scam_score),
                "response": response_text,
                "category": category_info['category'],
                "timestamp": datetime.now().isoformat()
            },
            token=self.fcm_token,
            android=messaging.AndroidConfig(
                priority='high',
                notification=messaging.AndroidNotification(
                    sound='default',
                    priority=category_info['priority'],
                    channel_id='scam_alerts',
                    color=category_info['color'],
                    icon='ic_dialog_alert',
                    default_sound=True,
                    default_vibrate_timings=False,
                    vibrate_timings_millis=category_info['vibration'],
                    visibility='public',
                    notification_count=1
                )
            )
        )

        try:
            response = messaging.send(message)
            print(f"✓ Successfully sent notification")
            print(f"  Category: {category_info['category']}")
            print(f"  Message ID: {response}")
            print(f"  Phone: {phone_number}")
            print(f"  Score: {scam_score}/10")
            print(f"  Color: {category_info['color']}")
            return True
        except Exception as e:
            print(f"✗ Error sending notification: {e}")
            return False
        
        
    async def configure_session(self):
        """Configure the session settings"""
        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "voice": "alloy",
                "input_audio_format": "pcm16",
                "output_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 500
                },
                "instructions": self.SYSTEM_INSTRUCTIONS
            }
        }
        await self.ws.send(json.dumps(session_update))
        print(f"Session configured with system instructions {self.SYSTEM_INSTRUCTIONS}")

    async def send_audio(self):
        """Capture and send audio"""
        print(f"Streaming audio from {self.source}...")
        try:
            while True:
                if self.source == "mic":
                    audio_data = self.input_stream.read(CHUNK, exception_on_overflow=False)
                elif self.source == "arecord":
                    audio_data = self.arecord_process.stdout.read(CHUNK * 2)
                    if not audio_data:
                        break

                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                message = {"type": "input_audio_buffer.append", "audio": audio_base64}
                await self.ws.send(json.dumps(message))
                await asyncio.sleep(0.01)
        except Exception as e:
            print(f"Error sending audio: {e}")

    async def receive_messages(self):
        """Receive and process messages from API"""
        try:
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")

                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    print(f"\nYou: {transcript}")

                elif event_type == "response.audio_transcript.done":
                    transcript = event.get("transcript", "")
                    print(f"Assistant: {transcript}\n")
                    try:
                        data = json.loads(transcript)
                        if "response" in data and "score" in data:
                            response_text = data["response"]
                            scam_score = int(data["score"])
                            await save_call_data(
                                user_uuid=user_uuid,
                                scam_score=scam_score,
                                other_person_phone=event.get("phone_number", "unknown"),
                                call_id=event.get("call_id", "unknown")
                            )                                                   
                            await self.send_notification(event.get("phone_number", "unknown"), scam_score, response_text)
                    except json.JSONDecodeError:
                        pass

                elif event_type == "response.audio.delta":
                    audio_base64 = event.get("delta", "")
                    if audio_base64:
                        audio_data = base64.b64decode(audio_base64)
                        # Uncomment to hear assistant response
                        # self.output_stream.write(audio_data)

                elif event_type == "error":
                    print(f"Error: {event.get('error', {})}")

        except Exception as e:
            print(f"Error receiving messages: {e}")

    async def close_ws(self):
        """Close WebSocket safely"""
        if self.ws:
            try:
                await self.ws.close()
                print("WebSocket connection closed")
            except Exception as e:
                print(f"Error closing WebSocket: {e}")

    def cleanup(self):
        """Clean up audio resources and subprocess"""
        if self.input_stream:
            self.input_stream.stop_stream()
            self.input_stream.close()
        if self.output_stream:
            self.output_stream.stop_stream()
            self.output_stream.close()
        if self.arecord_process:
            self.arecord_process.terminate()
        self.audio.terminate()
        print("Audio resources cleaned up")

# ---------------- FastAPI Integration ----------------
app = FastAPI()
realtime_client: RealtimeClient = None

# ✅ Always parse args so APP_CONFIG is set, even under uvicorn
parser = argparse.ArgumentParser()
parser.add_argument("--source", choices=["mic", "arecord"], default="mic", help="Audio input source")
parser.add_argument("--host", default="0.0.0.0")
parser.add_argument("--port", type=int, default=8000)
args, _ = parser.parse_known_args()
APP_CONFIG = args  # <--- now never None

class CallStartEvent(BaseModel):
    call_id: str
    phone_number: str
    incoming: bool
    exists_in_contacts: bool
    fcm_token: str

class CallEndEvent(BaseModel):
    call_id: str
    duration: int = 0

@app.post("/call/start")
async def call_start(event: CallStartEvent):
    print("/call/start body: ", event.dict())
    global realtime_client, APP_CONFIG
    await save_call_data(
                            user_uuid=user_uuid,
                            scam_score=5,
                            other_person_phone="1234567890",
                            call_id="0987654321"
                        )   
    instructions = SYSTEM_INSTRUCTIONS + f"\n\nThe caller {event.phone_number} is {'known' if event.exists_in_contacts else 'unknown'} to the user, keep this in context."
    if realtime_client is None:
        realtime_client = RealtimeClient(source=APP_CONFIG.source, instructions=instructions)

        realtime_client.phone_number = event.phone_number
        realtime_client.fcm_token = event.fcm_token

        asyncio.create_task(realtime_client.start())
        return {"status": "Realtime scam detection started"}
    else:
        realtime_client.SYSTEM_INSTRUCTIONS = instructions

        realtime_client.phone_number = event.phone_number
        realtime_client.fcm_token = event.fcm_token

        return {"status": "Realtime client already running, instructions updated"}

@app.post("/call/end")
async def call_end(event: CallEndEvent):
    print("/call/end body: ", event.dict())
    global realtime_client
    if realtime_client:
        await realtime_client.close_ws()
        realtime_client.cleanup()
        realtime_client = None
        return {"status": f"Call ended, duration {event.duration}s, realtime client cleaned up"}
    return {"status": "No active call to end"}

# ---------------- Run FastAPI ----------------
if __name__ == "__main__":
    uvicorn.run("main:app", host=args.host, port=8080, reload=True)
