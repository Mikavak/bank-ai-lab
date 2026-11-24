import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.infrastructure.database.models import (
    Conversation,
    Message,
)


class ConversationNotFoundError(Exception):
    pass


class ConversationClosedError(Exception):
    pass


async def create_client_message(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    customer_id: uuid.UUID,
    sender_user_id: uuid.UUID,
    content: str,
) -> Message:
    statement = (
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.customer_id == customer_id,
        )
        .with_for_update()
    )

    conversation = await db.scalar(statement)

    if conversation is None:
        raise ConversationNotFoundError

    if conversation.status == "closed":
        raise ConversationClosedError

    message = Message(
        conversation_id=conversation.id,
        sender_user_id=sender_user_id,
        role="client",
        content=content,
    )

    db.add(message)

    await db.commit()
    await db.refresh(message)

    return message


async def get_client_messages(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    customer_id: uuid.UUID,
    limit: int = 50,
    offset: int = 0,
) -> list[Message]:
    conversation = await db.scalar(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.customer_id == customer_id,
        )
    )

    if conversation is None:
        raise ConversationNotFoundError

    statement = (
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(
            Message.created_at.asc(),
            Message.id.asc(),
        )
        .limit(limit)
        .offset(offset)
    )

    result = await db.scalars(statement)

    return list(result.all())
