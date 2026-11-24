from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.api.dependencies.auth import get_current_user
from bank_ai.api.schemas.auth import CurrentUserResponse, LoginRequest, TokenResponse
from bank_ai.application.auth_service import authenticate_user
from bank_ai.core.config import Settings, get_settings
from bank_ai.core.security import create_access_token
from bank_ai.infrastructure.database.models import User
from bank_ai.infrastructure.database.session import get_db

router = APIRouter(
    prefix="/auth",
    tags=["Authentication"],
)


@router.post("/login", response_model=TokenResponse)
async def login(
    data: LoginRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    user = await authenticate_user(
        db=db,
        email=data.email,
        password=data.password,
    )

    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        user_id=user.id,
        secret_key=settings.jwt_secret_key.get_secret_value(),
        expires_minutes=settings.jwt_expire_minutes,
    )

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.jwt_expire_minutes * 60,
    )


@router.get("/me", response_model=CurrentUserResponse)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=current_user.id,
        email=current_user.email,
        role=current_user.role,
    )
