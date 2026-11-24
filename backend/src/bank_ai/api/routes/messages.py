import uuid

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.api.dependencies.auth import get_current_user
from bank_ai.api.dependencies.customer import get_current_customer
from bank_ai.api.schemas.messages import (
    MessageCreateRequest,
    MessageResponse,
)
from bank_ai.application.message_service import (
    ConversationClosedError,
    ConversationNotFoundError,
    create_client_message,
    get_client_messages,
)
from bank_ai.infrastructure.database.models import Customer, User
from bank_ai.infrastructure.database.session import get_db

router = APIRouter(
    prefix="/conversations",
    tags=["Messages"],
)


@router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    conversation_id: uuid.UUID,
    data: MessageCreateRequest,
    current_user: User = Depends(get_current_user),
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    try:
        message = await create_client_message(
            db=db,
            conversation_id=conversation_id,
            customer_id=customer.id,
            sender_user_id=current_user.id,
            content=data.content,
        )

    except ConversationNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        ) from None

    except ConversationClosedError:
        raise HTTPException(
            status_code=409,
            detail="Conversation is closed",
        ) from None

    return MessageResponse.model_validate(message)


@router.get(
    "/{conversation_id}/messages",
    response_model=list[MessageResponse],
)
async def list_messages(
    conversation_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
) -> list[MessageResponse]:
    try:
        messages = await get_client_messages(
            db=db,
            conversation_id=conversation_id,
            customer_id=customer.id,
            limit=limit,
            offset=offset,
        )

    except ConversationNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        ) from None

    return [MessageResponse.model_validate(message) for message in messages]
