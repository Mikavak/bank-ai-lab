from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.core.config import Settings, get_settings
from bank_ai.core.security import (
    InvalidAccessTokenError,
    verify_access_token,
)
from bank_ai.infrastructure.database.models import User
from bank_ai.infrastructure.database.session import get_db

bearer_scheme = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing authentication credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None:
        raise unauthorized()

    try:
        user_id = verify_access_token(
            credentials.credentials,
            secret_key=settings.jwt_secret_key.get_secret_value(),
        )
    except InvalidAccessTokenError:
        raise unauthorized() from None

    user = await db.get(User, user_id)

    if user is None:
        raise unauthorized()

    return user
