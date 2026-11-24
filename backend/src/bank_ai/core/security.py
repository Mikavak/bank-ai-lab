# Здесь токен содержит user_id в sub.
# Роль пользователя не записываем в JWT — в будущем будем получать актуальные права из БД.


import uuid
from datetime import UTC, datetime, timedelta

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

password_hash = PasswordHash.recommended()

JWT_ALGORITHM = "HS256"
JWT_ISSUER = "bank-ai-lab"
JWT_AUDIENCE = "bank-ai-api"


class InvalidAccessTokenError(Exception):
    pass


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(
    password: str,
    hashed_password: str,
) -> bool:
    return password_hash.verify(password, hashed_password)


def create_access_token(
    user_id: uuid.UUID,
    *,
    secret_key: str,
    expires_minutes: int = 30,
) -> str:
    if expires_minutes <= 0:
        raise ValueError("Token lifetime must be positive")

    now = datetime.now(UTC)

    payload = {
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
    }

    return jwt.encode(
        payload,
        secret_key,
        algorithm=JWT_ALGORITHM,
    )


def verify_access_token(
    token: str,
    *,
    secret_key: str,
) -> uuid.UUID:
    try:
        payload = jwt.decode(
            token,
            secret_key,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={
                "require": ["sub", "exp", "iat", "iss", "aud"],
            },
        )

        return uuid.UUID(payload["sub"])

    except (InvalidTokenError, ValueError, TypeError, KeyError) as exc:
        raise InvalidAccessTokenError("Invalid access token") from exc
