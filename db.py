import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from datetime import datetime, timezone

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI")
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["scam_detection"]

calls_collection = db["calls"]  # Each call = one document


# ---------------- Calls ----------------
async def save_call_data(user_uuid: str, scam_score: int, phone_number: str, call_id: str):
    """
    Save each call as a separate document in the 'calls' collection.

    Args:
        user_uuid: User UUID
        scam_score: Scam score (1–10)
        phone_number: Other person's phone number
        call_id: Unique call identifier
    """

    call_doc = {
        "user_uuid": user_uuid,
        "call_id": call_id,
        "phone_number": phone_number,
        "scam_score": scam_score,
        "timestamp": datetime.now(timezone.utc),  # precise UTC timestamp
    }

    result = await calls_collection.insert_one(call_doc)
    print(f"✓ Saved call record: user={user_uuid}, call_id={call_id}, time={call_doc['timestamp']}")
    return result
