import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.infrastructure.database.models import (
    Conversation,
    SupportTicket,
    TicketAssignmentHistory,
    TicketTransferRequest,
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


class TicketResolutionConflictError(Exception):
    """Текущее состояние обращения не позволяет его завершить."""


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

    if ticket.status != "pending" or ticket.assigned_operator_id is not None:
        raise TicketAlreadyClaimedError

    now = datetime.now(UTC)

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


async def get_assigned_tickets(
    db: AsyncSession,
    *,
    operator: User,
    limit: int = 50,
) -> list[SupportTicket]:
    """
    Получить активные обращения текущего оператора.

    Возвращаются только обращения, за которые
    отвечает авторизованный оператор.
    """

    # Проверяем роль пользователя.
    if operator.role != "operator":
        raise TicketAccessDeniedError

    # Ограничиваем количество возвращаемых записей.
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")

    # Получаем назначенные обращения.
    result = await db.scalars(
        select(SupportTicket)
        .where(
            SupportTicket.assigned_operator_id == operator.id,
            SupportTicket.status == "in_progress",
        )
        .order_by(
            SupportTicket.assigned_at.desc(),
            SupportTicket.id.desc(),
        )
        .limit(limit)
    )

    return list(result.all())


async def resolve_support_ticket(
    db: AsyncSession,
    *,
    ticket_id: uuid.UUID,
    operator: User,
) -> SupportTicket:
    """
    Завершить обращение, решение которого найдено.

    Действия выполняются в одной транзакции:
    - проверяется текущий ответственный оператор;
    - закрывается история действующего назначения;
    - отменяется ожидающая передача;
    - обращение переводится в resolved;
    - диалог возвращается в open.
    """

    # Завершать обращения могут только операторы.
    if operator.role != "operator":
        raise TicketAccessDeniedError

    # Блокируем обращение, чтобы исключить одновременную
    # передачу другому оператору или повторное завершение.
    ticket = await db.scalar(
        select(SupportTicket)
        .where(SupportTicket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if ticket is None:
        raise TicketNotFoundError

    # Не раскрываем постороннему оператору состояние обращения.
    if ticket.assigned_operator_id != operator.id:
        raise TicketNotFoundError

    # Завершать можно только обращения в работе.
    if ticket.status != "in_progress":
        raise TicketResolutionConflictError

    # Блокируем связанный диалог для изменения статуса.
    conversation = await db.scalar(
        select(Conversation)
        .where(Conversation.id == ticket.conversation_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if conversation is None:
        raise TicketNotFoundError

    if conversation.status == "closed":
        raise TicketResolutionConflictError

    # Получаем активное назначение оператора.
    assignment = await db.scalar(
        select(TicketAssignmentHistory)
        .where(
            TicketAssignmentHistory.ticket_id == ticket.id,
            TicketAssignmentHistory.operator_id == operator.id,
            TicketAssignmentHistory.released_at.is_(None),
        )
        .with_for_update()
    )

    if assignment is None:
        raise TicketResolutionConflictError

    # Находим ожидающую передачу, если она существует.
    pending_transfer = await db.scalar(
        select(TicketTransferRequest)
        .where(
            TicketTransferRequest.ticket_id == ticket.id,
            TicketTransferRequest.status == "pending",
        )
        .with_for_update()
    )

    now = datetime.now(UTC)

    # Закрываем период ответственности оператора.
    assignment.released_at = now

    # Отменяем передачу: обращение уже решено.
    if pending_transfer is not None:
        pending_transfer.status = "cancelled"
        pending_transfer.responded_at = now

    # Сохраняем финальное состояние обращения.
    ticket.status = "resolved"
    ticket.resolved_at = now

    # Разрешаем клиенту продолжить общение в диалоге.
    conversation.status = "open"

    # Все изменения фиксируются атомарно.
    await db.commit()
    await db.refresh(ticket)

    return ticket
