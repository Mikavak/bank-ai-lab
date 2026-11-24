import pytest
from sqlalchemy import text

from bank_ai.infrastructure.database.session import get_engine


@pytest.mark.asyncio
async def test_postgres_connection():
    engine = get_engine()

    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))

        assert result.scalar_one() == 1


@pytest.mark.asyncio
async def test_pgvector_extension():
    engine = get_engine()

    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT extname FROM pg_extension WHERE extname = 'vector'")
        )

        assert result.scalar_one() == "vector"
