import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.api.main import create_app
from bank_ai.core.config import get_settings
from bank_ai.core.security import create_access_token
from bank_ai.infrastructure.database.models import (
    Conversation,
    Customer,
    TicketAssignmentHistory,
    User,
)
from bank_ai.infrastructure.database.session import get_db, get_engine

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
            ) as db:
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

                db.add_all([client_user, other_user, operator])
                await db.flush()

                customer = Customer(
                    user_id=client_user.id,
                    full_name="Тестовый клиент",
                )
                other_customer = Customer(
                    user_id=other_user.id,
                    full_name="Другой клиент",
                )

                db.add_all([customer, other_customer])
                await db.flush()

                conversation = Conversation(
                    customer_id=customer.id,
                    status="open",
                )
                closed_conversation = Conversation(
                    customer_id=customer.id,
                    status="closed",
                )

                db.add_all([conversation, closed_conversation])
                await db.commit()

                ids = {
                    "client": client_user.id,
                    "other": other_user.id,
                    "operator": operator.id,
                    "conversation": conversation.id,
                    "closed": closed_conversation.id,
                }

            app = create_app()

            async def override_db():
                async with AsyncSession(
                    bind=connection,
                    expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                ) as db:
                    yield db

            app.dependency_overrides[get_db] = override_db
            app.dependency_overrides[get_settings] = lambda: SimpleNamespace(
                jwt_secret_key=SecretStr(TEST_SECRET),
            )

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                yield client, ids, connection

        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_create_ticket(api_context):
    client, ids, connection = api_context

    response = await client.post(
        f"/conversations/{ids['conversation']}/tickets",
        headers=auth_headers(ids["client"]),
        json={"reason": "AI не смог ответить на вопрос"},
    )

    assert response.status_code == 201

    ticket = response.json()

    assert ticket["status"] == "pending"
    assert ticket["assigned_operator_id"] is None
    assert ticket["conversation_id"] == str(ids["conversation"])

    result = await connection.execute(
        select(Conversation.status).where(Conversation.id == ids["conversation"])
    )

    assert result.scalar_one() == "waiting_operator"


@pytest.mark.asyncio
async def test_operator_claim_ticket(api_context):
    client, ids, connection = api_context

    create_response = await client.post(
        f"/conversations/{ids['conversation']}/tickets",
        headers=auth_headers(ids["client"]),
        json={"reason": "Нужен оператор для консультации"},
    )

    assert create_response.status_code == 201

    ticket_id = create_response.json()["id"]

    queue_response = await client.get(
        "/operator/tickets",
        headers=auth_headers(ids["operator"]),
    )

    assert queue_response.status_code == 200
    assert any(ticket["id"] == ticket_id for ticket in queue_response.json())

    claim_response = await client.post(
        f"/operator/tickets/{ticket_id}/claim",
        headers=auth_headers(ids["operator"]),
    )

    assert claim_response.status_code == 200

    claimed = claim_response.json()

    assert claimed["status"] == "in_progress"
    assert claimed["assigned_operator_id"] == str(ids["operator"])

    history = (
        await connection.execute(
            select(
                TicketAssignmentHistory.operator_id,
                TicketAssignmentHistory.released_at,
            ).where(TicketAssignmentHistory.ticket_id == uuid.UUID(ticket_id))
        )
    ).all()

    assert len(history) == 1
    assert history[0].operator_id == ids["operator"]
    assert history[0].released_at is None

    queue_response = await client.get(
        "/operator/tickets",
        headers=auth_headers(ids["operator"]),
    )

    assert all(ticket["id"] != ticket_id for ticket in queue_response.json())


@pytest.mark.asyncio
async def test_duplicate_ticket_returns_409(api_context):
    client, ids, _ = api_context

    url = f"/conversations/{ids['conversation']}/tickets"

    first = await client.post(
        url,
        headers=auth_headers(ids["client"]),
        json={"reason": "Первое обращение"},
    )

    second = await client.post(
        url,
        headers=auth_headers(ids["client"]),
        json={"reason": "Повторное обращение"},
    )

    assert first.status_code == 201
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_other_client_cannot_create_ticket(api_context):
    client, ids, _ = api_context

    response = await client.post(
        f"/conversations/{ids['conversation']}/tickets",
        headers=auth_headers(ids["other"]),
        json={"reason": "Попытка доступа к чужому диалогу"},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_client_cannot_access_operator_queue(api_context):
    client, ids, _ = api_context

    response = await client.get(
        "/operator/tickets",
        headers=auth_headers(ids["client"]),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_closed_conversation_rejects_ticket(api_context):
    client, ids, _ = api_context

    response = await client.post(
        f"/conversations/{ids['closed']}/tickets",
        headers=auth_headers(ids["client"]),
        json={"reason": "Обращение в закрытом диалоге"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_claim_already_claimed_ticket(api_context):
    client, ids, _ = api_context

    response = await client.post(
        f"/conversations/{ids['conversation']}/tickets",
        headers=auth_headers(ids["client"]),
        json={"reason": "Проверка повторного назначения"},
    )

    assert response.status_code == 201

    ticket_id = response.json()["id"]

    url = f"/operator/tickets/{ticket_id}/claim"

    first = await client.post(
        url,
        headers=auth_headers(ids["operator"]),
    )

    second = await client.post(
        url,
        headers=auth_headers(ids["operator"]),
    )

    assert first.status_code == 200
    assert second.status_code == 409
