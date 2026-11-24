import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.infrastructure.database.models import (
    SupportTicket,
    TicketAssignmentHistory,
    TicketTransferRequest,
    User,
)


class TransferNotFoundError(Exception):
    """Запрос на передачу или обращение не найден."""


class TransferPermissionError(Exception):
    """Оператор не имеет права выполнять действие."""


class TransferConflictError(Exception):
    """Текущее состояние обращения не позволяет выполнить действие."""


class InvalidTransferTargetError(Exception):
    """Некорректный получатель обращения."""


async def request_transfer(
    db: AsyncSession,
    *,
    ticket_id: uuid.UUID,
    from_operator: User,
    to_operator_id: uuid.UUID,
    reason: str,
) -> TicketTransferRequest:
    """
    Создать запрос на передачу обращения другому оператору.

    Ответственный оператор не изменяется до принятия запроса.
    """

    if from_operator.role != "operator":
        raise TransferPermissionError

    reason = reason.strip()

    if not 5 <= len(reason) <= 2000:
        raise ValueError("Transfer reason must contain 5-2000 characters")

    if from_operator.id == to_operator_id:
        raise InvalidTransferTargetError("Cannot transfer ticket to yourself")

    # Блокируем обращение, чтобы исключить конкурирующие передачи.
    ticket = await db.scalar(
        select(SupportTicket)
        .where(SupportTicket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if ticket is None:
        raise TransferNotFoundError

    if ticket.status != "in_progress":
        raise TransferConflictError("Ticket is not in progress")

    # Передачу может инициировать только ответственный оператор.
    if ticket.assigned_operator_id != from_operator.id:
        raise TransferPermissionError

    # Получатель должен существовать и иметь роль operator.
    target_operator = await db.get(User, to_operator_id)

    if target_operator is None or target_operator.role != "operator":
        raise InvalidTransferTargetError("Target operator not found")

    # Одновременно допускается только один ожидающий запрос.
    existing_transfer = await db.scalar(
        select(TicketTransferRequest.id).where(
            TicketTransferRequest.ticket_id == ticket_id,
            TicketTransferRequest.status == "pending",
        )
    )

    if existing_transfer is not None:
        raise TransferConflictError("Ticket already has a pending transfer")

    transfer = TicketTransferRequest(
        ticket_id=ticket.id,
        from_operator_id=from_operator.id,
        to_operator_id=to_operator_id,
        reason=reason,
        status="pending",
    )

    db.add(transfer)

    await db.commit()
    await db.refresh(transfer)

    return transfer


async def _lock_transfer_for_response(
    db: AsyncSession,
    *,
    transfer_id: uuid.UUID,
    operator: User,
) -> tuple[SupportTicket, TicketTransferRequest]:
    """
    Заблокировать обращение и запрос передачи для обработки решения.

    Порядок блокировок:
    1. SupportTicket.
    2. TicketTransferRequest.

    Во всех операциях сохраняем один порядок блокировок,
    чтобы уменьшить риск взаимных блокировок.
    """

    if operator.role != "operator":
        raise TransferPermissionError

    # Сначала получаем ID обращения без блокировки.
    ticket_id = await db.scalar(
        select(TicketTransferRequest.ticket_id).where(
            TicketTransferRequest.id == transfer_id
        )
    )

    if ticket_id is None:
        raise TransferNotFoundError

    ticket = await db.scalar(
        select(SupportTicket)
        .where(SupportTicket.id == ticket_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if ticket is None:
        raise TransferNotFoundError

    transfer = await db.scalar(
        select(TicketTransferRequest)
        .where(TicketTransferRequest.id == transfer_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if transfer is None:
        raise TransferNotFoundError

    # Принять или отклонить передачу может только получатель.
    if transfer.to_operator_id != operator.id:
        raise TransferPermissionError

    if transfer.status != "pending":
        raise TransferConflictError("Transfer is already processed")

    # Проверяем, что исходный оператор всё ещё ответственный.
    if (
        ticket.status != "in_progress"
        or ticket.assigned_operator_id != transfer.from_operator_id
    ):
        raise TransferConflictError("Ticket assignment has changed")

    return ticket, transfer


async def accept_transfer(
    db: AsyncSession,
    *,
    transfer_id: uuid.UUID,
    operator: User,
) -> TicketTransferRequest:
    """
    Принять обращение от другого оператора.

    В одной транзакции:
    - закрывается старое назначение;
    - создаётся новое назначение;
    - меняется текущий ответственный;
    - запрос передачи получает статус accepted.
    """

    ticket, transfer = await _lock_transfer_for_response(
        db=db,
        transfer_id=transfer_id,
        operator=operator,
    )

    # Находим действующее назначение прежнего оператора.
    current_assignment = await db.scalar(
        select(TicketAssignmentHistory)
        .where(
            TicketAssignmentHistory.ticket_id == ticket.id,
            TicketAssignmentHistory.operator_id == transfer.from_operator_id,
            TicketAssignmentHistory.released_at.is_(None),
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if current_assignment is None:
        raise TransferConflictError("Active assignment not found")

    now = datetime.now(UTC)

    # Завершаем ответственность предыдущего оператора.
    current_assignment.released_at = now

    # Назначаем нового ответственного.
    ticket.assigned_operator_id = operator.id
    ticket.assigned_at = now

    # Сначала обновляем записи в PostgreSQL.
    # Это освобождает частичный уникальный индекс
    # uq_ticket_active_assignment для нового назначения.
    await db.flush()

    # Создаём запись в истории назначений.
    new_assignment = TicketAssignmentHistory(
        ticket_id=ticket.id,
        operator_id=operator.id,
        assigned_at=now,
    )

    db.add(new_assignment)

    # Фиксируем принятие запроса передачи.
    transfer.status = "accepted"
    transfer.responded_at = now

    # Все изменения сохраняются атомарно.
    await db.commit()
    await db.refresh(transfer)

    return transfer


async def reject_transfer(
    db: AsyncSession,
    *,
    transfer_id: uuid.UUID,
    operator: User,
) -> TicketTransferRequest:
    """
    Отклонить передачу обращения.

    Текущий ответственный оператор не изменяется.
    """

    _, transfer = await _lock_transfer_for_response(
        db=db,
        transfer_id=transfer_id,
        operator=operator,
    )

    transfer.status = "rejected"
    transfer.responded_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(transfer)

    return transfer


async def get_incoming_transfers(
    db: AsyncSession,
    *,
    operator: User,
    limit: int = 50,
) -> list[TicketTransferRequest]:
    """
    Получить ожидающие запросы передачи для оператора.

    Возвращаются только запросы, адресованные
    текущему авторизованному оператору.
    """

    # Доступ разрешён только операторам.
    if operator.role != "operator":
        raise TransferPermissionError

    # Ограничиваем максимальное количество результатов.
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be between 1 and 100")

    # Получаем ожидающие передачи в порядке создания.
    result = await db.scalars(
        select(TicketTransferRequest)
        .where(
            TicketTransferRequest.to_operator_id == operator.id,
            TicketTransferRequest.status == "pending",
        )
        .order_by(
            TicketTransferRequest.created_at.asc(),
            TicketTransferRequest.id.asc(),
        )
        .limit(limit)
    )

    return list(result.all())
