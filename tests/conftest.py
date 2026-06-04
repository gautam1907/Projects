"""conftest.py — shared pytest fixtures."""
import os
import pytest
import pytest_asyncio

os.environ.setdefault("DATABASE_URL", ":memory:")


@pytest_asyncio.fixture(autouse=True)
async def fresh_db():
    """Re-create a fresh in-memory DB before every test."""
    from app.database import init_db
    await init_db()
    yield
