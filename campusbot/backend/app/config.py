import os
from pathlib import Path
from typing import List
from pydantic_settings import BaseSettings

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    GOOGLE_API_KEY: str = ""
    MODEL_NAME: str = "gemini-1.5-flash"
    
    # Embedding Configuration
    # Options: "gemini" (cloud, 0MB RAM, recommended for Render free tier) or "local" (sentence-transformers)
    EMBEDDING_PROVIDER: str = "gemini"
    EMBEDDING_MODEL: str = "models/text-embedding-004"
    
    # Cosine similarity threshold for RAG retrieval fallback
    SIMILARITY_THRESHOLD: float = 0.45
    
    # Simple admin secret for POST /ingest
    ADMIN_KEY: str = "campusbot-admin-secret"
    
    # CORS: Comma-separated list of allowed origins or "*"
    ALLOWED_ORIGINS: str = "*"
    
    PORT: int = 8000
    
    CHROMA_PERSIST_DIR: str = str(BASE_DIR / "data" / "chroma_db")
    DOCS_DIR: str = str(BASE_DIR / "data" / "docs")
    SQLITE_DB_PATH: str = str(BASE_DIR / "data" / "campusbot.db")

    def get_cors_origins(self) -> List[str]:
        """Parse comma-separated ALLOWED_ORIGINS into a clean list."""
        raw = self.ALLOWED_ORIGINS.strip()
        if raw == "*":
            return ["*"]
        origins = [o.strip() for o in raw.split(",") if o.strip()]
        # Always include local dev origins
        for default_dev in ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"]:
            if default_dev not in origins:
                origins.append(default_dev)
        return origins

    class Config:
        env_file = [
            str(BASE_DIR / ".env"),
            str(BASE_DIR.parent / ".env"),
            ".env"
        ]
        env_file_encoding = "utf-8"
        extra = "ignore"

settings = Settings()
