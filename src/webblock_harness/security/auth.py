"""Server-side identity.

The harness never trusts a tenant identifier supplied in a request body, path or
header. The tenant is taken exclusively from a signed JWT issued by the WebBlock
authentication service (the Spring Boot gateway). Forwarding alone is not
treated as authentication: every request must carry a token whose signature,
expiry, issuer and audience verify here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import jwt

from webblock_harness.config import Settings


class Role(StrEnum):
    VIEWER = "viewer"  # read project, jobs, snapshots
    EDITOR = "editor"  # + edit drafts, create change jobs
    OWNER = "owner"  # + approve high-impact plans, rollback
    ADMIN = "admin"  # + register repositories


_RANK = {Role.VIEWER: 0, Role.EDITOR: 1, Role.OWNER: 2, Role.ADMIN: 3}


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    roles: frozenset[Role] = field(default_factory=frozenset)

    def has(self, role: Role) -> bool:
        return any(_RANK[r] >= _RANK[role] for r in self.roles)


class TokenVerifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        if settings.auth_jwt_public_key:
            self._key: Any = settings.auth_jwt_public_key
        elif settings.auth_jwt_secret is not None:
            self._key = settings.auth_jwt_secret.get_secret_value()
        else:  # pragma: no cover - guarded by Settings validation
            raise AuthError("No JWT verification key configured.")

    def verify(self, token: str) -> Principal:
        s = self._settings
        options = {"require": ["exp", "sub"], "verify_aud": s.auth_jwt_audience is not None}
        try:
            claims = jwt.decode(
                token,
                self._key,
                algorithms=s.auth_jwt_algorithms,
                issuer=s.auth_jwt_issuer,
                audience=s.auth_jwt_audience,
                options=options,  # type: ignore[arg-type]
                leeway=30,
            )
        except jwt.PyJWTError as exc:
            raise AuthError(f"Invalid token: {exc.__class__.__name__}") from exc

        tenant = claims.get(s.auth_tenant_claim)
        if not isinstance(tenant, str) or not tenant:
            raise AuthError("Token has no tenant claim.")
        raw_roles = claims.get(s.auth_roles_claim) or []
        if isinstance(raw_roles, str):
            raw_roles = [raw_roles]
        roles = frozenset(Role(r) for r in raw_roles if r in Role._value2member_map_)
        if not roles:
            roles = frozenset({Role.VIEWER})
        return Principal(user_id=str(claims["sub"]), tenant_id=tenant, roles=roles)
