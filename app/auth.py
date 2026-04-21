from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable

from fastapi import Header, HTTPException, status

_ROLE_LEVEL = {
    "viewer": 1,
    "operator": 2,
    "admin": 3,
}


@dataclass
class AuthContext:
    actor: str
    role: str
    api_key_present: bool


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def standalone_mode() -> bool:
    return _as_bool(os.getenv("RESINLOGIC_STANDALONE_MODE"), default=True)


def auth_enforced() -> bool:
    if standalone_mode():
        return False
    return _as_bool(os.getenv("RESINLOGIC_ENFORCE_AUTH"), default=False)


def _configured_api_keys() -> dict[str, str]:
    keys: dict[str, str] = {}
    admin = os.getenv("RESINLOGIC_ADMIN_API_KEY")
    operator = os.getenv("RESINLOGIC_OPERATOR_API_KEY")
    viewer = os.getenv("RESINLOGIC_VIEWER_API_KEY")
    if admin:
        keys[admin] = "admin"
    if operator:
        keys[operator] = "operator"
    if viewer:
        keys[viewer] = "viewer"
    if keys:
        return keys

    if _as_bool(os.getenv("RESINLOGIC_ENABLE_DEFAULT_KEYS"), default=True):
        return {
            "dev-admin-key": "admin",
            "dev-operator-key": "operator",
            "dev-viewer-key": "viewer",
        }
    return {}


def _extract_token(authorization: str | None) -> str:
    value = (authorization or "").strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered.startswith("bearer "):
        return value[7:].strip()
    if lowered.startswith("token "):
        return value[6:].strip()
    return value


def authenticate_request(
    authorization: str | None = Header(default=None, alias="Authorization"),
    actor_header: str | None = Header(default=None, alias="Actor"),
) -> AuthContext:
    default_actor = os.getenv("USER") or os.getenv("USERNAME") or "local-user"
    actor = (actor_header or default_actor).strip() or default_actor
    key_map = _configured_api_keys()
    key = _extract_token(authorization)

    if not auth_enforced():
        # Standalone/local mode: no auth required, always treat as admin.
        return AuthContext(actor=actor, role="admin", api_key_present=bool(key))

    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header. Use 'Authorization: Bearer <token>'.",
        )

    role = key_map.get(key)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authorization token.",
        )
    return AuthContext(actor=actor, role=role, api_key_present=True)


def require_role(required_role: str) -> Callable[..., AuthContext]:
    required_level = _ROLE_LEVEL.get(required_role)
    if required_level is None:
        raise ValueError(f"Unknown role '{required_role}'.")

    def _dependency(
        authorization: str | None = Header(default=None, alias="Authorization"),
        actor_header: str | None = Header(default=None, alias="Actor"),
    ) -> AuthContext:
        auth = authenticate_request(authorization=authorization, actor_header=actor_header)
        level = _ROLE_LEVEL.get(auth.role, 0)
        if level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{auth.role}' cannot access this endpoint. Required role: {required_role}.",
            )
        return auth

    return _dependency
