import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.infrastructure.database.models import (
    Conversation,
    SupportTicket,
)


class ConversationCloseNotFoundError(Exception):
    """Диалог не найден или не принадлежит клиенту."""


class ConversationCloseConflictError(Exception):
    """Диалог нельзя закрыть из-за активного обращения."""


async def close_client_conversation(
    db: AsyncSession,
    *,
    conversation_id: uuid.UUID,
    customer_id: uuid.UUID,
) -> Conversation:
    """
    Закрыть диалог клиента.

    Правила:
    - клиент может закрыть только собственный диалог;
    - активные обращения должны быть завершены;
    - повторное закрытие возвращает тот же результат;
    - сообщения и история обращений не удаляются.
    """

    # Блокируем диалог для защиты от одновременного
    # закрытия и создания нового обращения.
    conversation = await db.scalar(
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.customer_id == customer_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if conversation is None:
        raise ConversationCloseNotFoundError

    # Повторное закрытие выполняется идемпотентно.
    if conversation.status == "closed":
        return conversation

    # Проверяем наличие незавершённых обращений.
    active_ticket_id = await db.scalar(
        select(SupportTicket.id)
        .where(
            SupportTicket.conversation_id == conversation.id,
            SupportTicket.status.in_(("pending", "in_progress")),
        )
        .limit(1)
    )

    if active_ticket_id is not None:
        raise ConversationCloseConflictError(
            "Conversation has an active support ticket"
        )

    # Переводим диалог в окончательное состояние.
    conversation.status = "closed"

    await db.commit()
    await db.refresh(conversation)

    return conversation
