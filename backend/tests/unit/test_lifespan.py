from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI

from bank_ai.api.main import lifespan


@pytest.mark.asyncio
async def test_lifespan_disposes_engine(monkeypatch):
    engine = Mock()
    engine.dispose = AsyncMock()

    monkeypatch.setattr(
        "bank_ai.api.main.get_engine",
        lambda: engine,
    )

    app = FastAPI()

    async with lifespan(app):
        engine.dispose.assert_not_awaited()

    engine.dispose.assert_awaited_once()
