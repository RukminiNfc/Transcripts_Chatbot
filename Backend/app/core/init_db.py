from app.core.database import init_db
from app.core.config import settings
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    """Initialize database"""
    logger.info("Creating database tables...")
    init_db()
    logger.info("Database initialization complete!")

if __name__ == "__main__":
    main()