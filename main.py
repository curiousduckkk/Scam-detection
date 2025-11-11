import asyncio
import websockets
import json
import base64
import pyaudio
import os
import subprocess
import argparse
from fastapi import FastAPI
from pydantic import BaseModel
import uvicorn
from datetime import datetime
import firebase_admin
from firebase_admin import messaging, credentials
from typing import Optional
import signal

import config
from logger import main_logger, log_exception
from db import save_call_data

user_uuid = config.DEFAULT_USER_UUID

try:
    with open(config.FIREBASE_CONFIG_PATH, 'r') as f:
        firebase_config = json.load(f)
    
    cred = credentials.Certificate(firebase_config['service_account_key'])
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
    main_logger.info("Firebase initialized successfully")
except Exception as e:
    log_exception(main_logger, e, "Failed to initialize Firebase")
    firebase_config = None


def get_scam_category(score):
    """Determine scam category based on score"""
    if 1 <= score <= config.SCAM_SCORE_SAFE_MAX:
        return {
            "category": "Not a Scam",
            "emoji": "✅",
            "color": "#4CAF50",  #green
            "priority": "default",
            "vibration": [100, 100]
        }
    elif config.SCAM_SCORE_POSSIBLE_MIN <= score <= config.SCAM_SCORE_POSSIBLE_MAX:
        return {
            "category": "Possible Scam",
            "emoji": "⚠",
            "color": "#FFC107",  #yellow
            "priority": "high",
            "vibration": [200, 200, 200, 200]
        }
    elif score >= config.SCAM_SCORE_DEFINITE_MIN:
        return {
            "category": "Definitely Scam",
            "emoji": "🚨",
            "color": "#F44336",  #red
            "priority": "max",
            "vibration": [500, 200, 500, 200, 500]
        }
    else:
        return {
            "category": "Unknown",
            "emoji": "❓",
            "color": "#9E9E9E",  #gray
            "priority": "default",
            "vibration": [100, 100]
        }



