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
    OPENAI_COMPARISON_MODEL: str = "gpt-5.4-mini"    # Meaning-based change detection

    # Requirement Extraction
    EXTRACTION_CHUNK_SIZE: int = 60       # Max transcript blocks per extraction chunk
    EXTRACTION_CHUNK_OVERLAP: int = 10    # Overlapping blocks between chunks to avoid missing context
    EXTRACTION_PROMPT_FILE: str = "requirement_extraction_prompt.txt"  # Prompt file name inside app/prompts/
    
    # Application
    APP_ENV: str = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    
    # Chunking
    CHUNK_SIZE: int = 800
    CHUNK_OVERLAP: int = 100
    
    # SMTP / Email Notification Settings
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "rukminisowrothu3@gmail.com"
    
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