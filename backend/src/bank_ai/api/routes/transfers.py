import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.api.dependencies.auth import get_current_user
from bank_ai.api.schemas.transfers import (
    TransferCreateRequest,
    TransferResponse,
)
from bank_ai.application.ticket_transfer_service import (
    InvalidTransferTargetError,
    TransferConflictError,
    TransferNotFoundError,
    TransferPermissionError,
    accept_transfer,
    get_incoming_transfers,
    reject_transfer,
    request_transfer,
)
from bank_ai.infrastructure.database.models import User
from bank_ai.infrastructure.database.session import get_db

router = APIRouter(
    prefix="/operator",
    tags=["Operator Transfers"],
)


@router.post(
    "/tickets/{ticket_id}/transfer",
    response_model=TransferResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_transfer(
    ticket_id: uuid.UUID,
    data: TransferCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TransferResponse:
    """
    Создать запрос передачи обращения другому оператору.

    Передачу может инициировать только
    текущий ответственный оператор.
    """

    try:
        transfer = await request_transfer(
            db=db,
            ticket_id=ticket_id,
            from_operator=current_user,
            to_operator_id=data.to_operator_id,
            reason=data.reason,
        )

    except TransferPermissionError:
        raise HTTPException(
            status_code=403,
            detail="Operator has no permission",
        ) from None

    except TransferNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Ticket not found",
        ) from None

    except InvalidTransferTargetError:
        raise HTTPException(
            status_code=422,
            detail="Invalid target operator",
        ) from None

    except TransferConflictError:
        raise HTTPException(
            status_code=409,
            detail="Transfer cannot be created",
        ) from None

    return TransferResponse.model_validate(transfer)


@router.get(
    "/transfers/incoming",
    response_model=list[TransferResponse],
)
async def list_incoming_transfers(
    limit: int = Query(default=50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[TransferResponse]:
    """
    Получить входящие запросы передачи.

    Каждый оператор видит только адресованные ему
    запросы со статусом pending.
    """

    try:
        transfers = await get_incoming_transfers(
            db=db,
            operator=current_user,
            limit=limit,
        )

    except TransferPermissionError:
        raise HTTPException(
            status_code=403,
            detail="Operator access required",
        ) from None

    return [TransferResponse.model_validate(transfer) for transfer in transfers]


@router.post(
    "/transfers/{transfer_id}/accept",
    response_model=TransferResponse,
)
async def accept_incoming_transfer(
    transfer_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TransferResponse:
    """Принять передачу и стать ответственным за обращение."""

    try:
        transfer = await accept_transfer(
            db=db,
            transfer_id=transfer_id,
            operator=current_user,
        )

    except TransferPermissionError:
        raise HTTPException(
            status_code=403,
            detail="Operator has no permission",
        ) from None

    except TransferNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Transfer not found",
        ) from None

    except TransferConflictError:
        raise HTTPException(
            status_code=409,
            detail="Transfer cannot be accepted",
        ) from None

    return TransferResponse.model_validate(transfer)


@router.post(
    "/transfers/{transfer_id}/reject",
    response_model=TransferResponse,
)
async def reject_incoming_transfer(
    transfer_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TransferResponse:
    """Отклонить передачу без изменения ответственного."""

    try:
        transfer = await reject_transfer(
            db=db,
            transfer_id=transfer_id,
            operator=current_user,
        )

    except TransferPermissionError:
        raise HTTPException(
            status_code=403,
            detail="Operator has no permission",
        ) from None

    except TransferNotFoundError:
        raise HTTPException(
            status_code=404,
            detail="Transfer not found",
        ) from None

    except TransferConflictError:
        raise HTTPException(
            status_code=409,
            detail="Transfer cannot be rejected",
        ) from None

    return TransferResponse.model_validate(transfer)
