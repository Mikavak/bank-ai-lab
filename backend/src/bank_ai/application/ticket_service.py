
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.infrastructure.database.models import (
    Conversation,
    SupportTicket,
    TicketAssignmentHistory,
    User,
)


class TicketNotFoundError(Exception):
    pass


class TicketAlreadyActiveError(Exception):
    pass


class TicketAlreadyClaimedError(Exception):
    pass


class TicketAccessDeniedError(Exception):
    pass


class ConversationClosedError(Exception):
    pass


async def create_support_ticket(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    customer_id: uuid.UUID,
    reason: str,
) -> SupportTicket:
    reason = reason.strip()

    if not reason:
        raise ValueError("Ticket reason cannot be empty")

    conversation = await db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.customer_id == customer_id,
        )
        .with_for_update()
    )

    if conversation is None:
        raise TicketNotFoundError("Conversation not found")

    if conversation.status == "closed":
        raise ConversationClosedError

    existing_ticket = await db.scalar(
        select(SupportTicket.id).where(
            SupportTicket.conversation_id == conversation_id,
            SupportTicket.status.in_(("pending", "in_progress")),
        )
    )

    if existing_ticket is not None:
        raise TicketAlreadyActiveError

    ticket = SupportTicket(
        conversation_id=conversation_id,
        reason=reason,
        status="pending",
    )

    conversation.status = "waiting_operator"

    db.add(ticket)

    await db.commit()
    await db.refresh(ticket)

    return ticket


async def get_pending_tickets(
    db: AsyncSession,
    *,
    operator: User,
    limit: int = 50,
) -> list[SupportTicket]:
    if operator.role != "operator":
        raise TicketAccessDeniedError

    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")

    result = await db.scalars(
        select(SupportTicket)
        .where(SupportTicket.status == "pending")
        .order_by(
            SupportTicket.created_at.asc(),
            SupportTicket.id.asc(),
        )
        .limit(limit)
    )

    return list(result.all())


async def claim_support_ticket(
    db: AsyncSession,
    *,
    ticket_id: uuid.UUID,
    operator: User,
) -> SupportTicket:
    if operator.role != "operator":
        raise TicketAccessDeniedError

    ticket = await db.scalar(
        select(SupportTicket)
        .where(SupportTicket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if ticket is None:
        raise TicketNotFoundError

    if (
        ticket.status != "pending"
        or ticket.assigned_operator_id is not None
    ):
        raise TicketAlreadyClaimedError

    now = datetime.now(timezone.utc)

    ticket.status = "in_progress"
    ticket.assigned_operator_id = operator.id
    ticket.assigned_at = now

    assignment = TicketAssignmentHistory(
        ticket_id=ticket.id,
        operator_id=operator.id,
        assigned_at=now,
    )

    db.add(assignment)

    await db.commit()
    await db.refresh(ticket)

    return ticket
