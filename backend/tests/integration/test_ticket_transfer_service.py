import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
)

from bank_ai.application.ticket_transfer_service import (
    TransferConflictError,
    TransferPermissionError,
    accept_transfer,
    reject_transfer,
    request_transfer,
)
from bank_ai.infrastructure.database.models import (
    Conversation,
    Customer,
    SupportTicket,
    TicketAssignmentHistory,
    TicketTransferRequest,
    User,
)
from bank_ai.infrastructure.database.session import get_engine


@dataclass
class TransferCase:
    # Соединение с тестовой транзакцией PostgreSQL.
    connection: AsyncConnection

    # ID обращения, которое будем передавать.
    ticket_id: uuid.UUID

    # Текущий ответственный оператор.
    operator_1: User

    # Оператор, которому передаём обращение.
    operator_2: User

    # Посторонний оператор.
    operator_3: User


def make_session(connection: AsyncConnection) -> AsyncSession:
    """
    Создать сессию внутри внешней тестовой транзакции.

    Внутренние commit() используют SAVEPOINT.
    """
    return AsyncSession(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )


@pytest_asyncio.fixture
async def transfer_case():
    engine = get_engine()

    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with make_session(connection) as db:
                client = User(
                    email=f"client-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="client",
                )

                operator_1 = User(
                    email=f"operator1-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="operator",
                )

                operator_2 = User(
                    email=f"operator2-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="operator",
                )

                operator_3 = User(
                    email=f"operator3-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="operator",
                )

                db.add_all(
                    [
                        client,
                        operator_1,
                        operator_2,
                        operator_3,
                    ]
                )
                await db.flush()

                customer = Customer(
                    user_id=client.id,
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

                now = datetime.now(UTC)

                ticket = SupportTicket(
                    conversation_id=conversation.id,
                    assigned_operator_id=operator_1.id,
                    status="in_progress",
                    reason="Клиенту требуется консультация",
                    assigned_at=now,
                )
                db.add(ticket)
                await db.flush()

                assignment = TicketAssignmentHistory(
                    ticket_id=ticket.id,
                    operator_id=operator_1.id,
                    assigned_at=now,
                )
                db.add(assignment)

                await db.commit()

                case = TransferCase(
                    connection=connection,
                    ticket_id=ticket.id,
                    operator_1=operator_1,
                    operator_2=operator_2,
                    operator_3=operator_3,
                )

            yield case

        finally:
            await transaction.rollback()


async def create_pending_transfer(
    case: TransferCase,
) -> uuid.UUID:
    """Создать ожидающий запрос на передачу."""

    async with make_session(case.connection) as db:
        transfer = await request_transfer(
            db=db,
            ticket_id=case.ticket_id,
            from_operator=case.operator_1,
            to_operator_id=case.operator_2.id,
            reason="Требуется консультация специалиста",
        )

        return transfer.id


@pytest.mark.asyncio
async def test_request_transfer(transfer_case: TransferCase):
    case = transfer_case

    transfer_id = await create_pending_transfer(case)

    async with make_session(case.connection) as db:
        transfer = await db.get(
            TicketTransferRequest,
            transfer_id,
        )

        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert transfer is not None
        assert ticket is not None

        assert transfer.status == "pending"
        assert transfer.responded_at is None

        assert transfer.from_operator_id == case.operator_1.id
        assert transfer.to_operator_id == case.operator_2.id

        # До принятия ответственным остаётся первый оператор.
        assert ticket.assigned_operator_id == case.operator_1.id


@pytest.mark.asyncio
async def test_accept_transfer_updates_assignment_history(
    transfer_case: TransferCase,
):
    case = transfer_case
    transfer_id = await create_pending_transfer(case)

    async with make_session(case.connection) as db:
        transfer = await accept_transfer(
            db=db,
            transfer_id=transfer_id,
            operator=case.operator_2,
        )

        assert transfer.status == "accepted"
        assert transfer.responded_at is not None

    async with make_session(case.connection) as db:
        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert ticket is not None
        assert ticket.status == "in_progress"
        assert ticket.assigned_operator_id == case.operator_2.id

        result = await db.scalars(
            select(TicketAssignmentHistory).where(
                TicketAssignmentHistory.ticket_id == case.ticket_id
            )
        )

        history = list(result.all())

        assert len(history) == 2

        old_assignment = next(
            item for item in history if item.operator_id == case.operator_1.id
        )

        new_assignment = next(
            item for item in history if item.operator_id == case.operator_2.id
        )

        # Старое назначение закрыто.
        assert old_assignment.released_at is not None

        # Новое назначение активно.
        assert new_assignment.released_at is None

        # Время начала нового назначения совпадает
        # с моментом завершения предыдущего.
        assert new_assignment.assigned_at == old_assignment.released_at

        assert ticket.assigned_at == new_assignment.assigned_at


@pytest.mark.asyncio
async def test_reject_transfer_keeps_original_operator(
    transfer_case: TransferCase,
):
    case = transfer_case
    transfer_id = await create_pending_transfer(case)

    async with make_session(case.connection) as db:
        transfer = await reject_transfer(
            db=db,
            transfer_id=transfer_id,
            operator=case.operator_2,
        )

        assert transfer.status == "rejected"
        assert transfer.responded_at is not None

    async with make_session(case.connection) as db:
        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        assert ticket is not None
        assert ticket.assigned_operator_id == case.operator_1.id

        history = (
            await db.scalars(
                select(TicketAssignmentHistory).where(
                    TicketAssignmentHistory.ticket_id == case.ticket_id
                )
            )
        ).all()

        # Отклонение не создаёт нового назначения.
        assert len(history) == 1
        assert history[0].operator_id == case.operator_1.id
        assert history[0].released_at is None


@pytest.mark.asyncio
async def test_other_operator_cannot_request_transfer(
    transfer_case: TransferCase,
):
    case = transfer_case

    async with make_session(case.connection) as db:
        with pytest.raises(TransferPermissionError):
            await request_transfer(
                db=db,
                ticket_id=case.ticket_id,
                from_operator=case.operator_3,
                to_operator_id=case.operator_2.id,
                reason="Попытка передачи чужого обращения",
            )


@pytest.mark.asyncio
async def test_wrong_operator_cannot_accept_transfer(
    transfer_case: TransferCase,
):
    case = transfer_case
    transfer_id = await create_pending_transfer(case)

    async with make_session(case.connection) as db:
        with pytest.raises(TransferPermissionError):
            await accept_transfer(
                db=db,
                transfer_id=transfer_id,
                operator=case.operator_3,
            )

    async with make_session(case.connection) as db:
        transfer = await db.get(
            TicketTransferRequest,
            transfer_id,
        )

        assert transfer is not None
        assert transfer.status == "pending"


@pytest.mark.asyncio
async def test_duplicate_pending_transfer_is_rejected(
    transfer_case: TransferCase,
):
    case = transfer_case

    await create_pending_transfer(case)

    async with make_session(case.connection) as db:
        with pytest.raises(TransferConflictError):
            await request_transfer(
                db=db,
                ticket_id=case.ticket_id,
                from_operator=case.operator_1,
                to_operator_id=case.operator_3.id,
                reason="Повторный запрос передачи обращения",
            )


@pytest.mark.asyncio
async def test_transfer_cannot_be_accepted_twice(
    transfer_case: TransferCase,
):
    case = transfer_case
    transfer_id = await create_pending_transfer(case)

    async with make_session(case.connection) as db:
        await accept_transfer(
            db=db,
            transfer_id=transfer_id,
            operator=case.operator_2,
        )

    async with make_session(case.connection) as db:
        with pytest.raises(TransferConflictError):
            await accept_transfer(
                db=db,
                transfer_id=transfer_id,
                operator=case.operator_2,
            )
