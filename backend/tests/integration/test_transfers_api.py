import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from bank_ai.api.main import create_app
from bank_ai.core.config import get_settings
from bank_ai.core.security import create_access_token
from bank_ai.infrastructure.database.models import (
    Conversation,
    Customer,
    Message,
    SupportTicket,
    TicketAssignmentHistory,
    TicketTransferRequest,
    User,
)
from bank_ai.infrastructure.database.session import get_db, get_engine

TEST_SECRET = "test-secret-key-with-at-least-32-characters"


@dataclass
class TransferApiCase:
    """Тестовый HTTP-клиент и идентификаторы записей."""

    client: AsyncClient
    connection: AsyncConnection
    ticket_id: uuid.UUID
    client_id: uuid.UUID
    operator_1_id: uuid.UUID
    operator_2_id: uuid.UUID
    operator_3_id: uuid.UUID


def auth_headers(user_id: uuid.UUID) -> dict[str, str]:
    """Создать JWT-заголовок тестового пользователя."""

    token = create_access_token(
        user_id,
        secret_key=TEST_SECRET,
    )
    return {"Authorization": f"Bearer {token}"}


def transfer_url(ticket_id: uuid.UUID) -> str:
    """Сформировать URL создания передачи."""
    return f"/operator/tickets/{ticket_id}/transfer"


async def create_transfer(case: TransferApiCase) -> dict:
    """Создать запрос от первого оператора второму."""

    response = await case.client.post(
        transfer_url(case.ticket_id),
        headers=auth_headers(case.operator_1_id),
        json={
            "to_operator_id": str(case.operator_2_id),
            "reason": "Требуется помощь другого специалиста",
        },
    )

    assert response.status_code == 201, response.text
    return response.json()


