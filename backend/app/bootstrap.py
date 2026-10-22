from __future__ import annotations

from sqlalchemy import select

from app.auth import hasher
from app.config import get_settings
from app.db import SessionLocal
from app.models import User


def main() -> None:
    settings = get_settings()
    if not settings.admin_username or not settings.admin_password:
        raise RuntimeError("Set ADMIN_USERNAME and ADMIN_PASSWORD")
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.username == settings.admin_username)):
            return
        db.add(
            User(
                username=settings.admin_username,
                password_hash=hasher.hash(settings.admin_password),
                role="admin",
            )
        )
        db.commit()


if __name__ == "__main__":
    main()
