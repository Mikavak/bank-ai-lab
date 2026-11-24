
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.api.main import create_app
from bank_ai.core.config import get_settings
from bank_ai.core.security import (
    hash_password,
    verify_access_token,
)
from bank_ai.infrastructure.database.models import User
from bank_ai.infrastructure.database.session import get_db


TEST_SECRET = "test-secret-key-with-at-least-32-characters"


def make_app(user: User | None):
    app = create_app()

    db = AsyncMock(spec=AsyncSession)
    db.scalar.return_value = user

    app.dependency_overrides[get_db] = lambda: db

    app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
        jwt_secret_key=SecretStr(TEST_SECRET),
        jwt_expire_minutes=30,
    )

    return app


@pytest.mark.asyncio
async def test_login_success():
    user_id = uuid.uuid4()

    user = User(
        id=user_id,
        email="client@test.local",
        password_hash=hash_password("StrongPassword123!"),
        role="client",
    )

    app = make_app(user)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/auth/login",
            json={
                "email": "client@test.local",
                "password": "StrongPassword123!",
            },
        )

    assert response.status_code == 200

    data = response.json()
    assert data["token_type"] == "bearer"
    assert data["expires_in"] == 1800

    decoded_user_id = verify_access_token(
        data["access_token"],
        secret_key=TEST_SECRET,
    )
    assert decoded_user_id == user_id


@pytest.mark.asyncio
async def test_login_wrong_password():
    user = User(
        id=uuid.uuid4(),
        email="client@test.local",
        password_hash=hash_password("CorrectPassword"),
        role="client",
    )

    app = make_app(user)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/auth/login",
            json={
                "email": "client@test.local",
                "password": "WrongPassword",
            },
        )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_user_not_found():
    app = make_app(None)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/auth/login",
            json={
                "email": "unknown@test.local",
                "password": "AnyPassword",
            },
        )

    assert response.status_code == 401
