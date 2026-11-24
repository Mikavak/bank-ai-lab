import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from bank_ai.application.conversation_lifecycle_service import (
    ConversationCloseConflictError,
    close_client_conversation,
)
from bank_ai.application.ticket_service import (
    ConversationClosedError,
    TicketNotFoundError,
    create_support_ticket,
    resolve_support_ticket,
)
from bank_ai.application.ticket_transfer_service import (
    TransferConflictError,
    accept_transfer,
)
from bank_ai.infrastructure.database.models import (
    Conversation,
    Customer,
    SupportTicket,
    TicketAssignmentHistory,
    TicketTransferRequest,
    User,
)
from bank_ai.infrastructure.database.session import get_engine


@dataclass
class ConcurrencyCase:
    # Идентификаторы тестовых записей для проверки и очистки.
    user_ids: list[uuid.UUID]
    customer_id: uuid.UUID
    conversation_id: uuid.UUID
    ticket_id: uuid.UUID
    transfer_id: uuid.UUID
    source_operator_id: uuid.UUID
    target_operator_id: uuid.UUID


@pytest_asyncio.fixture
async def concurrency_case():
    """
    Подготовить данные, видимые из разных соединений PostgreSQL.

    В отличие от тестов с внешней транзакцией и SAVEPOINT,
    данные здесь фиксируются настоящим COMMIT.
    После теста удаляются только созданные записи.
    """
    session_factory = async_sessionmaker(
        get_engine(),
        expire_on_commit=False,
    )

    async with session_factory.begin() as db:
        client = User(
            email=f"client-{uuid.uuid4()}@test.local",
            password_hash="test_hash",
            role="client",
        )
        source_operator = User(
            email=f"operator1-{uuid.uuid4()}@test.local",
            password_hash="test_hash",
            role="operator",
        )
        target_operator = User(
            email=f"operator2-{uuid.uuid4()}@test.local",
            password_hash="test_hash",
            role="operator",
        )

        db.add_all(
            [
                client,
                source_operator,
                target_operator,
            ]
        )
        await db.flush()

        customer = Customer(
            user_id=client.id,
            full_name="Тестовый клиент",
        )
        db.add(customer)
        await db.flush()

        conversation = Conversation(
            customer_id=customer.id,
            status="waiting_operator",
        )
        db.add(conversation)
        await db.flush()

        assigned_at = datetime.now(UTC)

        ticket = SupportTicket(
            conversation_id=conversation.id,
            assigned_operator_id=source_operator.id,
            status="in_progress",
            reason="Нужна помощь специалиста",
            assigned_at=assigned_at,
        )
        db.add(ticket)
        await db.flush()

        db.add(
            TicketAssignmentHistory(
                ticket_id=ticket.id,
                operator_id=source_operator.id,
                assigned_at=assigned_at,
            )
        )

        transfer = TicketTransferRequest(
            ticket_id=ticket.id,
            from_operator_id=source_operator.id,
            to_operator_id=target_operator.id,
            status="pending",
            reason="Передача профильному специалисту",
        )
        db.add(transfer)
        await db.flush()

        case = ConcurrencyCase(
            user_ids=[
                client.id,
                source_operator.id,
                target_operator.id,
            ],
            customer_id=customer.id,
            conversation_id=conversation.id,
            ticket_id=ticket.id,
            transfer_id=transfer.id,
            source_operator_id=source_operator.id,
            target_operator_id=target_operator.id,
        )

    try:
        yield case

    finally:
        # Удаляем созданные записи в порядке зависимостей FK.
        async with session_factory.begin() as db:
            # Находим все обращения, принадлежащие тестовому диалогу.
            ticket_ids = select(SupportTicket.id).where(
                SupportTicket.conversation_id == case.conversation_id
            )

            # Сначала удаляем запросы передачи.
            await db.execute(
                delete(TicketTransferRequest).where(
                    TicketTransferRequest.ticket_id.in_(ticket_ids)
                )
            )

            # Затем удаляем историю назначений операторов.
            await db.execute(
                delete(TicketAssignmentHistory).where(
                    TicketAssignmentHistory.ticket_id.in_(ticket_ids)
                )
            )

            # Удаляем все обращения тестового диалога.
            await db.execute(
                delete(SupportTicket).where(
                    SupportTicket.conversation_id == case.conversation_id
                )
            )

            await db.execute(
                delete(Conversation).where(Conversation.id == case.conversation_id)
            )
            await db.execute(delete(Customer).where(Customer.id == case.customer_id))
            await db.execute(delete(User).where(User.id.in_(case.user_ids)))


