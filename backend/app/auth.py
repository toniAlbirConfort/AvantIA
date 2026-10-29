from __future__ import annotations

from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User

hasher = PasswordHasher()
bearer = HTTPBearer(auto_error=False)


def _signer() -> URLSafeTimedSerializer:
    secret = get_settings().app_secret
    if len(secret) < 32:
        raise RuntimeError("APP_SECRET must contain at least 32 characters")
    return URLSafeTimedSerializer(secret, salt="avantia-login-v1")


def issue_token(user: User) -> str:
    return _signer().dumps({"user_id": user.id})


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        return hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(401, "Autenticación requerida")
    try:
        payload = _signer().loads(credentials.credentials, max_age=8 * 3600)
        user_id = int(payload["user_id"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(401, "Sesión no válida") from exc
    user = db.get(User, user_id)
    if user is None or not user.active:
        raise HTTPException(401, "Usuario no disponible")
    return user


def admin_user(user: Annotated[User, Depends(current_user)]) -> User:
    if user.role != "admin":
        raise HTTPException(403, "Se requiere administrador")
    return user


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username))
    if user and user.active and verify_password(password, user.password_hash):
        return user
    return None
