from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    llm_provider: str = "google_genai"
    llm_model: str = "gemini-3.5-flash"
    llm_max_retries: int = 6

    wallet_api_url: str
    jwks_url: str
    jwt_issuer: str = "wallet-idp"
    jwt_audience: str = "wallet-api"
    jwks_cache_ttl_seconds: int = 300
    http_timeout_seconds: float = 10.0

    # Without a database the graph checkpoints in memory: state is lost on restart.
    database_url: str | None = None

    log_level: str = "INFO"
