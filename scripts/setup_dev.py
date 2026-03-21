"""
scripts/setup_dev.py

Creates initial org, admin user, and API key for local development.
Run once after database migration.
"""

import asyncio
import hashlib
import secrets
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from config import get_settings
from app.db.models import OrganizationRecord, UserRecord, ApiKeyRecord


async def setup():
    settings = get_settings()
    engine = create_async_engine(settings.postgres_dsn)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        # 1. Create organization
        org_id = uuid.uuid4()
        org = OrganizationRecord(
            id=org_id,
            name="QuillFlow Dev",
        )
        session.add(org)
        await session.flush()

        # 2. Create admin user
        user_id = uuid.uuid4()
        user = UserRecord(
            id=user_id,
            org_id=org_id,
            email="lalit250603@gmail.com",
            name="Lalit", 
            role="admin",
            is_active=True,
        )
        session.add(user)
        await session.flush()

        # 3. Create API key
        raw_key = "qf-" + secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        api_key = ApiKeyRecord(
            id=uuid.uuid4(),
            org_id=org_id,
            user_id=user_id,
            key_hash=key_hash,
            key_prefix=raw_key[:8],
            name="Development Key",
            is_active=True,
        )
        session.add(api_key)
        await session.commit()

        print("=" * 60)
        print("  QuillFlow Dev Setup Complete")
        print("=" * 60)
        print(f"  Organization: {org.name}")
        print(f"  Org ID:       {org_id}")
        print(f"  User:         {user.email}")
        print(f"  User ID:      {user_id}")
        print(f"  Role:         admin")
        print()
        print(f"  *** API KEY (SAVE THIS — shown only once): ***")
        print(f"  {raw_key}")
        print()
        print("  Use it in requests:")
        print(f'  curl -H "Authorization: Bearer {raw_key}" ...')
        print("=" * 60)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(setup())