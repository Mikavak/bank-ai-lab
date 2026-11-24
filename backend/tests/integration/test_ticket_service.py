import asyncio
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from bank_ai.application.ticket_service import (
    TicketAccessDeniedError,
    TicketAlreadyActiveError,
    TicketAlreadyClaimedError,
    TicketNotFoundError,
    claim_support_ticket,
    create_support_ticket,
    get_pending_tickets,
)
from bank_ai.infrastructure.database.models import (
    Conversation,
    Customer,
    SupportTicket,
    TicketAssignmentHistory,
    User,
)
from bank_ai.infrastructure.database.session import get_session_factory


@pytest_asyncio.fixture
async def ticket_data():
    factory = get_session_factory()

    async with factory() as db:
        client = User(
            email=f"client-{uuid.uuid4()}@test.local",
            password_hash="test_hash",
            role="client",
        )
        operator1 = User(
            email=f"op1-{uuid.uuid4()}@test.local",
            password_hash="test_hash",
            role="operator",
        )
        operator2 = User(
            email=f"op2-{uuid.uuid4()}@test.local",
            password_hash="test_hash",
            role="operator",
        )

        db.add_all([client, operator1, operator2])
        await db.flush()

        customer = Customer(
            user_id=client.id,
            full_name="Тестовый клиент",
        )
        db.add(customer)
        await db.flush()

        conversation = Conversation(
            customer_id=customer.id,
            status="open",
        )
        db.add(conversation)
        await db.commit()

        data = {
            "client_id": client.id,
            "customer_id": customer.id,
            "conversation_id": conversation.id,
            "operator1_id": operator1.id,
            "operator2_id": operator2.id,
        }

    try:
        yield data
    finally:
        async with factory() as db, db.begin():
            ticket_ids = select(SupportTicket.id).where(
                SupportTicket.conversation_id == data["conversation_id"]
            )

            await db.execute(
                delete(TicketAssignmentHistory).where(
                    TicketAssignmentHistory.ticket_id.in_(ticket_ids)
                )
            )

            await db.execute(
                delete(SupportTicket).where(
                    SupportTicket.conversation_id == data["conversation_id"]
                )
            )

            await db.execute(
                delete(Conversation).where(Conversation.id == data["conversation_id"])
            )

            await db.execute(delete(Customer).where(Customer.id == data["customer_id"]))

            await db.execute(
                delete(User).where(
                    User.id.in_(
                        [
                            data["client_id"],
                            data["operator1_id"],
                            data["operator2_id"],
                        ]
                    )
                )
            )


@pytest.mark.asyncio
async def test_create_ticket_and_list_queue(ticket_data):
    factory = get_session_factory()

    async with factory() as db:
        ticket = await create_support_ticket(
            db,
            conversation_id=ticket_data["conversation_id"],
            customer_id=ticket_data["customer_id"],
            reason="AI не смог ответить",
        )

        assert ticket.status == "pending"
        assert ticket.assigned_operator_id is None

    async with factory() as db:
        operator = await db.get(
            User,
            ticket_data["operator1_id"],
        )

        queue = await get_pending_tickets(
            db,
            operator=operator,
        )

        assert any(item.id == ticket.id for item in queue)

        conversation = await db.get(
            Conversation,
            ticket_data["conversation_id"],
        )

        assert conversation.status == "waiting_operator"


@pytest.mark.asyncio
async def test_claim_ticket_creates_history(ticket_data):
    factory = get_session_factory()

    async with factory() as db:
        ticket = await create_support_ticket(
            db,
            conversation_id=ticket_data["conversation_id"],
            customer_id=ticket_data["customer_id"],
            reason="Нужна консультация оператора",
        )

    async with factory() as db:
        operator = await db.get(
            User,
            ticket_data["operator1_id"],
        )

        claimed = await claim_support_ticket(
            db,
            ticket_id=ticket.id,
            operator=operator,
        )

        assert claimed.status == "in_progress"
        assert claimed.assigned_operator_id == operator.id
        assert claimed.assigned_at is not None

        history = (
            await db.scalars(
                select(TicketAssignmentHistory).where(
                    TicketAssignmentHistory.ticket_id == ticket.id
                )
            )
        ).all()

        assert len(history) == 1
        assert history[0].operator_id == operator.id
        assert history[0].released_at is None


@pytest.mark.asyncio
async def test_duplicate_ticket_is_rejected(ticket_data):
    factory = get_session_factory()

    async with factory() as db:
        await create_support_ticket(
            db,
            conversation_id=ticket_data["conversation_id"],
            customer_id=ticket_data["customer_id"],
            reason="Первое обращение",
        )

    async with factory() as db:
        with pytest.raises(TicketAlreadyActiveError):
            await create_support_ticket(
                db,
                conversation_id=ticket_data["conversation_id"],
                customer_id=ticket_data["customer_id"],
                reason="Повторное обращение",
            )


@pytest.mark.asyncio
async def test_client_cannot_claim_ticket(ticket_data):
    factory = get_session_factory()

    async with factory() as db:
        ticket = await create_support_ticket(
            db,
            conversation_id=ticket_data["conversation_id"],
            customer_id=ticket_data["customer_id"],
            reason="Нужен оператор",
        )

    async with factory() as db:
        client = await db.get(
            User,
            ticket_data["client_id"],
        )

        with pytest.raises(TicketAccessDeniedError):
            await claim_support_ticket(
                db,
                ticket_id=ticket.id,
                operator=client,
            )


@pytest.mark.asyncio
async def test_two_operators_cannot_claim_same_ticket(
    ticket_data,
):
    factory = get_session_factory()

    async with factory() as db:
        ticket = await create_support_ticket(
            db,
            conversation_id=ticket_data["conversation_id"],
            customer_id=ticket_data["customer_id"],
            reason="Конкурентное назначение",
        )

    async def try_claim(operator_id: uuid.UUID) -> bool:
        async with factory() as db:
            operator = await db.get(User, operator_id)

            try:
                await claim_support_ticket(
                    db,
                    ticket_id=ticket.id,
                    operator=operator,
                )
                return True
            except TicketAlreadyClaimedError:
                return False

    results = await asyncio.gather(
        try_claim(ticket_data["operator1_id"]),
        try_claim(ticket_data["operator2_id"]),
    )

    assert results.count(True) == 1
    assert results.count(False) == 1

    async with factory() as db:
        history = (
            await db.scalars(
                select(TicketAssignmentHistory).where(
                    TicketAssignmentHistory.ticket_id == ticket.id
                )
            )
        ).all()

        assert len(history) == 1
        assert history[0].released_at is None


@pytest.mark.asyncio
async def test_other_customer_cannot_create_ticket(
    ticket_data,
):
    factory = get_session_factory()

    async with factory() as db:
        with pytest.raises(TicketNotFoundError):
            await create_support_ticket(
                db,
                conversation_id=ticket_data["conversation_id"],
                customer_id=uuid.uuid4(),
                reason="Чужой диалог",
            )
