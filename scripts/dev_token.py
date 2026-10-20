"""Mint a short-lived development JWT signed with AUTH_JWT_SECRET (HS256).

Production tokens are issued by the WebBlock auth service; this exists only so the
API can be exercised locally.

    uv run python scripts/dev_token.py --tenant tenant-a --roles admin --user dev-1
"""

from __future__ import annotations

import argparse
import time

import jwt

from webblock_harness.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", required=True)
    parser.add_argument("--user", default="dev-user")
    parser.add_argument("--roles", default="editor", help="comma-separated: viewer,editor,owner,admin")
    parser.add_argument("--ttl", type=int, default=3600)
    args = parser.parse_args()
    s = get_settings()
    if s.auth_jwt_secret is None:
        raise SystemExit("AUTH_JWT_SECRET is not set (RS*/ES* deployments must use the real auth service).")
    claims: dict[str, object] = {
        "sub": args.user,
        s.auth_tenant_claim: args.tenant,
        s.auth_roles_claim: args.roles.split(","),
        "exp": int(time.time()) + args.ttl,
    }
    if s.auth_jwt_issuer:
        claims["iss"] = s.auth_jwt_issuer
    if s.auth_jwt_audience:
        claims["aud"] = s.auth_jwt_audience
    print(jwt.encode(claims, s.auth_jwt_secret.get_secret_value(), algorithm=s.auth_jwt_algorithms[0]))


if __name__ == "__main__":
    main()
