"""
Centralized logging configuration for Scam Detection System
Provides structured logging with rotation and multiple handlers
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime
import config

os.makedirs(config.LOG_DIR, exist_ok=True)

def setup_logger(name: str, log_file: str = None) -> logging.Logger:
    """
    Setup a logger with both console and file handlers
    
    Args:
        name: Logger name (usually __name__)
        log_file: Optional specific log file name
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(name)
    
    if logger.handlers:
        return logger
    
    logger.setLevel(getattr(logging, config.LOG_LEVEL.upper(), logging.INFO))
    
    formatter = logging.Formatter(
        config.LOG_FORMAT,
        datefmt=config.LOG_DATE_FORMAT
    )
    
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    if log_file is None:
        log_file = f"{name.replace('.', '_')}.log"
    
    file_path = os.path.join(config.LOG_DIR, log_file)
    file_handler = RotatingFileHandler(
        file_path,
        maxBytes=config.LOG_MAX_BYTES,
        backupCount=config.LOG_BACKUP_COUNT
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    return logger

main_logger = setup_logger("main", "main.log")
bluetooth_logger = setup_logger("bluetooth_bridge", "bluetooth_bridge.log")
db_logger = setup_logger("database", "database.log")

def log_exception(logger: logging.Logger, e: Exception, context: str = ""):
    """
    Log exception with context information
    
    Args:
        logger: Logger instance
        e: Exception object
        context: Additional context about where the exception occurred
    """
    if context:
        logger.error(f"{context}: {type(e).__name__}: {str(e)}", exc_info=True)
    else:
        logger.error(f"{type(e).__name__}: {str(e)}", exc_info=True)
