"""Seed the deterministic Hub row for the Compose E2E topology."""

from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from nfm_db.models import HubNode

HUB_NODE_ID = uuid.UUID(os.environ.get("HUB_NODE_ID", "b1000000-0000-0000-0000-000000000001"))


async def main() -> None:
    database_url = os.environ["NFM_DATABASE_URL"]
    engine = create_async_engine(database_url)
    sessions = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as session:
        existing = await session.scalar(select(HubNode).where(HubNode.id == HUB_NODE_ID))
        if existing is None:
            session.add(
                HubNode(
                    id=HUB_NODE_ID,
                    name="e2e-hub",
                    api_endpoint="http://hub:8000",
                    status="active",
                )
            )
            await session.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
