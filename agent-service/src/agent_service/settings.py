from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_provider: str = "google_genai"
    llm_model: str = "gemini-3.1-flash-lite"
    llm_max_retries: int = 6

    wallet_api_url: str
    jwks_url: str
    jwt_issuer: str = "wallet-idp"
    jwt_audience: str = "wallet-api"
    jwks_cache_ttl_seconds: int = 300
    http_timeout_seconds: float = 10.0

    # Without a database the graph checkpoints in memory: state is lost on restart.
    database_url: str | None = None

    embeddings_provider: str = "google_genai"
    embeddings_model: str = "gemini-embedding-001"
    knowledge_dir: str = "knowledge"

    log_level: str = "INFO"
