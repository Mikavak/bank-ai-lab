import asyncio

from sqlalchemy import select

from bank_ai.core.config import get_settings
from bank_ai.core.security import hash_password
from bank_ai.infrastructure.database.models import Customer, User
from bank_ai.infrastructure.database.session import (
    get_engine,
    get_session_factory,
)


async def seed_demo() -> None:
    settings = get_settings()

    if settings.app_env != "local":
        raise RuntimeError("Demo seed is allowed only locally")

    if settings.demo_client_password is None:
        raise ValueError("DEMO_CLIENT_PASSWORD is required")

    password = settings.demo_client_password.get_secret_value()

    if len(password) < 12:
        raise ValueError("Demo password must contain at least 12 characters")

    email = settings.demo_client_email.strip().lower()

    try:
        async with get_session_factory()() as session:
            async with session.begin():
                user = await session.scalar(select(User).where(User.email == email))

                password_digest = await asyncio.to_thread(
                    hash_password,
                    password,
                )

                if user is None:
                    user = User(
                        email=email,
                        password_hash=password_digest,
                        role="client",
                    )
                    session.add(user)
                    await session.flush()
                else:
                    if user.role != "client":
                        raise ValueError("Existing user is not a client")

                    user.password_hash = password_digest

                customer = await session.scalar(
                    select(Customer).where(Customer.user_id == user.id)
                )

                if customer is None:
                    customer = Customer(
                        user_id=user.id,
                        full_name="Тестовый клиент",
                        external_customer_id="demo-client-001",
                    )
                    session.add(customer)

            print(f"Demo user ready: {email}")
            print(f"User ID: {user.id}")

    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(seed_demo())
