import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bank_ai.infrastructure.database.models import (
    Conversation,
    Customer,
    Message,
    User,
)
from bank_ai.infrastructure.database.session import get_engine


@pytest.mark.asyncio
async def test_create_conversation_with_messages():
    engine = get_engine()

    async with engine.connect() as connection:
        transaction = await connection.begin()

        try:
            async with AsyncSession(
                bind=connection,
                expire_on_commit=False,
                join_transaction_mode="create_savepoint",
            ) as session:
                client = User(
                    email=f"client-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="client",
                )

                operator = User(
                    email=f"operator-{uuid.uuid4()}@test.local",
                    password_hash="test_hash",
                    role="operator",
                )

                async with session.begin():
                    session.add_all([client, operator])
                    await session.flush()

                    customer = Customer(
                        user_id=client.id,
                        full_name="Тестовый клиент",
                    )
                    session.add(customer)
                    await session.flush()

                    conversation = Conversation(
                        customer_id=customer.id,
                        status="open",
                    )
                    session.add(conversation)
                    await session.flush()

                    session.add_all(
                        [
                            Message(
                                conversation_id=conversation.id,
                                sender_user_id=client.id,
                                role="client",
                                content="Как погасить кредит?",
                            ),
                            Message(
                                conversation_id=conversation.id,
                                sender_user_id=operator.id,
                                role="operator",
                                content="Через мобильное приложение.",
                            ),
                        ]
                    )

                messages = (
                    await session.scalars(
                        select(Message).where(
                            Message.conversation_id == conversation.id
                        )
                    )
                ).all()

                assert len(messages) == 2
                assert customer.user_id == client.id
                assert conversation.customer_id == customer.id
                assert {m.role for m in messages} == {
                    "client",
                    "operator",
                }

        finally:
            await transaction.rollback()