@pytest_asyncio.fixture
async def transfer_api_case():
    """
    Подготовить изолированные данные PostgreSQL.

    Сервис может вызывать commit(), но внешняя
    тестовая транзакция будет отменена.
    """

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

                operator_1 = User(
                    email=f"op1-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="operator",
                )

                operator_2 = User(
                    email=f"op2-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="operator",
                )

                operator_3 = User(
                    email=f"op3-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="operator",
                )

                db.add_all(
                    [
                        client_user,
                        operator_1,
                        operator_2,
                        operator_3,
                    ]
                )
                await db.flush()

                customer = Customer(
                    user_id=client_user.id,
                    full_name="Тестовый клиент",
                )
                db.add(customer)
                await db.flush()

                conversation = Conversation(
                    customer_id=customer.id,
                    status="waiting_operator",
                )
                db.add(conversation)
                await db.flush()

                assigned_at = datetime.now(UTC)

                ticket = SupportTicket(
                    conversation_id=conversation.id,
                    assigned_operator_id=operator_1.id,
                    status="in_progress",
                    reason="Требуется консультация",
                    assigned_at=assigned_at,
                )
                db.add(ticket)
                await db.flush()

                db.add(
                    TicketAssignmentHistory(
                        ticket_id=ticket.id,
                        operator_id=operator_1.id,
                        assigned_at=assigned_at,
                    )
                )

                await db.commit()

                ids = {
                    "ticket": ticket.id,
                    "client": client_user.id,
                    "operator_1": operator_1.id,
                    "operator_2": operator_2.id,
                    "operator_3": operator_3.id,
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
                yield TransferApiCase(
                    client=client,
                    connection=connection,
                    ticket_id=ids["ticket"],
                    client_id=ids["client"],
                    operator_1_id=ids["operator_1"],
                    operator_2_id=ids["operator_2"],
                    operator_3_id=ids["operator_3"],
                )

        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_create_transfer_and_incoming_queue(
    transfer_api_case: TransferApiCase,
):
    """Получатель видит запрос, остальные операторы — нет."""

    case = transfer_api_case
    transfer = await create_transfer(case)

    assert transfer["status"] == "pending"
    assert transfer["ticket_id"] == str(case.ticket_id)
    assert transfer["from_operator_id"] == str(case.operator_1_id)
    assert transfer["to_operator_id"] == str(case.operator_2_id)

    incoming = await case.client.get(
        "/operator/transfers/incoming",
        headers=auth_headers(case.operator_2_id),
    )

    assert incoming.status_code == 200
    assert any(item["id"] == transfer["id"] for item in incoming.json())

    other_incoming = await case.client.get(
        "/operator/transfers/incoming",
        headers=auth_headers(case.operator_3_id),
    )

    assert other_incoming.status_code == 200
    assert all(item["id"] != transfer["id"] for item in other_incoming.json())


@pytest.mark.asyncio
async def test_accept_transfer_updates_ticket_and_history(
    transfer_api_case: TransferApiCase,
):
    """Принятие изменяет ответственного и историю назначений."""

    case = transfer_api_case
    transfer = await create_transfer(case)

    url = f"/operator/transfers/{transfer['id']}/accept"

    response = await case.client.post(
        url,
        headers=auth_headers(case.operator_2_id),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    assert response.json()["responded_at"] is not None

    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        ticket = await db.get(SupportTicket, case.ticket_id)

        assert ticket is not None
        assert ticket.assigned_operator_id == case.operator_2_id

        history = (
            await db.scalars(
                select(TicketAssignmentHistory).where(
                    TicketAssignmentHistory.ticket_id == case.ticket_id
                )
            )
        ).all()

        assert len(history) == 2

        active = [item for item in history if item.released_at is None]

        assert len(active) == 1
        assert active[0].operator_id == case.operator_2_id

    # Повторное принятие должно завершиться конфликтом.
    repeat = await case.client.post(
        url,
        headers=auth_headers(case.operator_2_id),
    )

    assert repeat.status_code == 409


@pytest.mark.asyncio
async def test_reject_transfer_keeps_original_operator(
    transfer_api_case: TransferApiCase,
):
    """При отклонении ответственный не изменяется."""

    case = transfer_api_case
    transfer = await create_transfer(case)

    response = await case.client.post(
        f"/operator/transfers/{transfer['id']}/reject",
        headers=auth_headers(case.operator_2_id),
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"

    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        ticket = await db.get(SupportTicket, case.ticket_id)

        assert ticket is not None
        assert ticket.assigned_operator_id == case.operator_1_id

        history = (
            await db.scalars(
                select(TicketAssignmentHistory).where(
                    TicketAssignmentHistory.ticket_id == case.ticket_id
                )
            )
        ).all()

        assert len(history) == 1
        assert history[0].released_at is None


@pytest.mark.asyncio
async def test_client_cannot_use_operator_transfer_api(
    transfer_api_case: TransferApiCase,
):
    """Клиент не имеет доступа к операторскому API."""

    case = transfer_api_case

    response = await case.client.post(
        transfer_url(case.ticket_id),
        headers=auth_headers(case.client_id),
        json={
            "to_operator_id": str(case.operator_2_id),
            "reason": "Попытка создания передачи клиентом",
        },
    )

    assert response.status_code == 403

    incoming = await case.client.get(
        "/operator/transfers/incoming",
        headers=auth_headers(case.client_id),
    )

    assert incoming.status_code == 403

    anonymous = await case.client.get(
        "/operator/transfers/incoming",
    )

    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_other_operator_cannot_manage_transfer(
    transfer_api_case: TransferApiCase,
):
    """Посторонний оператор не может создать или принять передачу."""

    case = transfer_api_case

    forbidden = await case.client.post(
        transfer_url(case.ticket_id),
        headers=auth_headers(case.operator_3_id),
        json={
            "to_operator_id": str(case.operator_2_id),
            "reason": "Попытка передачи чужого обращения",
        },
    )

    assert forbidden.status_code == 403

    transfer = await create_transfer(case)

    accept = await case.client.post(
        f"/operator/transfers/{transfer['id']}/accept",
        headers=auth_headers(case.operator_3_id),
    )

    assert accept.status_code == 403

    reject = await case.client.post(
        f"/operator/transfers/{transfer['id']}/reject",
        headers=auth_headers(case.operator_3_id),
    )

    assert reject.status_code == 403


@pytest.mark.asyncio
async def test_duplicate_transfer_returns_409(
    transfer_api_case: TransferApiCase,
):
    """Нельзя создать две ожидающие передачи одного обращения."""

    case = transfer_api_case

    await create_transfer(case)

    response = await case.client.post(
        transfer_url(case.ticket_id),
        headers=auth_headers(case.operator_1_id),
        json={
            "to_operator_id": str(case.operator_3_id),
            "reason": "Повторная передача обращения",
        },
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_invalid_transfer_target_and_missing_ticket(
    transfer_api_case: TransferApiCase,
):
    """Некорректный получатель — 422, неизвестное обращение — 404."""

    case = transfer_api_case

    invalid_target = await case.client.post(
        transfer_url(case.ticket_id),
        headers=auth_headers(case.operator_1_id),
        json={
            "to_operator_id": str(case.client_id),
            "reason": "Попытка передачи обычному клиенту",
        },
    )

    assert invalid_target.status_code == 422

    missing_ticket = await case.client.post(
        transfer_url(uuid.uuid4()),
        headers=auth_headers(case.operator_1_id),
        json={
            "to_operator_id": str(case.operator_2_id),
            "reason": "Передача несуществующего обращения",
        },
    )

    assert missing_ticket.status_code == 404


@pytest.mark.asyncio
async def test_assigned_tickets_visible_only_to_owner(
    transfer_api_case: TransferApiCase,
):
    """
    Только ответственный оператор видит своё обращение.

    Другие операторы не получают это обращение
    в списке назначенных.
    """

    case = transfer_api_case

    # Получаем обращения первого оператора.
    response_1 = await case.client.get(
        "/operator/tickets/assigned",
        headers=auth_headers(case.operator_1_id),
    )

    assert response_1.status_code == 200

    assert any(item["id"] == str(case.ticket_id) for item in response_1.json())

    # Второй оператор пока не назначен.
    response_2 = await case.client.get(
        "/operator/tickets/assigned",
        headers=auth_headers(case.operator_2_id),
    )

    assert response_2.status_code == 200

    assert all(item["id"] != str(case.ticket_id) for item in response_2.json())

    # Третий оператор также не имеет назначения.
    response_3 = await case.client.get(
        "/operator/tickets/assigned",
        headers=auth_headers(case.operator_3_id),
    )

    assert response_3.status_code == 200

    assert all(item["id"] != str(case.ticket_id) for item in response_3.json())


@pytest.mark.asyncio
async def test_assigned_tickets_update_after_transfer(
    transfer_api_case: TransferApiCase,
):
    """
    После принятия передачи обращение исчезает
    из списка первого оператора и появляется у второго.
    """

    case = transfer_api_case

    # Первый оператор создаёт запрос передачи.
    transfer = await create_transfer(case)

    # Второй оператор принимает запрос.
    accept_response = await case.client.post(
        f"/operator/transfers/{transfer['id']}/accept",
        headers=auth_headers(case.operator_2_id),
    )

    assert accept_response.status_code == 200

    # Проверяем список первого оператора.
    previous_operator = await case.client.get(
        "/operator/tickets/assigned",
        headers=auth_headers(case.operator_1_id),
    )

    assert previous_operator.status_code == 200

    assert all(item["id"] != str(case.ticket_id) for item in previous_operator.json())

    # Проверяем список нового ответственного.
    new_operator = await case.client.get(
        "/operator/tickets/assigned",
        headers=auth_headers(case.operator_2_id),
    )

    assert new_operator.status_code == 200

    assigned_ticket = next(
        item for item in new_operator.json() if item["id"] == str(case.ticket_id)
    )

    assert assigned_ticket["status"] == "in_progress"
    assert assigned_ticket["assigned_operator_id"] == str(case.operator_2_id)


@pytest.mark.asyncio
async def test_assigned_tickets_require_operator_jwt(
    transfer_api_case: TransferApiCase,
):
    """
    Клиент не может использовать операторский API.

    Неавторизованные запросы также отклоняются.
    """

    case = transfer_api_case

    # Обычный клиент не имеет роли operator.
    client_response = await case.client.get(
        "/operator/tickets/assigned",
        headers=auth_headers(case.client_id),
    )

    assert client_response.status_code == 403

    # Запрос без JWT.
    anonymous_response = await case.client.get(
        "/operator/tickets/assigned",
    )

    assert anonymous_response.status_code == 401

    # Запрос с некорректным JWT.
    invalid_response = await case.client.get(
        "/operator/tickets/assigned",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert invalid_response.status_code == 401


@pytest.mark.asyncio
async def test_assigned_tickets_limit_validation(
    transfer_api_case: TransferApiCase,
):
    """Проверить параметр ограничения количества обращений."""

    case = transfer_api_case
    url = "/operator/tickets/assigned"
    headers = auth_headers(case.operator_1_id)

    # Корректный limit.
    valid = await case.client.get(
        url,
        params={"limit": 1},
        headers=headers,
    )

    assert valid.status_code == 200
    assert len(valid.json()) == 1

    # Значение меньше допустимого.
    too_small = await case.client.get(
        url,
        params={"limit": 0},
        headers=headers,
    )

    assert too_small.status_code == 422

    # Значение больше допустимого.
    too_large = await case.client.get(
        url,
        params={"limit": 101},
        headers=headers,
    )

    assert too_large.status_code == 422


@pytest.mark.asyncio
async def test_operator_sends_and_reads_message(
    transfer_api_case: TransferApiCase,
):
    """Сообщение оператора сохраняется в правильном диалоге."""

    case = transfer_api_case
    url = f"/operator/tickets/{case.ticket_id}/messages"

    response = await case.client.post(
        url,
        headers=auth_headers(case.operator_1_id),
        json={"content": "Здравствуйте, я помогу с вопросом."},
    )

    assert response.status_code == 201, response.text

    message = response.json()

    assert message["role"] == "operator"
    assert message["sender_user_id"] == str(case.operator_1_id)
    assert message["content"] == "Здравствуйте, я помогу с вопросом."

    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        ticket = await db.get(SupportTicket, case.ticket_id)
        saved = await db.get(Message, uuid.UUID(message["id"]))

        assert ticket is not None
        assert saved is not None
        assert saved.conversation_id == ticket.conversation_id
        assert saved.sender_user_id == case.operator_1_id

    history = await case.client.get(
        url,
        headers=auth_headers(case.operator_1_id),
    )

    assert history.status_code == 200
    assert any(item["id"] == message["id"] for item in history.json())


@pytest.mark.asyncio
async def test_operator_reads_client_and_ai_history(
    transfer_api_case: TransferApiCase,
):
    """Оператор видит сообщения клиента и AI-ассистента."""

    case = transfer_api_case

    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        ticket = await db.get(SupportTicket, case.ticket_id)
        assert ticket is not None

        db.add_all(
            [
                Message(
                    conversation_id=ticket.conversation_id,
                    sender_user_id=case.client_id,
                    role="client",
                    content="Как узнать остаток по кредиту?",
                ),
                Message(
                    conversation_id=ticket.conversation_id,
                    sender_user_id=None,
                    role="assistant",
                    content="Уточните информацию по договору.",
                ),
            ]
        )
        await db.commit()

    response = await case.client.get(
        f"/operator/tickets/{case.ticket_id}/messages",
        headers=auth_headers(case.operator_1_id),
    )

    assert response.status_code == 200

    messages = response.json()

    assert len(messages) == 2
    assert {item["role"] for item in messages} == {
        "client",
        "assistant",
    }

    ai_message = next(item for item in messages if item["role"] == "assistant")

    assert ai_message["sender_user_id"] is None


@pytest.mark.asyncio
async def test_operator_messages_access_control(
    transfer_api_case: TransferApiCase,
):
    """Проверка доступа к истории и отправке сообщений."""

    case = transfer_api_case
    url = f"/operator/tickets/{case.ticket_id}/messages"

    # Постороннему оператору обращение недоступно.
    for method in ("get", "post"):
        kwargs = {"json": {"content": "Чужое обращение"}} if method == "post" else {}

        response = await getattr(case.client, method)(
            url,
            headers=auth_headers(case.operator_3_id),
            **kwargs,
        )

        assert response.status_code == 404

    # Обычный клиент не имеет роли оператора.
    client_response = await case.client.post(
        url,
        headers=auth_headers(case.client_id),
        json={"content": "Попытка отправки"},
    )

    assert client_response.status_code == 403

    # Неавторизованному пользователю доступ запрещён.
    anonymous_response = await case.client.get(url)

    assert anonymous_response.status_code == 401


@pytest.mark.asyncio
async def test_operator_access_changes_after_transfer(
    transfer_api_case: TransferApiCase,
):
    """Передача обращения меняет права доступа операторов."""

    case = transfer_api_case
    url = f"/operator/tickets/{case.ticket_id}/messages"

    # Сообщение первого оператора до передачи.
    first_message = await case.client.post(
        url,
        headers=auth_headers(case.operator_1_id),
        json={"content": "Передам вопрос специалисту."},
    )

    assert first_message.status_code == 201

    # Первый оператор инициирует передачу.
    transfer = await create_transfer(case)

    # Второй принимает обращение.
    accepted = await case.client.post(
        f"/operator/transfers/{transfer['id']}/accept",
        headers=auth_headers(case.operator_2_id),
    )

    assert accepted.status_code == 200

    # Прежний оператор больше не может читать переписку.
    old_read = await case.client.get(
        url,
        headers=auth_headers(case.operator_1_id),
    )

    assert old_read.status_code == 404

    # Прежний оператор больше не может отвечать.
    old_write = await case.client.post(
        url,
        headers=auth_headers(case.operator_1_id),
        json={"content": "Попытка ответа после передачи"},
    )

    assert old_write.status_code == 404

    # Новый оператор видит предыдущую историю.
    new_read = await case.client.get(
        url,
        headers=auth_headers(case.operator_2_id),
    )

    assert new_read.status_code == 200
    assert any(item["id"] == first_message.json()["id"] for item in new_read.json())

    # Новый оператор может продолжить диалог.
    new_write = await case.client.post(
        url,
        headers=auth_headers(case.operator_2_id),
        json={"content": "Продолжим консультацию."},
    )

    assert new_write.status_code == 201
    assert new_write.json()["sender_user_id"] == str(case.operator_2_id)


@pytest.mark.asyncio
async def test_operator_cannot_write_to_closed_conversation(
    transfer_api_case: TransferApiCase,
):
    """Запрещаем отправку сообщений в закрытый диалог."""

    case = transfer_api_case

    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        ticket = await db.get(SupportTicket, case.ticket_id)
        assert ticket is not None

        conversation = await db.get(
            Conversation,
            ticket.conversation_id,
        )
        assert conversation is not None

        conversation.status = "closed"
        await db.commit()

    response = await case.client.post(
        f"/operator/tickets/{case.ticket_id}/messages",
        headers=auth_headers(case.operator_1_id),
        json={"content": "Сообщение после закрытия"},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_operator_message_validation(
    transfer_api_case: TransferApiCase,
):
    """Проверка текста и защиты серверных полей."""

    case = transfer_api_case
    url = f"/operator/tickets/{case.ticket_id}/messages"
    headers = auth_headers(case.operator_1_id)

    invalid_payloads = [
        {"content": ""},
        {"content": "   "},
        {"content": "a" * 4001},
        {
            "content": "Сообщение",
            "role": "assistant",
        },
        {
            "content": "Сообщение",
            "sender_user_id": str(case.operator_2_id),
        },
    ]

    for payload in invalid_payloads:
        response = await case.client.post(
            url,
            headers=headers,
            json=payload,
        )

        assert response.status_code == 422, (
            f"Payload: {payload!r}, response: {response.text}"
        )

    # Корректный текст с пробелами должен нормализоваться.
    valid_response = await case.client.post(
        url,
        headers=headers,
        json={"content": "  Проверка сообщения  "},
    )

    assert valid_response.status_code == 201
    assert valid_response.json()["content"] == "Проверка сообщения"


@pytest.mark.asyncio
async def test_resolve_ticket_updates_status_and_history(
    transfer_api_case: TransferApiCase,
):
    """
    Завершение обращения:
    - переводит обращение в resolved;
    - фиксирует время решения;
    - закрывает активное назначение;
    - возвращает диалог в open.
    """

    case = transfer_api_case

    response = await case.client.post(
        f"/operator/tickets/{case.ticket_id}/resolve",
        headers=auth_headers(case.operator_1_id),
    )

    assert response.status_code == 200, response.text

    data = response.json()

    assert data["status"] == "resolved"
    assert data["resolved_at"] is not None
    assert data["assigned_operator_id"] == str(case.operator_1_id)

    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        # Проверяем обращение после завершения.
        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert ticket is not None
        assert ticket.status == "resolved"
        assert ticket.resolved_at is not None

        # Диалог снова доступен для общения.
        conversation = await db.get(
            Conversation,
            ticket.conversation_id,
        )

        assert conversation is not None
        assert conversation.status == "open"

        # Проверяем историю ответственности оператора.
        history = (
            await db.scalars(
                select(TicketAssignmentHistory).where(
                    TicketAssignmentHistory.ticket_id == case.ticket_id
                )
            )
        ).all()

        assert len(history) == 1
        assert history[0].operator_id == case.operator_1_id
        assert history[0].released_at is not None

        # Завершение назначения совпадает
        # по времени с завершением обращения.
        assert history[0].released_at == ticket.resolved_at


@pytest.mark.asyncio
async def test_resolve_ticket_cancels_pending_transfer(
    transfer_api_case: TransferApiCase,
):
    """
    Завершение обращения автоматически отменяет
    ожидающий запрос передачи другому оператору.
    """

    case = transfer_api_case

    # Первый оператор предлагает передачу второму.
    transfer = await create_transfer(case)

    assert transfer["status"] == "pending"

    # Первый оператор решает вопрос клиента.
    response = await case.client.post(
        f"/operator/tickets/{case.ticket_id}/resolve",
        headers=auth_headers(case.operator_1_id),
    )

    assert response.status_code == 200

    # Проверяем состояние запроса передачи в PostgreSQL.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        saved_transfer = await db.get(
            TicketTransferRequest,
            uuid.UUID(transfer["id"]),
        )

        assert saved_transfer is not None
        assert saved_transfer.status == "cancelled"
        assert saved_transfer.responded_at is not None

    # Отменённый запрос исчезает из входящей очереди.
    incoming = await case.client.get(
        "/operator/transfers/incoming",
        headers=auth_headers(case.operator_2_id),
    )

    assert incoming.status_code == 200

    assert all(item["id"] != transfer["id"] for item in incoming.json())

    # Второй оператор больше не может принять передачу.
    accept_response = await case.client.post(
        f"/operator/transfers/{transfer['id']}/accept",
        headers=auth_headers(case.operator_2_id),
    )

    assert accept_response.status_code == 409


@pytest.mark.asyncio
async def test_resolve_ticket_access_control(
    transfer_api_case: TransferApiCase,
):
    """
    Завершить обращение может только
    текущий ответственный оператор.
    """

    case = transfer_api_case

    url = f"/operator/tickets/{case.ticket_id}/resolve"

    # Обычный клиент не имеет роли operator.
    client_response = await case.client.post(
        url,
        headers=auth_headers(case.client_id),
    )

    assert client_response.status_code == 403

    # Другой оператор не отвечает за это обращение.
    other_response = await case.client.post(
        url,
        headers=auth_headers(case.operator_2_id),
    )

    assert other_response.status_code == 404

    # Запрос без JWT.
    anonymous_response = await case.client.post(url)

    assert anonymous_response.status_code == 401

    # Несуществующее обращение.
    missing_response = await case.client.post(
        f"/operator/tickets/{uuid.uuid4()}/resolve",
        headers=auth_headers(case.operator_1_id),
    )

    assert missing_response.status_code == 404

    # Неудачные попытки не изменяют обращение.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert ticket is not None
        assert ticket.status == "in_progress"
        assert ticket.assigned_operator_id == case.operator_1_id


@pytest.mark.asyncio
async def test_resolve_ticket_cannot_be_repeated(
    transfer_api_case: TransferApiCase,
):
    """Повторное завершение не изменяет историю назначений."""

    case = transfer_api_case

    url = f"/operator/tickets/{case.ticket_id}/resolve"
    headers = auth_headers(case.operator_1_id)

    first = await case.client.post(
        url,
        headers=headers,
    )

    assert first.status_code == 200

    # Повторное завершение возвращает конфликт.
    second = await case.client.post(
        url,
        headers=headers,
    )

    assert second.status_code == 409

    # Убеждаемся, что история не продублирована.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        history = (
            await db.scalars(
                select(TicketAssignmentHistory).where(
                    TicketAssignmentHistory.ticket_id == case.ticket_id
                )
            )
        ).all()

        assert len(history) == 1
        assert history[0].released_at is not None


@pytest.mark.asyncio
async def test_client_can_create_new_ticket_after_resolution(
    transfer_api_case: TransferApiCase,
):
    """
    После решения предыдущего обращения клиент
    может снова обратиться к оператору в том же диалоге.
    """

    case = transfer_api_case

    # Сначала получаем идентификатор диалога.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert ticket is not None
        conversation_id = ticket.conversation_id

    # Оператор завершает текущее обращение.
    resolved = await case.client.post(
        f"/operator/tickets/{case.ticket_id}/resolve",
        headers=auth_headers(case.operator_1_id),
    )

    assert resolved.status_code == 200

    # Клиент создаёт новое обращение в том же диалоге.
    new_ticket = await case.client.post(
        f"/conversations/{conversation_id}/tickets",
        headers=auth_headers(case.client_id),
        json={
            "reason": "Появился новый вопрос по обслуживанию",
        },
    )

    assert new_ticket.status_code == 201, new_ticket.text

    data = new_ticket.json()

    assert data["id"] != str(case.ticket_id)
    assert data["conversation_id"] == str(conversation_id)
    assert data["status"] == "pending"
    assert data["assigned_operator_id"] is None

    # Предыдущее обращение осталось завершённым.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        old_ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        conversation = await db.get(
            Conversation,
            conversation_id,
        )

        assert old_ticket is not None
        assert old_ticket.status == "resolved"

        assert conversation is not None
        assert conversation.status == "waiting_operator"


async def resolve_and_get_conversation_id(
    case: TransferApiCase,
) -> uuid.UUID:
    """
    Завершить обращение и вернуть ID связанного диалога.

    Подготовительная функция для тестов закрытия.
    """

    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert ticket is not None
        conversation_id = ticket.conversation_id

    response = await case.client.post(
        f"/operator/tickets/{case.ticket_id}/resolve",
        headers=auth_headers(case.operator_1_id),
    )

    assert response.status_code == 200, response.text

    return conversation_id


@pytest.mark.asyncio
async def test_client_closes_resolved_conversation(
    transfer_api_case: TransferApiCase,
):
    """Клиент может закрыть диалог после решения обращения."""

    case = transfer_api_case

    conversation_id = await resolve_and_get_conversation_id(case)

    response = await case.client.post(
        f"/conversations/{conversation_id}/close",
        headers=auth_headers(case.client_id),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "closed"

    # Проверяем сохранённое состояние PostgreSQL.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        conversation = await db.get(
            Conversation,
            conversation_id,
        )

        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert conversation is not None
        assert conversation.status == "closed"

        # История обращения не меняется.
        assert ticket is not None
        assert ticket.status == "resolved"


@pytest.mark.asyncio
async def test_close_conversation_is_idempotent(
    transfer_api_case: TransferApiCase,
):
    """Повторное закрытие возвращает 200 без ошибки."""

    case = transfer_api_case

    conversation_id = await resolve_and_get_conversation_id(case)

    url = f"/conversations/{conversation_id}/close"
    headers = auth_headers(case.client_id)

    first = await case.client.post(
        url,
        headers=headers,
    )

    second = await case.client.post(
        url,
        headers=headers,
    )

    assert first.status_code == 200
    assert second.status_code == 200

    assert first.json()["id"] == second.json()["id"]
    assert second.json()["status"] == "closed"


@pytest.mark.asyncio
async def test_cannot_close_conversation_with_active_ticket(
    transfer_api_case: TransferApiCase,
):
    """
    Клиент не может закрыть диалог,
    пока оператор обрабатывает обращение.
    """

    case = transfer_api_case

    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert ticket is not None
        conversation_id = ticket.conversation_id

    response = await case.client.post(
        f"/conversations/{conversation_id}/close",
        headers=auth_headers(case.client_id),
    )

    assert response.status_code == 409

    # Статус диалога не должен измениться.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        conversation = await db.get(
            Conversation,
            conversation_id,
        )

        assert conversation is not None
        assert conversation.status == "waiting_operator"


@pytest.mark.asyncio
async def test_other_client_cannot_close_conversation(
    transfer_api_case: TransferApiCase,
):
    """Посторонний клиент не может закрыть чужой диалог."""

    case = transfer_api_case

    conversation_id = await resolve_and_get_conversation_id(case)

    # Создаём второго клиента внутри тестовой транзакции.
    async with AsyncSession(
        bind=case.connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    ) as db:
        other_user = User(
            email=f"other-{uuid.uuid4()}@test.local",
            password_hash="test_hash",
            role="client",
        )

        db.add(other_user)
        await db.flush()

        other_customer = Customer(
            user_id=other_user.id,
            full_name="Другой клиент",
        )

        db.add(other_customer)
        await db.commit()

        other_user_id = other_user.id

    response = await case.client.post(
        f"/conversations/{conversation_id}/close",
        headers=auth_headers(other_user_id),
    )

    assert response.status_code == 404

    # Чужая попытка не закрыла диалог.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        conversation = await db.get(
            Conversation,
            conversation_id,
        )

        assert conversation is not None
        assert conversation.status == "open"


@pytest.mark.asyncio
async def test_close_conversation_requires_client_jwt(
    transfer_api_case: TransferApiCase,
):
    """Проверить авторизацию и роль пользователя."""

    case = transfer_api_case

    conversation_id = await resolve_and_get_conversation_id(case)

    url = f"/conversations/{conversation_id}/close"

    # Без токена доступ запрещён.
    anonymous = await case.client.post(url)

    assert anonymous.status_code == 401

    # Некорректный JWT.
    invalid_token = await case.client.post(
        url,
        headers={
            "Authorization": "Bearer invalid-token",
        },
    )

    assert invalid_token.status_code == 401

    # Оператор не может закрыть клиентский диалог.
    operator_response = await case.client.post(
        url,
        headers=auth_headers(case.operator_1_id),
    )

    assert operator_response.status_code == 403


@pytest.mark.asyncio
async def test_closed_conversation_rejects_new_activity(
    transfer_api_case: TransferApiCase,
):
    """
    После закрытия:
    - клиент не может отправлять сообщения;
    - клиент не может создать новое обращение.
    """

    case = transfer_api_case

    conversation_id = await resolve_and_get_conversation_id(case)

    closed = await case.client.post(
        f"/conversations/{conversation_id}/close",
        headers=auth_headers(case.client_id),
    )

    assert closed.status_code == 200

    # Попытка отправить сообщение в закрытый диалог.
    message_response = await case.client.post(
        f"/conversations/{conversation_id}/messages",
        headers=auth_headers(case.client_id),
        json={
            "content": "Сообщение после закрытия",
        },
    )

    assert message_response.status_code == 409

    # Попытка создать новое обращение.
    ticket_response = await case.client.post(
        f"/conversations/{conversation_id}/tickets",
        headers=auth_headers(case.client_id),
        json={
            "reason": "Новое обращение после закрытия диалога",
        },
    )

    assert ticket_response.status_code == 409


@pytest.mark.asyncio
async def test_full_support_ticket_lifecycle(
    transfer_api_case: TransferApiCase,
):
    """
    Проверить полный жизненный цикл обращения:

    1. Первый оператор передаёт обращение второму.
    2. Второй оператор принимает обращение.
    3. Второй оператор отправляет сообщение.
    4. Второй оператор завершает обращение.
    5. Клиент создаёт повторное обращение.
    6. Первый оператор принимает новое обращение.
    7. Первый оператор завершает новое обращение.
    8. Клиент закрывает диалог.

    Проверяем итоговые состояния и историю в PostgreSQL.
    """

    case = transfer_api_case

    # Получаем идентификатор исходного диалога.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        original_ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert original_ticket is not None
        conversation_id = original_ticket.conversation_id

    # Шаг 1. Первый оператор предлагает передачу.
    transfer = await create_transfer(case)

    assert transfer["status"] == "pending"

    # Шаг 2. Второй оператор принимает обращение.
    accepted = await case.client.post(
        f"/operator/transfers/{transfer['id']}/accept",
        headers=auth_headers(case.operator_2_id),
    )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "accepted"

    # Шаг 3. Второй оператор отправляет ответ клиенту.
    message_response = await case.client.post(
        f"/operator/tickets/{case.ticket_id}/messages",
        headers=auth_headers(case.operator_2_id),
        json={
            "content": "Ваш вопрос рассмотрен специалистом.",
        },
    )

    assert message_response.status_code == 201
    assert message_response.json()["role"] == "operator"
    assert message_response.json()["sender_user_id"] == str(case.operator_2_id)

    # Шаг 4. Второй оператор завершает обращение.
    resolved = await case.client.post(
        f"/operator/tickets/{case.ticket_id}/resolve",
        headers=auth_headers(case.operator_2_id),
    )

    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "resolved"

    # Шаг 5. Клиент создаёт повторное обращение.
    new_ticket_response = await case.client.post(
        f"/conversations/{conversation_id}/tickets",
        headers=auth_headers(case.client_id),
        json={
            "reason": "Возник дополнительный вопрос по обслуживанию",
        },
    )

    assert new_ticket_response.status_code == 201
    assert new_ticket_response.json()["status"] == "pending"

    new_ticket_id = new_ticket_response.json()["id"]

    # Пока обращение активно, закрытие диалога запрещено.
    premature_close = await case.client.post(
        f"/conversations/{conversation_id}/close",
        headers=auth_headers(case.client_id),
    )

    assert premature_close.status_code == 409

    # Шаг 6. Первый оператор принимает новое обращение.
    claimed = await case.client.post(
        f"/operator/tickets/{new_ticket_id}/claim",
        headers=auth_headers(case.operator_1_id),
    )

    assert claimed.status_code == 200
    assert claimed.json()["status"] == "in_progress"

    # Шаг 7. Первый оператор завершает новое обращение.
    second_resolution = await case.client.post(
        f"/operator/tickets/{new_ticket_id}/resolve",
        headers=auth_headers(case.operator_1_id),
    )

    assert second_resolution.status_code == 200
    assert second_resolution.json()["status"] == "resolved"

    # Шаг 8. Клиент окончательно закрывает диалог.
    closed = await case.client.post(
        f"/conversations/{conversation_id}/close",
        headers=auth_headers(case.client_id),
    )

    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"

    # Проверяем сохранённые данные в PostgreSQL.
    async with AsyncSession(
        bind=case.connection,
        join_transaction_mode="create_savepoint",
    ) as db:
        conversation = await db.get(
            Conversation,
            conversation_id,
        )

        tickets = list(
            (
                await db.scalars(
                    select(SupportTicket).where(
                        SupportTicket.conversation_id == conversation_id
                    )
                )
            ).all()
        )

        assert conversation is not None
        assert conversation.status == "closed"

        # Оба обращения завершены.
        assert len(tickets) == 2
        assert all(ticket.status == "resolved" for ticket in tickets)

        # Проверяем сохранение истории назначений.
        ticket_ids = [ticket.id for ticket in tickets]

        assignments = list(
            (
                await db.scalars(
                    select(TicketAssignmentHistory).where(
                        TicketAssignmentHistory.ticket_id.in_(ticket_ids)
                    )
                )
            ).all()
        )

        # Первое обращение: два оператора.
        # Второе обращение: один оператор.
        assert len(assignments) == 3

        # Все назначения завершены.
        assert all(item.released_at is not None for item in assignments)

        # Проверяем, что ответ оператора сохранён.
        messages = list(
            (
                await db.scalars(
                    select(Message).where(Message.conversation_id == conversation_id)
                )
            ).all()
        )

        assert any(
            message.role == "operator" and message.sender_user_id == case.operator_2_id
            for message in messages
        )
