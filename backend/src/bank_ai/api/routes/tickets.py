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
from bank_ai.api.schemas.messages import MessageResponse
from bank_ai.api.schemas.operator_messages import (
    OperatorMessageCreateRequest,
)
from bank_ai.api.schemas.tickets import (
    TicketCreateRequest,
    TicketResponse,
)
from bank_ai.application.operator_message_service import (
    OperatorConversationClosedError,
    OperatorMessagesAccessDeniedError,
    OperatorTicketNotFoundError,
    create_operator_message,
    get_operator_ticket_messages,
)
from bank_ai.application.ticket_service import (
    ConversationClosedError,
    TicketAccessDeniedError,
    TicketAlreadyActiveError,
    TicketAlreadyClaimedError,
    TicketNotFoundError,
    TicketResolutionConflictError,
    claim_support_ticket,
    create_support_ticket,
    get_assigned_tickets,
    get_pending_tickets,
    resolve_support_ticket,
)
from bank_ai.infrastructure.database.models import Customer, User
from bank_ai.infrastructure.database.session import get_db

client_router = APIRouter(
    prefix="/conversations",
    tags=["Support Tickets"],
)

operator_router = APIRouter(
    prefix="/operator/tickets",
    tags=["Operator Tickets"],
)


@client_router.post(
    "/{conversation_id}/tickets",
    response_model=TicketResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ticket(
    conversation_id: uuid.UUID,
    data: TicketCreateRequest,
    customer: Customer = Depends(get_current_customer),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    try:
        ticket = await create_support_ticket(
            db=db,
            conversation_id=conversation_id,
            customer_id=customer.id,
            reason=data.reason,
        )

    except TicketNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        ) from None

    except ConversationClosedError:
        raise HTTPException(
            status_code=409,
            detail="Conversation is closed",
        ) from None

    except TicketAlreadyActiveError:
        raise HTTPException(
            status_code=409,
            detail="Conversation already has an active ticket",
        ) from None

    return TicketResponse.model_validate(ticket)


@operator_router.get(
    "",
    response_model=list[TicketResponse],
)
async def list_pending_tickets(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TicketResponse]:
    try:
        tickets = await get_pending_tickets(
            db=db,
            operator=current_user,
            limit=limit,
        )

    except TicketAccessDeniedError:
        raise HTTPException(
            status_code=403,
            detail="Operator access required",
        ) from None

    return [TicketResponse.model_validate(ticket) for ticket in tickets]


@operator_router.get(
    "/assigned",
    response_model=list[TicketResponse],
)
async def list_assigned_tickets(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TicketResponse]:
    """
    Получить обращения текущего оператора.

    Возвращаются только обращения со статусом
    in_progress, назначенные этому оператору.
    """

    try:
        tickets = await get_assigned_tickets(
            db=db,
            operator=current_user,
            limit=limit,
        )

    except TicketAccessDeniedError:
        raise HTTPException(
            status_code=403,
            detail="Operator access required",
        ) from None

    return [TicketResponse.model_validate(ticket) for ticket in tickets]


@operator_router.post(
    "/{ticket_id}/claim",
    response_model=TicketResponse,
)
async def claim_ticket(
    ticket_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    try:
        ticket = await claim_support_ticket(
            db=db,
            ticket_id=ticket_id,
            operator=current_user,
        )

    except TicketAccessDeniedError:
        raise HTTPException(
            status_code=403,
            detail="Operator access required",
        ) from None

    except TicketNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found",
        ) from None

    except TicketAlreadyClaimedError:
        raise HTTPException(
            status_code=409,
            detail="Ticket is no longer available",
        ) from None

    return TicketResponse.model_validate(ticket)


@operator_router.get(
    "/{ticket_id}/messages",
    response_model=list[MessageResponse],
)
async def list_operator_ticket_messages(
    ticket_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[MessageResponse]:
    """
    Получить полную историю переписки по обращению.

    Доступ разрешён только текущему
    ответственному оператору.
    """

    try:
        messages = await get_operator_ticket_messages(
            db=db,
            ticket_id=ticket_id,
            operator=current_user,
            limit=limit,
            offset=offset,
        )

    except OperatorMessagesAccessDeniedError:
        raise HTTPException(
            status_code=403,
            detail="Operator access required",
        ) from None

    except OperatorTicketNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found",
        ) from None

    return [MessageResponse.model_validate(message) for message in messages]


@operator_router.post(
    "/{ticket_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_operator_message(
    ticket_id: uuid.UUID,
    data: OperatorMessageCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    """
    Отправить сообщение клиенту от текущего оператора.

    Идентификатор отправителя и роль сообщения
    определяются на основании JWT.
    """

    try:
        message = await create_operator_message(
            db=db,
            ticket_id=ticket_id,
            operator=current_user,
            content=data.content,
        )

    except OperatorMessagesAccessDeniedError:
        raise HTTPException(
            status_code=403,
            detail="Operator access required",
        ) from None

    except OperatorTicketNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found",
        ) from None

    except OperatorConversationClosedError:
        raise HTTPException(
            status_code=409,
            detail="Conversation is closed",
        ) from None

    return MessageResponse.model_validate(message)


@operator_router.post(
    "/{ticket_id}/resolve",
    response_model=TicketResponse,
)
async def resolve_ticket(
    ticket_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TicketResponse:
    """
    Завершить обращение клиента.

    Доступ имеет только оператор, за которым
    закреплено активное обращение.
    """

    try:
        ticket = await resolve_support_ticket(
            db=db,
            ticket_id=ticket_id,
            operator=current_user,
        )

    except TicketAccessDeniedError:
        raise HTTPException(
            status_code=403,
            detail="Operator access required",
        ) from None

    except TicketNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found",
        ) from None

    except TicketResolutionConflictError:
        raise HTTPException(
            status_code=409,
            detail="Ticket cannot be resolved",
        ) from None

    return TicketResponse.model_validate(ticket)