@pytest.mark.asyncio
async def test_concurrent_accept_transfer(
    concurrency_case: ConcurrencyCase,
):
    case = concurrency_case

    # Каждая конкурентная задача получает собственную AsyncSession.
    session_factory = async_sessionmaker(
        get_engine(),
        expire_on_commit=False,
    )

    # Два запроса начнут принятие после общего сигнала.
    start = asyncio.Event()
    ready: asyncio.Queue[None] = asyncio.Queue()

    async def try_accept() -> str:
        async with session_factory() as db:
            operator = await db.get(
                User,
                case.target_operator_id,
            )

            assert operator is not None

            # Сообщаем, что задача готова.
            ready.put_nowait(None)

            # Обе задачи ожидают разрешения продолжить.
            await start.wait()

            try:
                await accept_transfer(
                    db=db,
                    transfer_id=case.transfer_id,
                    operator=operator,
                )
                return "accepted"

            except TransferConflictError:
                await db.rollback()
                return "conflict"

    first = asyncio.create_task(try_accept())
    second = asyncio.create_task(try_accept())

    try:
        # Ждём готовности обоих независимых запросов.
        await asyncio.wait_for(ready.get(), timeout=10)
        await asyncio.wait_for(ready.get(), timeout=10)

        start.set()

        results = await asyncio.wait_for(
            asyncio.gather(first, second),
            timeout=15,
        )

    finally:
        start.set()
        for task in (first, second):
            if not task.done():
                task.cancel()
        await asyncio.gather(
            first,
            second,
            return_exceptions=True,
        )

    # Ровно один запрос должен завершиться успешно.
    assert sorted(results) == ["accepted", "conflict"]

    # Проверяем окончательное состояние в PostgreSQL.
    async with session_factory() as db:
        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )
        transfer = await db.get(
            TicketTransferRequest,
            case.transfer_id,
        )

        assert ticket is not None
        assert transfer is not None

        assert transfer.status == "accepted"
        assert transfer.responded_at is not None
        assert ticket.assigned_operator_id == case.target_operator_id

        history = list(
            (
                await db.scalars(
                    select(TicketAssignmentHistory).where(
                        TicketAssignmentHistory.ticket_id == case.ticket_id
                    )
                )
            ).all()
        )

        # Никаких дубликатов назначений.
        assert len(history) == 2

        active = [item for item in history if item.released_at is None]

        assert len(active) == 1
        assert active[0].operator_id == case.target_operator_id

        old = next(
            item for item in history if item.operator_id == case.source_operator_id
        )

        assert old.released_at is not None


@pytest.mark.asyncio
async def test_concurrent_resolve_and_accept_transfer(
    concurrency_case: ConcurrencyCase,
):
    """
    Проверить конкурентное завершение и передачу обращения.

    Только одна операция должна успешно изменить
    состояние обращения.

    Используем две независимые сессии PostgreSQL.
    """

    case = concurrency_case

    session_factory = async_sessionmaker(
        get_engine(),
        expire_on_commit=False,
    )

    # Сигнал одновременного старта запросов.
    start = asyncio.Event()

    # Очередь подтверждений готовности задач.
    ready: asyncio.Queue[None] = asyncio.Queue()

    async def try_resolve() -> str:
        """Попытаться завершить обращение первым оператором."""

        async with session_factory() as db:
            operator = await db.get(
                User,
                case.source_operator_id,
            )

            assert operator is not None

            ready.put_nowait(None)
            await start.wait()

            try:
                await resolve_support_ticket(
                    db=db,
                    ticket_id=case.ticket_id,
                    operator=operator,
                )
                return "resolved"

            except TicketNotFoundError:
                # После принятия передачи оператор №1
                # уже не является ответственным.
                await db.rollback()
                return "not_found"

    async def try_accept() -> str:
        """Попытаться принять передачу вторым оператором."""

        async with session_factory() as db:
            operator = await db.get(
                User,
                case.target_operator_id,
            )

            assert operator is not None

            ready.put_nowait(None)
            await start.wait()

            try:
                await accept_transfer(
                    db=db,
                    transfer_id=case.transfer_id,
                    operator=operator,
                )
                return "accepted"

            except TransferConflictError:
                # Первый оператор уже завершил обращение
                # и отменил ожидающую передачу.
                await db.rollback()
                return "conflict"

    # Запускаем две конкурентные задачи.
    resolve_task = asyncio.create_task(try_resolve())
    accept_task = asyncio.create_task(try_accept())

    try:
        # Дожидаемся готовности обоих запросов.
        await asyncio.wait_for(ready.get(), timeout=10)
        await asyncio.wait_for(ready.get(), timeout=10)

        # Разрешаем обоим продолжить одновременно.
        start.set()

        results = await asyncio.wait_for(
            asyncio.gather(resolve_task, accept_task),
            timeout=15,
        )

    finally:
        start.set()

        # Безопасно отменяем незавершённые задачи.
        for task in (resolve_task, accept_task):
            if not task.done():
                task.cancel()

        await asyncio.gather(
            resolve_task,
            accept_task,
            return_exceptions=True,
        )

    # Допустимы только два последовательных исхода.
    assert results in (
        ["resolved", "conflict"],
        ["not_found", "accepted"],
    )

    # Проверяем итоговые данные PostgreSQL.
    async with session_factory() as db:
        ticket = await db.get(
            SupportTicket,
            case.ticket_id,
        )

        transfer = await db.get(
            TicketTransferRequest,
            case.transfer_id,
        )

        conversation = await db.get(
            Conversation,
            case.conversation_id,
        )

        history = list(
            (
                await db.scalars(
                    select(TicketAssignmentHistory).where(
                        TicketAssignmentHistory.ticket_id == case.ticket_id
                    )
                )
            ).all()
        )

        assert ticket is not None
        assert transfer is not None
        assert conversation is not None

        if results[0] == "resolved":
            # Завершение успело выполниться первым.
            assert ticket.status == "resolved"
            assert ticket.resolved_at is not None

            assert ticket.assigned_operator_id == (case.source_operator_id)

            assert transfer.status == "cancelled"
            assert transfer.responded_at is not None

            assert conversation.status == "open"

            # Осталась одна закрытая запись назначения.
            assert len(history) == 1
            assert history[0].released_at is not None

        else:
            # Передача успела выполниться первой.
            assert ticket.status == "in_progress"
            assert ticket.resolved_at is None

            assert ticket.assigned_operator_id == (case.target_operator_id)

            assert transfer.status == "accepted"
            assert transfer.responded_at is not None

            assert conversation.status == "waiting_operator"

            # Первое назначение закрыто, второе активно.
            assert len(history) == 2

            active = [item for item in history if item.released_at is None]

            assert len(active) == 1
            assert active[0].operator_id == (case.target_operator_id)


