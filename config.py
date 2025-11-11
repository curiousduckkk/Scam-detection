"""
Configuration management for Scam Detection System
Centralizes all configuration with environment variable support
"""
import os
from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MONGO_URI = os.getenv("MONGO_URI")

OPENAI_REALTIME_URI = "wss://api.openai.com/v1/realtime?model=gpt-realtime"
OPENAI_MODEL = "gpt-realtime"

AUDIO_CHUNK_SIZE = 1024
AUDIO_FORMAT = "paInt16"
AUDIO_CHANNELS = 1
AUDIO_RATE = 24000
AUDIO_INPUT_FORMAT = "pcm16"
AUDIO_OUTPUT_FORMAT = "pcm16"

BT_AUDIO_RATE = 8000
BT_AUDIO_FORMAT = "S16_LE"
BT_AUDIO_CHANNELS = 1

PHONE_MAC = os.getenv("PHONE_MAC", "30:BB:7D:48:29:BC")
EARBUDS_MAC = os.getenv("EARBUDS_MAC", "8C:64:A2:33:E8:D8")
HCI0_MAC = os.getenv("HCI0_MAC", "2C:CF:67:0A:17:6C")  # Onboard adapter
HCI1_MAC = os.getenv("HCI1_MAC", "0C:EF:15:43:05:8A")  # USB adapter

RECORDING_DIR = os.getenv("RECORDING_DIR", "/home/eshita")
PIPE_PATH = os.getenv("PIPE_PATH", "/tmp/downlink_tap")

MONGO_DB_NAME = "scam_detection"
MONGO_COLLECTION_CALLS = "calls"

FIREBASE_CONFIG_PATH = os.getenv("FIREBASE_CONFIG_PATH", "other/config.json")

API_HOST = "0.0.0.0"
API_PORT = 8000
API_RELOAD = True

SCAM_SCORE_SAFE_MAX = 3
SCAM_SCORE_POSSIBLE_MIN = 4
SCAM_SCORE_POSSIBLE_MAX = 7
SCAM_SCORE_DEFINITE_MIN = 8

NOTIFICATION_THRESHOLD = 4

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

WS_MAX_RETRIES = 5
WS_RETRY_BASE_DELAY = 2.0
WS_RETRY_MAX_DELAY = 60.0
WS_CONNECTION_TIMEOUT = 30.0
WS_PING_INTERVAL = 20
WS_PING_TIMEOUT = 20

AUDIO_SEND_TIMEOUT = 5.0
AUDIO_READ_TIMEOUT = 2.0
DB_OPERATION_TIMEOUT = 10.0
NOTIFICATION_TIMEOUT = 10.0
COMMAND_TIMEOUT = 10.0

AUDIO_QUEUE_MAX_SIZE = 100

HEALTH_CHECK_INTERVAL = 30
AUDIO_SILENCE_THRESHOLD = 60

BT_CONNECTION_TIMEOUT = 15
BT_CONNECTION_RETRIES = 3
BT_VERIFY_DELAY = 3

PROCESS_CLEANUP_TIMEOUT = 5
PROCESS_KILL_TIMEOUT = 2

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DIR = os.getenv("LOG_DIR", "/home/eshita/Scam-detection/logs")
LOG_MAX_BYTES = 10 * 1024 * 1024  #10MB
LOG_BACKUP_COUNT = 5
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

COLOR_RED = '\033[0;31m'
COLOR_GREEN = '\033[0;32m'
COLOR_YELLOW = '\033[1;33m'
COLOR_BLUE = '\033[0;34m'
COLOR_NC = '\033[0m'

DEFAULT_USER_UUID = "user-1234"

def validate_config():
    """Validate critical configuration values"""
    errors = []
    
    if not OPENAI_API_KEY:
        errors.append("OPENAI_API_KEY is not set")
    
    if not MONGO_URI:
        errors.append("MONGO_URI is not set")
    
    if not os.path.exists(RECORDING_DIR):
        errors.append(f"RECORDING_DIR does not exist: {RECORDING_DIR}")
    
    if errors:
        raise ValueError(f"Configuration errors: {', '.join(errors)}")
    
    return True

if os.getenv("SKIP_CONFIG_VALIDATION") != "1":
    try:
        validate_config()
    except ValueError as e:
        print(f"Warning: {e}")
