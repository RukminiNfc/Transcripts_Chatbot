from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    """Application settings"""
    
    # Database
    DATABASE_URL: str
    
    # Qdrant
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_COLLECTION_NAME: str = "vector_documents"
    
    # OpenAI
    OPENAI_API_KEY: str
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_LLM_MODEL: str = "gpt-5.4-mini"            # Chat answer generation
    OPENAI_EXTRACTION_MODEL: str = "gpt-5.5"         # Hard reasoning: notes + completeness review
    OPENAI_FORMAT_MODEL: str = "gpt-5.4-mini"        # Mechanical notes → JSON conversion
    OPENAI_COMPARISON_MODEL: str = "gpt-5.5"    # Meaning-based change detection
    OPENAI_RESOLVER_MODEL: str = "gpt-5.5"           # Context resolver: understand/route/rewrite questions

    # Cohere (purpose-built re-ranker for retrieval)
    COHERE_API_KEY: str = ""
    COHERE_RERANK_MODEL: str = "rerank-v3.5"

    # Retrieval tuning (all configurable — tune against the eval set, do NOT hardcode elsewhere)
    RETRIEVAL_CANDIDATE_POOL: int = 60      # vector-search candidates handed to the re-ranker (recall)
    RETRIEVAL_TOP_N_NARROW: int = 8         # chunks kept for a NARROW / specific question
    RETRIEVAL_TOP_N_BROAD: int = 30         # chunks kept for a BROAD topic / compare / timeline / list — raised 20->30 so detailed answers see more of a big topic's evidence; tune against the eval set
    RERANK_SCORE_THRESHOLD: float = 0.1     # drop Cohere chunks below this relevance (0..1); conservative start
    RERANK_COMPLETE_THRESHOLD: float = 0.3  # cutoff for "all requirements on X" (complete) mode — TUNE on real queries: raise if other features mix in, lower if it misses some
    RERANK_CHANGES_THRESHOLD: float = 0.3   # cutoff for topic-scoped CHANGES/diff answers ("what changed for X") — TUNE like the complete-mode cutoff: raise if other features mix in, lower if real changes are missed
    RERANK_COMPLETE_GATHER_THRESHOLD: float = 0.1  # complete mode STAGE-1 gather cutoff (generous, recall only) — the LLM confirm stage decides final membership; RERANK_COMPLETE_THRESHOLD stays as the fallback when that stage fails

    # Requirement Extraction
    EXTRACTION_CHUNK_SIZE: int = 60       # Max transcript blocks per extraction chunk
    EXTRACTION_CHUNK_OVERLAP: int = 10    # Overlapping blocks between chunks to avoid missing context
    EXTRACTION_PROMPT_FILE: str = "requirement_extraction_prompt.txt"  # Prompt file name inside app/prompts/
    
    # Application
    APP_ENV: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"

    # SMTP / Email Notification Settings.
    # Change notifications are currently disabled in worker/tasks.py; this block stays so the
    # feature can be restored by uncommenting there. All values must come from .env — no default
    # sender address, so a misconfigured deployment fails loudly instead of mailing from someone's
    # personal account.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""

    # Authentication (JWT). Set JWT_SECRET_KEY to a long random string in .env for production.
    JWT_SECRET_KEY: str = "CHANGE_ME_TO_A_LONG_RANDOM_SECRET_IN_ENV"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480   # token valid for 8 hours

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance"""
    return Settings()

# Create global settings instance
settings = get_settings()