@pytest.mark.asyncio
async def test_concurrent_close_and_create_ticket(
    concurrency_case: ConcurrencyCase,
):
    """
    Проверить конкурентное закрытие диалога
    и создание нового обращения.

    Два независимых соединения PostgreSQL должны
    обеспечить только один успешный переход состояния.
    """

    case = concurrency_case

    session_factory = async_sessionmaker(
        get_engine(),
        expire_on_commit=False,
    )

    # Подготовка: завершаем исходное обращение,
    # чтобы клиент мог создать новое обращение.
    async with session_factory() as db:
        operator = await db.get(
            User,
            case.source_operator_id,
        )
        assert operator is not None

        resolved = await resolve_support_ticket(
            db=db,
            ticket_id=case.ticket_id,
            operator=operator,
        )
        assert resolved.status == "resolved"

    # Синхронизируем старт конкурентных запросов.
    start = asyncio.Event()
    ready: asyncio.Queue[None] = asyncio.Queue()

    async def try_close() -> str:
        """Попытаться закрыть клиентский диалог."""

        async with session_factory() as db:
            ready.put_nowait(None)
            await start.wait()

            try:
                await close_client_conversation(
                    db=db,
                    conversation_id=case.conversation_id,
                    customer_id=case.customer_id,
                )
                return "closed"

            except ConversationCloseConflictError:
                await db.rollback()
                return "active_ticket"

    async def try_create_ticket() -> str:
        """Попытаться создать новое обращение."""

        async with session_factory() as db:
            ready.put_nowait(None)
            await start.wait()

            try:
                ticket = await create_support_ticket(
                    db=db,
                    conversation_id=case.conversation_id,
                    customer_id=case.customer_id,
                    reason="Повторный вопрос клиента",
                )

                assert ticket.status == "pending"
                return "created"

            except ConversationClosedError:
                await db.rollback()
                return "conversation_closed"

    close_task = asyncio.create_task(try_close())
    create_task = asyncio.create_task(try_create_ticket())

    try:
        # Обе задачи должны быть готовы к старту.
        await asyncio.wait_for(ready.get(), timeout=10)
        await asyncio.wait_for(ready.get(), timeout=10)

        start.set()

        # Выполняем операции конкурентно.
        results = await asyncio.wait_for(
            asyncio.gather(close_task, create_task),
            timeout=15,
        )

    finally:
        start.set()

        for task in (close_task, create_task):
            if not task.done():
                task.cancel()

        await asyncio.gather(
            close_task,
            create_task,
            return_exceptions=True,
        )

    # Только один из двух сценариев допустим.
    assert results in (
        ["closed", "conversation_closed"],
        ["active_ticket", "created"],
    )

    # Проверяем окончательное состояние PostgreSQL.
    async with session_factory() as db:
        conversation = await db.get(
            Conversation,
            case.conversation_id,
        )
        assert conversation is not None

        # Получаем только активные обращения диалога.
        active_tickets = list(
            (
                await db.scalars(
                    select(SupportTicket).where(
                        SupportTicket.conversation_id == case.conversation_id,
                        SupportTicket.status.in_(("pending", "in_progress")),
                    )
                )
            ).all()
        )

        # Исходное обращение осталось завершённым.
        original = await db.get(
            SupportTicket,
            case.ticket_id,
        )
        assert original is not None
        assert original.status == "resolved"

        if results[0] == "closed":
            # Диалог закрыт, новых обращений нет.
            assert conversation.status == "closed"
            assert len(active_tickets) == 0

        else:
            # Новое обращение создано первым.
            assert conversation.status == "waiting_operator"
            assert len(active_tickets) == 1

            new_ticket = active_tickets[0]

            assert new_ticket.id != case.ticket_id
            assert new_ticket.status == "pending"
            assert new_ticket.assigned_operator_id is None
