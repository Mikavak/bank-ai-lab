
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.infrastructure.database.models import Conversation


async def create_conversation(
    db: AsyncSession,
    customer_id: uuid.UUID,
) -> Conversation:
    conversation = Conversation(
        customer_id=customer_id,
        status="open",
    )

    db.add(conversation)

    await db.commit()
    await db.refresh(conversation)

    return conversation


async def get_customer_conversations(
    db: AsyncSession,
    customer_id: uuid.UUID,
) -> list[Conversation]:
    statement = (
        select(Conversation)
        .where(Conversation.customer_id == customer_id)
        .order_by(
            Conversation.created_at.desc(),
            Conversation.id.desc(),
        )
    )

    result = await db.scalars(statement)

    return list(result.all())
