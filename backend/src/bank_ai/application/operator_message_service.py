import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.infrastructure.database.models import (
    Conversation,
    Message,
    SupportTicket,
    User,
)


class OperatorMessagesAccessDeniedError(Exception):
    """Пользователь не имеет роли оператора."""


class OperatorTicketNotFoundError(Exception):
    """Обращение не найдено или не принадлежит оператору."""


class OperatorConversationClosedError(Exception):
    """Диалог закрыт, отправка сообщений запрещена."""


async def get_operator_ticket_messages(
    db: AsyncSession,
    *,
    ticket_id: uuid.UUID,
    operator: User,
    limit: int = 50,
    offset: int = 0,
) -> list[Message]:
    """
    Получить историю переписки по обращению.

    В историю входят сообщения клиента, AI-ассистента
    и всех операторов, ранее участвовавших в диалоге.

    Доступ имеет только текущий ответственный оператор.
    """

    # Проверяем роль авторизованного пользователя.
    if operator.role != "operator":
        raise OperatorMessagesAccessDeniedError

    # Проверяем параметры пагинации.
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("Invalid pagination parameters")

    # Находим диалог только среди активных обращений,
    # закреплённых за текущим оператором.
    conversation_id = await db.scalar(
        select(SupportTicket.conversation_id).where(
            SupportTicket.id == ticket_id,
            SupportTicket.assigned_operator_id == operator.id,
            SupportTicket.status == "in_progress",
        )
    )

    if conversation_id is None:
        raise OperatorTicketNotFoundError

    # Получаем все сообщения диалога независимо от роли
    # отправителя: client, assistant или operator.
    result = await db.scalars(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(
            Message.created_at.asc(),
            Message.id.asc(),
        )
        .offset(offset)
        .limit(limit)
    )

    return list(result.all())


async def create_operator_message(
    db: AsyncSession,
    *,
    ticket_id: uuid.UUID,
    operator: User,
    content: str,
) -> Message:
    """
    Сохранить сообщение текущего ответственного оператора.

    Проверяет роль, назначение оператора и статус диалога.
    """

    # Отправлять сообщения могут только операторы.
    if operator.role != "operator":
        raise OperatorMessagesAccessDeniedError

    # Нормализуем и проверяем текст сообщения.
    content = content.strip()

    if not 1 <= len(content) <= 4000:
        raise ValueError("Message must contain 1-4000 characters")

    # Блокируем обращение до завершения транзакции.
    # Это исключает одновременное изменение назначения
    # и сохранение сообщения от прежнего оператора.
    ticket = await db.scalar(
        select(SupportTicket)
        .where(
            SupportTicket.id == ticket_id,
            SupportTicket.assigned_operator_id == operator.id,
            SupportTicket.status == "in_progress",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if ticket is None:
        raise OperatorTicketNotFoundError

    # Проверяем состояние связанного диалога.
    conversation_status = await db.scalar(
        select(Conversation.status).where(Conversation.id == ticket.conversation_id)
    )

    if conversation_status is None:
        raise OperatorTicketNotFoundError

    if conversation_status == "closed":
        raise OperatorConversationClosedError

    # Создаём сообщение в существующем диалоге.
    # Отправитель и роль определяются сервером.
    message = Message(
        conversation_id=ticket.conversation_id,
        sender_user_id=operator.id,
        role="operator",
        content=content,
    )

    db.add(message)

    # Сохраняем сообщение и освобождаем блокировку.
    await db.commit()

    # Получаем значения, сформированные PostgreSQL.
    await db.refresh(message)

    return message
