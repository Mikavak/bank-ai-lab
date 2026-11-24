
import uuid

import pytest

from bank_ai.core.security import (
    InvalidAccessTokenError,
    create_access_token,
    hash_password,
    verify_access_token,
    verify_password,
)


TEST_SECRET = "local-test-signing-secret-at-least-32-bytes-long"


def test_password_hashing():
    password = "StrongTestPassword123!"

    hashed = hash_password(password)

    assert hashed != password
    assert verify_password(password, hashed)
    assert not verify_password("wrong-password", hashed)


def test_create_and_verify_jwt():
    user_id = uuid.uuid4()

    token = create_access_token(
        user_id,
        secret_key=TEST_SECRET,
    )

    decoded_user_id = verify_access_token(
        token,
        secret_key=TEST_SECRET,
    )

    assert decoded_user_id == user_id


def test_jwt_invalid_signature():
    token = create_access_token(
        uuid.uuid4(),
        secret_key=TEST_SECRET,
    )

    with pytest.raises(InvalidAccessTokenError):
        verify_access_token(
            token,
            secret_key="another-test-secret-key-at-least-32-bytes-long",
        )
