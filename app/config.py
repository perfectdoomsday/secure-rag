from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_env: str = "development"
    app_secret: str = "dev-only-change-me"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "company_knowledge"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    max_upload_mb: int = 15
    max_context_chars: int = 18000
    max_query_chars: int = 2000
    cost_input_per_1m: float = 0.10
    cost_output_per_1m: float = 0.10
    daily_cost_alert_usd: float = 5.0
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