class RealtimeClient:
    def __init__(self, source="mic", instructions=config.SYSTEM_INSTRUCTIONS, bluetooth_source=None):
        self.source = source
        self.audio = pyaudio.PyAudio()
        self.input_stream = None
        self.output_stream = None
        self.ws = None
        self.arecord_process = None
        self.SYSTEM_INSTRUCTIONS = instructions
        self.phone_number = "unknown"
        self.fcm_token = None
        self.bluetooth_source = bluetooth_source or self.detect_bluetooth_source()
        self.running = False
        self.audio_queue = asyncio.Queue(maxsize=config.AUDIO_QUEUE_MAX_SIZE)
        self.reconnect_attempts = 0
        self.call_id = None

    def detect_bluetooth_source(self):
        """Detect the Bluetooth audio source for call audio (incoming from phone)"""
        try:
            result = subprocess.run(
                ["pactl", "list", "sources", "short"],
                capture_output=True,
                text=True,
                timeout=config.COMMAND_TIMEOUT
            )
            
            main_logger.debug(f"Available sources:\n{result.stdout}")
            
            for line in result.stdout.split('\n'):
                if not line.strip():
                    continue
                
                if 'usb' in line.lower() and 'output' in line.lower() and 'monitor' in line.lower():
                    source_name = line.split()[1] if len(line.split()) > 1 else None
                    if source_name:
                        main_logger.info(f"Found USB audio output monitor (earphone loopback): {source_name}")
                        return source_name
            
            for line in result.stdout.split('\n'):
                if 'echocancel' in line.lower() and 'monitor' not in line.lower() and line.strip():
                    source_name = line.split()[1] if len(line.split()) > 1 else None
                    if source_name:
                        main_logger.info(f"Found echo-cancelled source: {source_name}")
                        return source_name
            
            for line in result.stdout.split('\n'):
                if not line.strip():
                    continue
                    
                if 'bluez' in line.lower() and 'handsfree_head_unit' in line.lower():
                    source_name = line.split()[1] if len(line.split()) > 1 else None
                    if source_name and 'monitor' not in source_name:
                        main_logger.info(f"Found HFP head unit source (phone audio): {source_name}")
                        return source_name
                
                elif 'bluez' in line.lower() and ('hsp' in line.lower() or 'headset' in line.lower()):
                    source_name = line.split()[1] if len(line.split()) > 1 else None
                    if source_name and 'monitor' not in source_name:
                        main_logger.info(f"Found HSP source (phone audio): {source_name}")
                        return source_name
            
            for line in result.stdout.split('\n'):
                if 'bluez' in line.lower() and 'monitor' not in line.lower() and 'gateway' not in line.lower() and line.strip():
                    source_name = line.split()[1] if len(line.split()) > 1 else None
                    if source_name:
                        main_logger.warning(f"Using generic bluez source: {source_name}")
                        return source_name
            
            main_logger.warning("Could not detect Bluetooth source for incoming audio")
            main_logger.info("Tip: Make sure you have audio playing through earphones or use --bluetooth-source to specify manually")
            return None
            
        except Exception as e:
            log_exception(main_logger, e, "Error detecting Bluetooth source")
            return None


    async def connect_websocket(self):
        """Connect to OpenAI Realtime API with retry logic"""
        for attempt in range(config.WS_MAX_RETRIES):
            try:
                main_logger.info(f"Connecting to OpenAI Realtime API (attempt {attempt + 1}/{config.WS_MAX_RETRIES})")
                
                async with asyncio.timeout(config.WS_CONNECTION_TIMEOUT):
                    self.ws = await websockets.connect(
                        config.OPENAI_REALTIME_URI,
                        additional_headers={
                            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                            "OpenAI-Beta": "realtime=v1"
                        },
                        ping_interval=config.WS_PING_INTERVAL,
                        ping_timeout=config.WS_PING_TIMEOUT,
                    )
                    
                main_logger.info("Connected to OpenAI Realtime API")
                self.reconnect_attempts = 0
                return True
                
            except (websockets.WebSocketException, asyncio.TimeoutError, OSError) as e:
                log_exception(main_logger, e, f"Connection attempt {attempt + 1} failed")
                
                if attempt < config.WS_MAX_RETRIES - 1:
                    delay = min(
                        config.WS_RETRY_BASE_DELAY * (2 ** attempt),
                        config.WS_RETRY_MAX_DELAY
                    )
                    main_logger.info(f"Retrying in {delay} seconds...")
                    await asyncio.sleep(delay)
                else:
                    main_logger.error("Max connection retries exceeded")
                    return False
        
        return False

    async def start(self):
        """Connect to OpenAI Realtime API and start processing"""
        self.running = True
        
        try:
            if not await self.connect_websocket():
                main_logger.error("Failed to establish WebSocket connection")
                return
            
            await self.setup_audio_capture()
            self.output_stream = self.audio.open(
                format=getattr(pyaudio, config.AUDIO_FORMAT),
                channels=config.AUDIO_CHANNELS,
                rate=config.AUDIO_RATE,
                output=True,
                frames_per_buffer=config.AUDIO_CHUNK_SIZE
            )

            await self.configure_session()

            await asyncio.gather(
                self.audio_producer(),
                self.audio_consumer(),
                self.receive_messages(),
                return_exceptions=True
            )
            
        except Exception as e:
            log_exception(main_logger, e, "Error in realtime client")
        finally:
            self.running = False
            await self.close_ws()
            self.cleanup()
    
    async def setup_audio_capture(self):
        """Setup audio capture based on source type"""
        try:
            if self.source == "mic":
                self.input_stream = self.audio.open(
                    format=getattr(pyaudio, config.AUDIO_FORMAT),
                    channels=config.AUDIO_CHANNELS,
                    rate=config.AUDIO_RATE,
                    input=True,
                    frames_per_buffer=config.AUDIO_CHUNK_SIZE
                )
                main_logger.info("Started microphone capture")
                
            elif self.source == "arecord":
                await self.setup_bluetooth_audio()
                
        except Exception as e:
            log_exception(main_logger, e, "Failed to setup audio capture")
            raise
    
    async def setup_bluetooth_audio(self):
        """Setup Bluetooth audio capture with echo cancellation"""
        echo_source = None
        try:
            check_module = subprocess.run(
                ["pactl", "list", "modules", "short"],
                capture_output=True,
                text=True,
                timeout=config.COMMAND_TIMEOUT
            )
            
            if "module-echo-cancel" not in check_module.stdout:
                main_logger.info("Loading echo cancellation module...")
                
                source_param = f"source_master={self.bluetooth_source}" if self.bluetooth_source else ""
                
                load_cmd = [
                    "pactl", "load-module", "module-echo-cancel",
                    "aec_method=webrtc",
                    "source_name=echocancel_phone",
                    "sink_name=echocancel_phone_sink"
                ]
                if source_param:
                    load_cmd.append(source_param)
                
                subprocess.run(load_cmd, timeout=config.COMMAND_TIMEOUT)
                await asyncio.sleep(0.5)
                echo_source = "echocancel_phone"
                main_logger.info("Echo cancellation enabled for phone audio")
            else:
                sources = subprocess.run(
                    ["pactl", "list", "sources", "short"],
                    capture_output=True,
                    text=True,
                    timeout=config.COMMAND_TIMEOUT
                )
                if "echocancel_phone" in sources.stdout:
                    echo_source = "echocancel_phone"
                    main_logger.info("Using existing echo-cancelled phone source")
        except Exception as e:
            log_exception(main_logger, e, "Could not load echo cancellation")
        
        audio_source = echo_source or self.bluetooth_source or "default"
        
        main_logger.info(f"Capturing audio from: {audio_source}")
        
        arecord_cmd = [
            "arecord", 
            "-f", "S16_LE", 
            "-r", str(config.AUDIO_RATE), 
            "-c", str(config.AUDIO_CHANNELS), 
            "-t", "raw"
        ]
        
        if audio_source == "default":
            arecord_cmd.extend(["-D", "pulse"])
        else:
            arecord_cmd = [
                "parec",
                f"--rate={config.AUDIO_RATE}",
                f"--channels={config.AUDIO_CHANNELS}",
                "--format=s16le",
                f"--device={audio_source}",
                "--raw"
            ]
        
        self.arecord_process = subprocess.Popen(
            arecord_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=4096
        )
        
        await asyncio.sleep(0.5)
        if self.arecord_process.poll() is not None:
            stderr_output = self.arecord_process.stderr.read().decode()
            main_logger.error(f"Audio capture failed: {stderr_output}")
            raise Exception("Audio capture failed to start")
        
        main_logger.info(f"Started capturing phone audio at {config.AUDIO_RATE} Hz")


    async def send_notification(self, phone_number, scam_score, response_text):
        """
        Send scam alert notification to Android device

        Args:
            phone_number: Phone number that called
            scam_score: Scam score (1-10)
            response_text: Description of the scam
        """
        if not self.fcm_token:
            main_logger.warning("No FCM token available, skipping notification")
            return False

        category_info = get_scam_category(scam_score)

        title = f"{category_info['emoji']} {category_info['category']}"

        body = f"{response_text}\nScore: {scam_score}/10"

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
            response = await asyncio.wait_for(
                asyncio.to_thread(messaging.send, message),
                timeout=config.NOTIFICATION_TIMEOUT
            )
            main_logger.info(
                f"Successfully sent notification - Category: {category_info['category']}, "
                f"Message ID: {response}, Phone: {phone_number}, Score: {scam_score}/10"
            )
            return True
        except asyncio.TimeoutError:
            main_logger.error("Notification send timeout")
            return False
        except Exception as e:
            log_exception(main_logger, e, "Error sending notification")
            return False
        
        
    async def configure_session(self):
        """Configure the session settings"""
        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["audio", "text"],
                "voice": "alloy",
                "input_audio_format": config.AUDIO_INPUT_FORMAT,
                "output_audio_format": config.AUDIO_OUTPUT_FORMAT,
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
        
        try:
            await asyncio.wait_for(
                self.ws.send(json.dumps(session_update)),
                timeout=config.AUDIO_SEND_TIMEOUT
            )
            main_logger.info("Session configured with scam detection instructions")
        except Exception as e:
            log_exception(main_logger, e, "Failed to configure session")
            raise

    async def audio_producer(self):
        """Capture audio and put into queue (non-blocking)"""
        main_logger.info(f"Starting audio producer from {self.source}...")
        
        while self.running:
            try:
                if self.source == "mic":
                    audio_data = await asyncio.to_thread(
                        self.input_stream.read, 
                        config.AUDIO_CHUNK_SIZE, 
                        exception_on_overflow=False
                    )
                elif self.source == "arecord":
                    audio_data = await asyncio.to_thread(
                        self.arecord_process.stdout.read,
                        config.AUDIO_CHUNK_SIZE * 2
                    )
                    
                    if not audio_data or len(audio_data) == 0:
                        main_logger.warning("No audio data from arecord, stream ended")
                        break
                
                try:
                    await asyncio.wait_for(
                        self.audio_queue.put(audio_data),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    main_logger.warning("Audio queue full, dropping frame")
                    
            except Exception as e:
                if self.running:
                    log_exception(main_logger, e, "Error in audio producer")
                break
        
        main_logger.info("Audio producer stopped")

    async def audio_consumer(self):
        """Send audio from queue to WebSocket"""
        main_logger.info("Starting audio consumer...")
        
        while self.running:
            try:
                audio_data = await asyncio.wait_for(
                    self.audio_queue.get(),
                    timeout=1.0
                )
                
                audio_base64 = base64.b64encode(audio_data).decode('utf-8')
                message = {"type": "input_audio_buffer.append", "audio": audio_base64}
                
                await asyncio.wait_for(
                    self.ws.send(json.dumps(message)),
                    timeout=config.AUDIO_SEND_TIMEOUT
                )
                
                await asyncio.sleep(0.01)
                
            except asyncio.TimeoutError:
                continue
            except websockets.ConnectionClosed:
                main_logger.error("WebSocket connection closed during audio send")
                break
            except Exception as e:
                if self.running:
                    log_exception(main_logger, e, "Error in audio consumer")
                break
        
        main_logger.info("Audio consumer stopped")


    async def receive_messages(self):
        """Receive and process messages from API"""
        main_logger.info("Starting message receiver...")
        
        try:
            async for message in self.ws:
                event = json.loads(message)
                event_type = event.get("type")

                if event_type == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript", "")
                    main_logger.info(f"User: {transcript}")

                elif event_type == "response.audio_transcript.done":
                    transcript = event.get("transcript", "")
                    main_logger.info(f"Assistant: {transcript}")
                    
                    try:
                        data = json.loads(transcript)
                        if "response" in data and "score" in data:
                            response_text = data["response"]
                            scam_score = int(data["score"])
                            
                            await save_call_data(
                                user_uuid=user_uuid,
                                scam_score=scam_score,
                                phone_number=self.phone_number,
                                call_id=self.call_id or "unknown"
                            )
                            
                            if scam_score >= config.NOTIFICATION_THRESHOLD:
                                await self.send_notification(
                                    self.phone_number, 
                                    scam_score, 
                                    response_text
                                )
                    except (json.JSONDecodeError, ValueError, KeyError) as e:
                        main_logger.debug(f"Could not parse assistant response as scam data: {e}")

                elif event_type == "response.audio.delta":
                    audio_base64 = event.get("delta", "")
                    if audio_base64:
                        # Uncomment to hear assistant response
                        # audio_data = base64.b64decode(audio_base64)
                        # self.output_stream.write(audio_data)
                        pass

                elif event_type == "error":
                    error_info = event.get('error', {})
                    main_logger.error(f"API Error: {error_info}")

        except websockets.ConnectionClosed:
            main_logger.warning("WebSocket connection closed")
        except Exception as e:
            if self.running:
                log_exception(main_logger, e, "Error receiving messages")

    async def close_ws(self):
        """Close WebSocket safely"""
        if self.ws:
            try:
                await asyncio.wait_for(self.ws.close(), timeout=5.0)
                main_logger.info("WebSocket connection closed")
            except Exception as e:
                log_exception(main_logger, e, "Error closing WebSocket")

    def cleanup(self):
        """Clean up audio resources and subprocess"""
        main_logger.info("Cleaning up resources...")
        
        if self.input_stream:
            try:
                self.input_stream.stop_stream()
                self.input_stream.close()
            except Exception as e:
                log_exception(main_logger, e, "Error closing input stream")
                
        if self.output_stream:
            try:
                self.output_stream.stop_stream()
                self.output_stream.close()
            except Exception as e:
                log_exception(main_logger, e, "Error closing output stream")
                
        if self.arecord_process:
            try:
                self.arecord_process.terminate()
                self.arecord_process.wait(timeout=config.PROCESS_CLEANUP_TIMEOUT)
            except subprocess.TimeoutExpired:
                main_logger.warning("arecord process did not terminate, killing...")
                self.arecord_process.kill()
            except Exception as e:
                log_exception(main_logger, e, "Error terminating arecord process")
                
        try:
            self.audio.terminate()
        except Exception as e:
            log_exception(main_logger, e, "Error terminating PyAudio")
            
        main_logger.info("Audio resources cleaned up")


app = FastAPI()
realtime_client: Optional[RealtimeClient] = None
client_lock = asyncio.Lock()

parser = argparse.ArgumentParser()
parser.add_argument("--source", choices=["mic", "arecord"], default="mic", help="Audio input source")
parser.add_argument("--bluetooth-source", default=None, help="Specific PulseAudio source name for Bluetooth")
parser.add_argument("--host", default=config.API_HOST)
parser.add_argument("--port", type=int, default=config.API_PORT)
args, _ = parser.parse_known_args()
APP_CONFIG = args

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
    global realtime_client
    
    async with client_lock:
        try:
            instructions = (
                config.SYSTEM_INSTRUCTIONS + 
                f"\n\nThe caller {event.phone_number} is "
                f"{'known' if event.exists_in_contacts else 'unknown'} to the user, "
                f"so most likely it won't be a scam, keep this in context."
            )
            
            if realtime_client is None:
                main_logger.info(f"Starting scam detection for call {event.call_id}")
                
                realtime_client = RealtimeClient(
                    source=APP_CONFIG.source, 
                    instructions=instructions,
                    bluetooth_source=APP_CONFIG.bluetooth_source
                )
                realtime_client.phone_number = event.phone_number
                realtime_client.fcm_token = event.fcm_token
                realtime_client.call_id = event.call_id

                asyncio.create_task(realtime_client.start())
                
                return {
                    "status": "success",
                    "message": "Realtime scam detection started",
                    "call_id": event.call_id
                }
            else:
                main_logger.warning(f"Realtime client already running, updating for call {event.call_id}")
                realtime_client.SYSTEM_INSTRUCTIONS = instructions
                realtime_client.phone_number = event.phone_number
                realtime_client.fcm_token = event.fcm_token
                realtime_client.call_id = event.call_id

                return {
                    "status": "updated",
                    "message": "Realtime client already running, instructions updated",
                    "call_id": event.call_id
                }
                
        except Exception as e:
            log_exception(main_logger, e, "Error starting call")
            return {
                "status": "error",
                "message": str(e)
            }

@app.post("/call/end")
async def call_end(event: CallEndEvent):
    global realtime_client
    
    async with client_lock:
        try:
            if realtime_client:
                main_logger.info(f"Ending call {event.call_id}, duration {event.duration}s")
                
                realtime_client.running = False
                await realtime_client.close_ws()
                realtime_client.cleanup()
                realtime_client = None
                
                return {
                    "status": "success",
                    "message": f"Call ended, duration {event.duration}s",
                    "call_id": event.call_id
                }
            else:
                main_logger.warning("No active call to end")
                return {
                    "status": "warning",
                    "message": "No active call to end"
                }
                
        except Exception as e:
            log_exception(main_logger, e, "Error ending call")
            return {
                "status": "error",
                "message": str(e)
            }

@app.get("/ping")
async def test_route():
    return {"msg": "pong"}

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_call": realtime_client is not None,
        "timestamp": datetime.now().isoformat()
    }

async def shutdown_handler():
    """Cleanup on server shutdown"""
    global realtime_client
    
    main_logger.info("Shutting down server...")
    
    if realtime_client:
        try:
            realtime_client.running = False
            await realtime_client.close_ws()
            realtime_client.cleanup()
        except Exception as e:
            log_exception(main_logger, e, "Error during shutdown cleanup")
    
    main_logger.info("Server shutdown complete")

@app.on_event("shutdown")
async def on_shutdown():
    await shutdown_handler()

if __name__ == "__main__":
    main_logger.info("Starting Scam Detection API Server")
    main_logger.info(f"Audio source: {args.source}")
    if args.bluetooth_source:
        main_logger.info(f"Bluetooth source: {args.bluetooth_source}")
    
    uvicorn.run("main:app", host=args.host, port=args.port, reload=config.API_RELOAD)
