import logging
import threading
import time
from collections.abc import Callable
from typing import Annotated, Any

import httpx
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agent_service.errors import ApiError
from agent_service.settings import Settings

logger = logging.getLogger(__name__)

# Unknown key ids trigger a refetch; this bounds how often a forged `kid` can do so.
_MIN_REFRESH_INTERVAL_SECONDS = 10.0


def http_jwks_fetcher(url: str, timeout: float) -> Callable[[], dict[str, Any]]:
    def fetch() -> dict[str, Any]:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
        return response.json()

    return fetch


class JwksCache:
    def __init__(self, fetch: Callable[[], dict[str, Any]], ttl_seconds: float) -> None:
        self._fetch = fetch
        self._ttl = ttl_seconds
        self._keys: dict[str, Any] = {}
        self._fetched_at: float | None = None
        self._lock = threading.Lock()

    def get_key(self, kid: str) -> Any:
        with self._lock:
            now = time.monotonic()
            expired = self._fetched_at is None or now - self._fetched_at > self._ttl
            can_refresh = (
                self._fetched_at is None or now - self._fetched_at > _MIN_REFRESH_INTERVAL_SECONDS
            )
            if expired or (kid not in self._keys and can_refresh):
                self._refresh()
            return self._keys.get(kid)

    def _refresh(self) -> None:
        try:
            document = self._fetch()
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("jwks fetch failed", extra={"fields": {"error": type(exc).__name__}})
            raise ApiError(
                503, "identity_provider_unavailable", "Cannot verify credentials right now"
            ) from exc
        self._keys = {jwk["kid"]: jwt.PyJWK.from_dict(jwk).key for jwk in document["keys"]}
        self._fetched_at = time.monotonic()


_bearer = HTTPBearer(auto_error=False)


def current_user_id(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> str:
    """The only source of the caller's identity: the verified `sub` claim."""
    if credentials is None:
        raise ApiError(401, "missing_token", "Authentication required")
    settings: Settings = request.app.state.settings
    jwks: JwksCache = request.app.state.jwks
    token = credentials.credentials
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        key = jwks.get_key(kid) if kid else None
        if key is None:
            raise ApiError(401, "invalid_token", "Invalid or expired token")
        claims = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise ApiError(401, "invalid_token", "Invalid or expired token") from exc
    subject = claims["sub"]
    if not isinstance(subject, str) or not subject:
        raise ApiError(401, "invalid_token", "Invalid or expired token")
    return subject
