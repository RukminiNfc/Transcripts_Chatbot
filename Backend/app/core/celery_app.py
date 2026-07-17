from celery import Celery
import os
from app.core.config import settings

# In a production environment, REDIS_URL should be defined in .env
# We fallback to the standard localhost port which Memurai uses.
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "transcription_worker",
    broker=redis_url,
    backend=redis_url,
    include=['app.worker.tasks'] # Tell celery where to find tasks
)

celery_app.conf.update(
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    # Make sure tasks are properly tracked
    task_track_started=True,
)
