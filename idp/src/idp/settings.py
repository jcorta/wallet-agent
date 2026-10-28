from pydantic import BaseModel
from pydantic_settings import BaseSettings


class User(BaseModel):
    username: str
    password: str
    user_id: str


class Settings(BaseSettings):
    jwt_issuer: str = "wallet-idp"
    jwt_audience: str = "wallet-api"
    token_ttl_seconds: int = 900
    key_path: str = "/data/private_key.pem"
    log_level: str = "INFO"

    user1_username: str
    user1_password: str
    user1_id: str
    user2_username: str
    user2_password: str
    user2_id: str

    @property
    def users(self) -> list[User]:
        return [
            User(username=self.user1_username, password=self.user1_password, user_id=self.user1_id),
            User(username=self.user2_username, password=self.user2_password, user_id=self.user2_id),
        ]
