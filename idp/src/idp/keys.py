import hashlib
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


class KeyStore:
    """RSA signing key, generated on first start and reused from disk afterwards."""

    def __init__(self, path: str) -> None:
        self._private_key = self._load_or_create(Path(path))
        public_der = self._private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        self.kid = hashlib.sha256(public_der).hexdigest()[:16]

    @staticmethod
    def _load_or_create(path: Path) -> RSAPrivateKey:
        if path.exists():
            key = serialization.load_pem_private_key(path.read_bytes(), password=None)
            if not isinstance(key, RSAPrivateKey):
                raise ValueError(f"{path} does not contain an RSA private key")
            return key
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        path.chmod(0o600)
        return key

    def sign(self, claims: dict[str, Any]) -> str:
        return jwt.encode(claims, self._private_key, algorithm="RS256", headers={"kid": self.kid})

    def jwks(self) -> dict[str, list[dict[str, Any]]]:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self._private_key.public_key(), as_dict=True)
        jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return {"keys": [jwk]}
