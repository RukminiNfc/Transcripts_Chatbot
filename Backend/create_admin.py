"""One-time script to create the FIRST admin user.

Needed because POST /api/auth/register is admin-only, so there must be at least one admin
to bootstrap from. After running this once, create everyone else via Swagger (log in as this
admin -> Authorize -> /api/auth/register).

Usage (run in the Backend folder, venv active):
    python create_admin.py --username admin
        -> prompts securely for a password

    python create_admin.py --username admin --password "your-strong-password"
        -> non-interactive
"""
import argparse
import asyncio
import getpass

from sqlalchemy.future import select

from app.core.database import AsyncSessionLocal, engine, init_db
from app.core.security import hash_password
from app.models.database import User


async def create_admin(username: str, password: str) -> None:
    await init_db()  # make sure the users table exists (idempotent)
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(select(User).where(User.username == username))).scalars().first()
        if existing:
            print(f"User '{username}' already exists (role={existing.role}). Nothing changed.")
        else:
            db.add(User(username=username, hashed_password=hash_password(password), role="admin"))
            await db.commit()
            print(f"Admin user '{username}' created successfully.")
    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first admin user.")
    parser.add_argument("--username", required=True, help="Admin username.")
    parser.add_argument("--password", help="If omitted, you'll be prompted securely.")
    args = parser.parse_args()

    password = args.password or getpass.getpass("Password for admin: ")
    if not password.strip():
        raise SystemExit("Password cannot be empty.")

    asyncio.run(create_admin(args.username, password))


if __name__ == "__main__":
    main()
