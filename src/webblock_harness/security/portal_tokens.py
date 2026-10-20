"""Business Portal sign-in tokens.

WebBlock signs short-lived Ed25519 JWTs for the owner of a site; each generated site verifies them with
WebBlock's *public* key (committed to its repository in ``webblock/site.json``). No shared secret ever
leaves the harness, and a token is only valid for the one site (``aud``) it was issued for.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from webblock_harness.config import Settings

ISSUER = "webblock"
SCOPE = "business_portal"
TTL_SECONDS = 8 * 3600


def audience(site_tenant_id: str) -> str:
    return f"site:{site_tenant_id}"


@dataclass(frozen=True)
class PortalSigner:
    private_key: Ed25519PrivateKey

    @property
    def public_key_pem(self) -> str:
        return (
            self.private_key.public_key()
            .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
            .decode()
        )

    def issue(self, site_tenant_id: str, user_id: str, ttl: int = TTL_SECONDS) -> tuple[str, int]:
        now = int(time.time())
        exp = now + ttl
        token = jwt.encode(
            {"iss": ISSUER, "aud": audience(site_tenant_id), "sub": user_id, "scope": SCOPE, "iat": now, "exp": exp},
            self.private_key,
            algorithm="EdDSA",
        )
        return token, exp

    def verify(self, token: str, site_tenant_id: str) -> dict[str, object]:
        """Mirror of the generated site's check (used in tests)."""
        public = self.private_key.public_key()
        return jwt.decode(token, public, algorithms=["EdDSA"], audience=audience(site_tenant_id), issuer=ISSUER)  # type: ignore[no-any-return]


def load_signer(settings: Settings) -> PortalSigner:
    """PORTAL_SIGNING_KEY (PEM) in production; otherwise a key generated once and kept under var/ for development."""
    if settings.portal_signing_key is not None:
        pem = settings.portal_signing_key.get_secret_value().replace("\\n", "\n").encode()
        key = serialization.load_pem_private_key(pem, password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("PORTAL_SIGNING_KEY must be an Ed25519 private key (PEM).")
        return PortalSigner(key)
    path = Path(settings.portal_key_file)
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        assert isinstance(key, Ed25519PrivateKey)
        return PortalSigner(key)
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return PortalSigner(key)
