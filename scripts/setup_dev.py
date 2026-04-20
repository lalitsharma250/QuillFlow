"""
scripts/setup_dev.py

Creates initial org, SUPER ADMIN user (with password), and API key.
Works for both local development AND production seeding.

Usage:
    python scripts/setup_dev.py
    
    Then answer prompts:
      - Email: your-email@gmail.com
      - Password: your-strong-password
      - Name: Your Name
"""

import asyncio
import getpass
import hashlib
import re
import secrets
import sys
import uuid

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import ApiKeyRecord, OrganizationRecord, UserRecord
from config import get_settings


def validate_email(email: str) -> bool:
    """Basic email validation."""
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return bool(re.match(pattern, email))


def prompt_user_info() -> dict:
    """Interactively prompt for user details."""
    print("=" * 60)
    print("  QuillFlow Super Admin Setup")
    print("=" * 60)
    print()

    # Organization name
    org_name = input("  Organization name [QuillFlow]: ").strip() or "QuillFlow"

    # Email
    while True:
        email = input("  Admin email: ").strip().lower()
        if validate_email(email):
            break
        print("  ❌ Invalid email format. Try again.")

    # Name
    name = input("  Your full name: ").strip()
    if not name:
        name = "Admin"

    # Password
    while True:
        password = getpass.getpass("  Password (min 8 chars): ")
        if len(password) < 8:
            print("  ❌ Password must be at least 8 characters.")
            continue
        confirm = getpass.getpass("  Confirm password: ")
        if password != confirm:
            print("  ❌ Passwords don't match. Try again.")
            continue
        break

    print()
    return {
        "org_name": org_name,
        "email": email,
        "name": name,
        "password": password,
    }


async def setup():
    settings = get_settings()

    # Show which database we're connecting to
    print()
    print(f"  Database host: {settings.postgres_host}")
    print(f"  Database:      {settings.postgres_db}")
    print()

    # Confirm if it looks like production
    if "neon.tech" in settings.postgres_host or "aws" in settings.postgres_host:
        print("  ⚠️  WARNING: This appears to be a PRODUCTION database!")
        confirm = input("  Are you sure? Type 'yes' to continue: ")
        if confirm.lower() != "yes":
            print("  Aborted.")
            return

    # Get user info interactively
    info = prompt_user_info()

    engine = create_async_engine(settings.postgres_dsn)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # Check if email already exists
        existing = await session.execute(
            select(UserRecord).where(UserRecord.email == info["email"])
        )
        if existing.scalar_one_or_none():
            print(f"  ❌ User with email '{info['email']}' already exists!")
            await engine.dispose()
            return

        # 1. Create organization
        org_id = uuid.uuid4()
        org = OrganizationRecord(
            id=org_id,
            name=info["org_name"],
            is_active=True,
        )
        session.add(org)
        await session.flush()

        # 2. Hash password with bcrypt
        password_hash = bcrypt.hashpw(
            info["password"].encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

        # 3. Create super admin user
        user_id = uuid.uuid4()
        user = UserRecord(
            id=user_id,
            org_id=org_id,
            email=info["email"],
            name=info["name"],
            password_hash=password_hash,
            role="admin",
            is_superadmin=True,  # ← Super admin with org creation rights
            is_active=True,
        )
        session.add(user)
        await session.flush()

        # 4. Create API key
        raw_key = "qf-" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        api_key = ApiKeyRecord(
            id=uuid.uuid4(),
            org_id=org_id,
            user_id=user_id,
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            name="Initial Admin Key",
            is_active=True,
        )
        session.add(api_key)
        await session.commit()

        print()
        print("=" * 60)
        print("  ✅ QuillFlow Super Admin Setup Complete!")
        print("=" * 60)
        print(f"  Organization:  {org.name}")
        print(f"  Org ID:        {org_id}")
        print()
        print(f"  Email:         {info['email']}")
        print(f"  Name:          {info['name']}")
        print(f"  Role:          admin")
        print(f"  Super Admin:   YES (can create/manage organizations)")
        print()
        print("  🔑 API KEY (save this — shown only once):")
        print(f"  {raw_key}")
        print()
        print("  📝 How to use:")
        print("  • Log in via the web UI with your email + password")
        print("  • OR use the API key in requests:")
        print(f'    curl -H "Authorization: Bearer {raw_key}" ...')
        print("=" * 60)

    await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(setup())
    except KeyboardInterrupt:
        print("\n  Cancelled.")
        sys.exit(1)