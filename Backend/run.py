import uvicorn

from app.core.config import settings

if __name__ == "__main__":
    # PORT and RELOAD come from Backend/.env (defaults: 8001, True — local development).
    # The server sets PORT=8003 and RELOAD=False: auto-reload there would restart the API
    # mid-deploy while files are still being updated.
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.PORT,
        reload=settings.RELOAD,
    )
