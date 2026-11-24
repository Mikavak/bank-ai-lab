
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.api.dependencies.customer import get_current_customer
from bank_ai.api.schemas.conversations import ConversationResponse
from bank_ai.application.conversation_service import (
    create_conversation,
    get_customer_conversations,
)
from bank_ai.infrastructure.database.models import Customer
from bank_ai.infrastructure.database.session import get_db


router = APIRouter(
    prefix="/conversations",
    tags=["Conversations"],
)


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_new_conversation(
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
) -> ConversationResponse:
    conversation = await create_conversation(
        db=db,
        customer_id=customer.id,
    )

    return ConversationResponse.model_validate(conversation)


@router.get(
    "",
    response_model=list[ConversationResponse],
)
async def list_conversations(
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationResponse]:
    conversations = await get_customer_conversations(
        db=db,
        customer_id=customer.id,
    )

    return [
        ConversationResponse.model_validate(conversation)
        for conversation in conversations
    ]
