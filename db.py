import os
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI")
mongo_client = AsyncIOMotorClient(MONGO_URI)
db = mongo_client["scam_detection"]

users_collection = db["users"]  # Users collection

# ---------------- Users and Calls ----------------
async def save_call_data(user_uuid: str, scam_score: int, phone_number: str, call_id: str):
    """
    Save call data under the given user UUID.

    Args:
        user_uuid: User UUID (manual for now)
        scam_score: Scam score (1-10)
        phone_number: Other person's phone number
        call_id: Unique call identifier
    """
    call_doc = {
        "call_id": call_id,
        "phone_number": phone_number,
        "scam_score": scam_score
    }

    # Check if user document exists
    user = await users_collection.find_one({"user_uuid": user_uuid})
    if user:
        # Append call to existing user
        result = await users_collection.update_one(
            {"user_uuid": user_uuid},
            {"$push": {"calls": call_doc}}
        )
    else:
        # Create new user document with first call
        result = await users_collection.insert_one({
            "user_uuid": user_uuid,
            "calls": [call_doc]
        })

    print(f"✓ Saved call data for user {user_uuid}, call_id {call_id}")
    return result
