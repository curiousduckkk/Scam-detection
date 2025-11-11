import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from datetime import datetime, timezone
from typing import Optional
import config
from logger import db_logger, log_exception

mongo_client = AsyncIOMotorClient(
    config.MONGO_URI,
    maxPoolSize=10,
    minPoolSize=1,
    serverSelectionTimeoutMS=5000,
    connectTimeoutMS=5000,
    socketTimeoutMS=5000,
)
db = mongo_client[config.MONGO_DB_NAME]
calls_collection = db[config.MONGO_COLLECTION_CALLS]


async def save_call_data(
    user_uuid: str, 
    scam_score: int, 
    phone_number: str, 
    call_id: str
) -> Optional[any]:
    """
    Save each call as a separate document in the 'calls' collection.
    Includes timeout handling and retry logic.

    Args:
        user_uuid: User UUID
        scam_score: Scam score (1-10)
        phone_number: Other person's phone number
        call_id: Unique call identifier
        
    Returns:
        Insert result or None on failure
    """
    if not user_uuid or not call_id:
        db_logger.error("Invalid input: user_uuid and call_id are required")
        return None
    
    if not isinstance(scam_score, int) or not (1 <= scam_score <= 10):
        db_logger.error(f"Invalid scam_score: {scam_score}. Must be integer 1-10")
        return None

    call_doc = {
        "user_uuid": user_uuid,
        "call_id": call_id,
        "phone_number": phone_number,
        "scam_score": scam_score,
        "timestamp": datetime.now(timezone.utc),
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            result = await asyncio.wait_for(
                calls_collection.insert_one(call_doc),
                timeout=config.DB_OPERATION_TIMEOUT
            )
            
            db_logger.info(
                f"Saved call record: user={user_uuid}, call_id={call_id}, "
                f"score={scam_score}, time={call_doc['timestamp']}"
            )
            return result
            
        except asyncio.TimeoutError:
            db_logger.warning(
                f"Database operation timeout (attempt {attempt + 1}/{max_retries})"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                db_logger.error("Max retries exceeded for database operation")
                return None
                
        except Exception as e:
            log_exception(
                db_logger, 
                e, 
                f"Error saving call data (attempt {attempt + 1}/{max_retries})"
            )
            if attempt < max_retries - 1:
                await asyncio.sleep(2 ** attempt)
            else:
                return None
    
    return None


async def test_connection() -> bool:
    """
    Test MongoDB connection health
    
    Returns:
        True if connection is healthy, False otherwise
    """
    try:
        await asyncio.wait_for(
            mongo_client.admin.command('ping'),
            timeout=5.0
        )
        db_logger.info("MongoDB connection healthy")
        return True
    except Exception as e:
        log_exception(db_logger, e, "MongoDB connection test failed")
        return False


async def close_connection():
    """Gracefully close MongoDB connection"""
    try:
        mongo_client.close()
        db_logger.info("MongoDB connection closed")
    except Exception as e:
        log_exception(db_logger, e, "Error closing MongoDB connection")
