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


def auth_enforced() -> bool:
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


def authenticate_request(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    x_actor: str | None = Header(default=None, alias="X-Actor"),
) -> AuthContext:
    actor = (x_actor or "anonymous").strip() or "anonymous"
    key_map = _configured_api_keys()
    key = (x_api_key or "").strip()

    if not auth_enforced() and not key:
        # Dev/default mode: no header required, treat as admin for local workflows.
        return AuthContext(actor=actor, role="admin", api_key_present=False)

    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key.",
        )

    role = key_map.get(key)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    return AuthContext(actor=actor, role=role, api_key_present=True)


def require_role(required_role: str) -> Callable[..., AuthContext]:
    required_level = _ROLE_LEVEL.get(required_role)
    if required_level is None:
        raise ValueError(f"Unknown role '{required_role}'.")

    def _dependency(
        x_api_key: str | None = Header(default=None, alias="X-API-Key"),
        x_actor: str | None = Header(default=None, alias="X-Actor"),
    ) -> AuthContext:
        auth = authenticate_request(x_api_key=x_api_key, x_actor=x_actor)
        level = _ROLE_LEVEL.get(auth.role, 0)
        if level < required_level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{auth.role}' cannot access this endpoint. Required role: {required_role}.",
            )
        return auth

    return _dependency
