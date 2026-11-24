
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.api.main import create_app
from bank_ai.core.config import get_settings
from bank_ai.core.security import create_access_token
from bank_ai.infrastructure.database.models import Customer, User
from bank_ai.infrastructure.database.session import get_db, get_engine


TEST_SECRET = "test-secret-key-with-at-least-32-characters"


def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id=user_id,
        secret_key=TEST_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def api_context():
    engine = get_engine()

    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as session:
                client_user = User(
                    email=f"client-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="client",
                )
                other_user = User(
                    email=f"other-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="client",
                )
                operator = User(
                    email=f"operator-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="operator",
                )

                session.add_all([
                    client_user,
                    other_user,
                    operator,
                ])
                await session.flush()

                session.add_all([
                    Customer(
                        user_id=client_user.id,
                        full_name="Первый клиент",
                    ),
                    Customer(
                        user_id=other_user.id,
                        full_name="Второй клиент",
                    ),
                ])
                await session.commit()

                app = create_app()

                async def override_db():
                    yield session

                app.dependency_overrides[get_db] = override_db
                app.dependency_overrides[get_settings] = lambda: (
                    SimpleNamespace(
                        jwt_secret_key=SecretStr(TEST_SECRET),
                    )
                )

                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    yield (
                        client,
                        client_user.id,
                        other_user.id,
                        operator.id,
                    )
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_create_and_list_conversations(api_context):
    client, user_id, other_id, _ = api_context

    response = await client.post(
        "/conversations",
        headers=auth_headers(user_id),
    )

    assert response.status_code == 201
    assert response.json()["status"] == "open"

    conversation_id = response.json()["id"]

    response = await client.get(
        "/conversations",
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == conversation_id

    other_response = await client.get(
        "/conversations",
        headers=auth_headers(other_id),
    )

    assert other_response.status_code == 200
    assert other_response.json() == []


@pytest.mark.asyncio
async def test_conversations_require_jwt(api_context):
    client, _, _, _ = api_context

    response = await client.get("/conversations")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_operator_cannot_create_client_conversation(
    api_context,
):
    client, _, _, operator_id = api_context

    response = await client.post(
        "/conversations",
        headers=auth_headers(operator_id),
    )

    assert response.status_code == 403
