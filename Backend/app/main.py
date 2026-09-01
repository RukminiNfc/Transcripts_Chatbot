from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routers import search, chat, requirements, subscriptions, auth
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
    title="Requirement Tracking API",
    description="API for Document Requirement Tracking and Chat",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174"],
    allow_origin_regex=r"http://localhost:\d+",   # dev: allow any localhost port (Vite)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(requirements.router)
app.include_router(chat.router)
app.include_router(search.router)
app.include_router(subscriptions.router, prefix="/api/subscriptions", tags=["Subscriptions"])
app.include_router(auth.router)   # /api/auth/login, /me, /register

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    logger.info("Starting application...")
    await init_db()
    logger.info("Database initialized")

@app.get("/")
def root():
    """Root endpoint"""
    return {
        "message": "RAG API",
        "version": "1.0.0",
        "status": "running"
    }

@app.get("/health")
def health_check():
    """Health check endpoint"""
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)