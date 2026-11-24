import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.api.main import create_app
from bank_ai.core.config import get_settings
from bank_ai.core.security import create_access_token
from bank_ai.infrastructure.database.models import User
from bank_ai.infrastructure.database.session import get_db

TEST_SECRET = "test-secret-key-with-at-least-32-characters"


def make_app(user: User | None):
    app = create_app()

    db = AsyncMock(spec=AsyncSession)
    db.get.return_value = user

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: type(
        "TestSettings",
        (),
        {"jwt_secret_key": SecretStr(TEST_SECRET)},
    )()

    return app, db


@pytest.mark.asyncio
async def test_get_current_user_success():
    user_id = uuid.uuid4()

    user = User(
        id=user_id,
        email="client@test.local",
        password_hash="test_hash",
        role="client",
    )

    app, db = make_app(user)

    token = create_access_token(
        user_id,
        secret_key=TEST_SECRET,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "id": str(user_id),
        "email": "client@test.local",
        "role": "client",
    }

    db.get.assert_awaited_once_with(User, user_id)


@pytest.mark.asyncio
async def test_get_current_user_without_token():
    app, _ = make_app(None)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/auth/me")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_invalid_token():
    app, _ = make_app(None)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/auth/me",
            headers={"Authorization": "Bearer invalid-token"},
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_not_found():
    app, _ = make_app(None)

    token = create_access_token(
        uuid.uuid4(),
        secret_key=TEST_SECRET,
    )

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 401
