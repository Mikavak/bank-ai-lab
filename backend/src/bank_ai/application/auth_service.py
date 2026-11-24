
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from bank_ai.core.security import verify_password
from bank_ai.infrastructure.database.models import User


async def authenticate_user(
    db: AsyncSession,
    email: str,
    password: str,
) -> User | None:
    normalized_email = email.strip().lower()

    user = await db.scalar(
        select(User).where(
            User.email == normalized_email
        )
    )

    if user is None:
        return None

    is_valid = await run_in_threadpool(
        verify_password,
        password,
        user.password_hash,
    )

    if not is_valid:
        return None

    return user
