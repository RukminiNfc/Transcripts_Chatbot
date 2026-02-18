from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import documents, search, chat, metadata
from app.core.database import init_db
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title="INTA RAG API",
    description="API for INTA document search and Q&A",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],  # React frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(documents.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(metadata.router)

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    logger.info("Starting application...")
    init_db()
    logger.info("Database initialized")

@app.get("/")
def root():
    """Root endpoint"""
    return {
        "message": "INTA RAG API",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)