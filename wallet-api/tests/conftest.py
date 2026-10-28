import os
import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from wallet_api.main import create_app
from wallet_api.settings import Settings

ADMIN_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/postgres"
)
ALICE = "11111111-1111-1111-1111-111111111111"
BOB = "22222222-2222-2222-2222-222222222222"
KID = "test-key"
ISSUER = "test-issuer"
AUDIENCE = "test-audience"

_signing_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key: rsa.RSAPrivateKey = _signing_key) -> dict[str, Any]:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update({"kid": KID, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}


def make_token(
    sub: str,
    *,
    ttl: int = 900,
    audience: str = AUDIENCE,
    issuer: str = ISSUER,
    key: rsa.RSAPrivateKey = _signing_key,
) -> str:
    now = int(time.time())
    claims = {"sub": sub, "iss": issuer, "aud": audience, "iat": now, "exp": now + ttl}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": KID})


def auth(user_id: str, **kwargs: Any) -> dict[str, str]:
    return {"Authorization": f"Bearer {make_token(user_id, **kwargs)}"}


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    schema = f"wallet_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema}"))
    yield f"{ADMIN_URL}?options=-csearch_path%3D{schema}"
    with admin.connect() as conn:
        conn.execute(text(f"DROP SCHEMA {schema} CASCADE"))
    admin.dispose()


@pytest.fixture
def settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        jwks_url="http://unused.invalid/jwks",
        jwt_issuer=ISSUER,
        jwt_audience=AUDIENCE,
        user1_id=ALICE,
        user2_id=BOB,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    reset = create_engine(settings.database_url)
    with reset.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS transfers, contacts, accounts CASCADE"))
    reset.dispose()
    with TestClient(create_app(settings, jwks_fetcher=_jwks)) as c:
        yield c


@pytest.fixture
def run_sql(client: TestClient) -> Callable[[str], Any]:
    def run(statement: str) -> Any:
        with client.app.state.sessionmaker() as session, session.begin():  # type: ignore[attr-defined]
            result = session.execute(text(statement))
            return result.fetchall() if result.returns_rows else None

    return run


def contact_id(client: TestClient, user_id: str, alias: str) -> str:
    contacts = client.get("/accounts/me/contacts", headers=auth(user_id)).json()
    return next(c["id"] for c in contacts if c["alias"] == alias)
