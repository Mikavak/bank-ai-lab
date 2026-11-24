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
from bank_ai.infrastructure.database.models import (
    Conversation,
    Customer,
    User,
)
from bank_ai.infrastructure.database.session import (
    get_db,
    get_engine,
)

TEST_SECRET = "test-secret-key-with-at-least-32-characters"


def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    token = create_access_token(
        user_id,
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
                user = User(
                    email=f"user-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="client",
                )
                other_user = User(
                    email=f"other-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="client",
                )

                session.add_all([user, other_user])
                await session.flush()

                customer = Customer(
                    user_id=user.id,
                    full_name="Тестовый клиент",
                )
                other_customer = Customer(
                    user_id=other_user.id,
                    full_name="Другой клиент",
                )

                session.add_all([customer, other_customer])
                await session.flush()

                conversation = Conversation(
                    customer_id=customer.id,
                    status="open",
                )
                closed_conversation = Conversation(
                    customer_id=customer.id,
                    status="closed",
                )

                session.add_all(
                    [
                        conversation,
                        closed_conversation,
                    ]
                )
                await session.commit()

                app = create_app()

                async def override_db():
                    yield session

                app.dependency_overrides[get_db] = override_db
                app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
                    jwt_secret_key=SecretStr(TEST_SECRET),
                )

                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    yield (
                        client,
                        user.id,
                        other_user.id,
                        conversation.id,
                        closed_conversation.id,
                    )

        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_create_and_read_message(api_context):
    client, user_id, _, conversation_id, _ = api_context

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        headers=auth_headers(user_id),
        json={"content": "Как погасить кредит?"},
    )

    assert response.status_code == 201

    message = response.json()

    assert message["role"] == "client"
    assert message["content"] == "Как погасить кредит?"
    assert message["sender_user_id"] == str(user_id)

    response = await client.get(
        f"/conversations/{conversation_id}/messages",
        headers=auth_headers(user_id),
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["id"] == message["id"]


@pytest.mark.asyncio
async def test_other_client_cannot_access_messages(api_context):
    client, _, other_id, conversation_id, _ = api_context

    response = await client.get(
        f"/conversations/{conversation_id}/messages",
        headers=auth_headers(other_id),
    )

    assert response.status_code == 404

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        headers=auth_headers(other_id),
        json={"content": "Чужое сообщение"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_messages_require_authentication(api_context):
    client, _, _, conversation_id, _ = api_context

    response = await client.get(f"/conversations/{conversation_id}/messages")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_cannot_write_to_closed_conversation(api_context):
    client, user_id, _, _, closed_id = api_context

    response = await client.post(
        f"/conversations/{closed_id}/messages",
        headers=auth_headers(user_id),
        json={"content": "Новое сообщение"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_client_cannot_set_operator_role(api_context):
    client, user_id, _, conversation_id, _ = api_context

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        headers=auth_headers(user_id),
        json={
            "content": "Поддельное сообщение",
            "role": "operator",
        },
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_empty_message_is_rejected(api_context):
    client, user_id, _, conversation_id, _ = api_context

    response = await client.post(
        f"/conversations/{conversation_id}/messages",
        headers=auth_headers(user_id),
        json={"content": "   "},
    )

    assert response.status_code == 422
