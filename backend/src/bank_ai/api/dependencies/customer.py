
from fastapi import Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.api.dependencies.auth import get_current_user
from bank_ai.infrastructure.database.models import Customer, User
from bank_ai.infrastructure.database.session import get_db


async def get_current_customer(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Customer:
    if current_user.role != "client":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only clients can access this resource",
        )

    customer = await db.scalar(
        select(Customer).where(
            Customer.user_id == current_user.id
        )
    )

    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Customer profile not found",
        )

    return customer
