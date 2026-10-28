from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    jwks_url: str
    jwt_issuer: str = "wallet-idp"
    jwt_audience: str = "wallet-api"
    jwks_cache_ttl_seconds: int = 300
    http_timeout_seconds: float = 5.0
    timezone: str = "America/Argentina/Buenos_Aires"
    log_level: str = "INFO"

    user1_id: str
    user2_id: str
