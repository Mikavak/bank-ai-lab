import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bank_ai.infrastructure.database.base import Base


# Модель: учётная запись для аутентификации и ролей.
class User(Base):
    __tablename__ = "users"

    # Уникальный идентификатор учётной записи. Используется как `sub` в JWT.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Email для входа в систему; должен быть уникальным.
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    # Хеш пароля (Argon2), а не пароль в открытом виде.
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Роль пользователя: client (клиент), operator (оператор), admin (администратор). По умолчанию client.
    role: Mapped[str] = mapped_column(
        String(20),
        default="client",
        server_default="client",
        nullable=False,
    )

    # Дата и время регистрации пользователя; проставляются PostgreSQL.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Банковский профиль пользователя; один пользователь может иметь не более одного профиля.
    customer: Mapped["Customer"] = relationship(
        back_populates="user",
        uselist=False,
    )

    # Сообщения в чатах, отправленные этим пользователем.
    messages: Mapped[list["Message"]] = relationship(
        back_populates="sender",
    )

    # Обращения, за которые этот пользователь сейчас указан ответственным оператором.
    assigned_tickets: Mapped[list["SupportTicket"]] = relationship(
        back_populates="assigned_operator",
        foreign_keys="SupportTicket.assigned_operator_id",
    )

    # История назначений данного пользователя оператором по обращениям.
    ticket_assignments: Mapped[list["TicketAssignmentHistory"]] = relationship(
        back_populates="operator",
    )

    # Ограничения и индексы: CHECK запрещает роли вне перечисленных значений.
    __table_args__ = (
        CheckConstraint(
            "role IN ('client', 'operator', 'admin')",
            name="ck_users_role",
        ),
    )


# Модель: банковский профиль клиента.
class Customer(Base):
    __tablename__ = "customers"

    # Уникальный идентификатор банковского профиля клиента.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Ссылка на учётную запись User. UNIQUE обеспечивает связь 1 к 0..1.
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )

    # Отображаемое полное имя клиента.
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # Необязательный уникальный ID клиента во внешней банковской системе.
    external_customer_id: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
        nullable=True,
    )

    # Учётная запись User, к которой относится этот банковский профиль.
    user: Mapped["User"] = relationship(
        back_populates="customer",
    )

    # Все диалоги, принадлежащие клиенту.
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="customer",
    )


# Модель: чат клиента с банком.
class Conversation(Base):
    __tablename__ = "conversations"

    # Уникальный идентификатор диалога, объединяющего сообщения и обращения.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Ссылка на владельца диалога (Customer); индекс ускоряет поиск диалогов клиента.
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Статус диалога: open (открыт), waiting_operator (ожидает оператора), closed (закрыт).
    status: Mapped[str] = mapped_column(
        String(30),
        default="open",
        server_default="open",
        nullable=False,
        index=True,
    )

    # Дата и время создания диалога.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Дата и время последнего обновления записи диалога.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Владелец диалога — банковский профиль Customer.
    customer: Mapped["Customer"] = relationship(
        back_populates="conversations",
    )

    # Все сообщения, относящиеся к этому диалогу.
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
    )

    # Все обращения в поддержку, созданные в этом диалоге.
    tickets: Mapped[list["SupportTicket"]] = relationship(
        back_populates="conversation",
    )

    # Ограничения и индексы: CHECK разрешает только предусмотренные статусы диалога.
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'waiting_operator', 'closed')",
            name="ck_conversations_status",
        ),
    )


# Модель: отдельное сообщение в чате.
class Message(Base):
    __tablename__ = "messages"

    # Уникальный идентификатор сообщения.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Ссылка на диалог, в котором находится сообщение.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Ссылка на User отправителя; NULL допустим для сообщений AI-ассистента.
    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    # Тип отправителя: client (клиент), operator (оператор), assistant (AI).
    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    # Текст сообщения.
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Дата и время создания сообщения.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Диалог, которому принадлежит сообщение.
    conversation: Mapped["Conversation"] = relationship(
        back_populates="messages",
    )

    # Учётная запись автора сообщения, если автор — человек.
    sender: Mapped["User | None"] = relationship(
        back_populates="messages",
    )

    # Ограничения и индексы: CHECK ограничивает роли; составной индекс ускоряет чтение истории диалога по времени.
    __table_args__ = (
        CheckConstraint(
            "role IN ('client', 'operator', 'assistant')",
            name="ck_messages_role",
        ),
        Index(
            "ix_messages_conversation_created",
            "conversation_id",
            "created_at",
        ),
    )


# Модель: обращение для участия оператора.
class SupportTicket(Base):
    __tablename__ = "support_tickets"

    # Уникальный идентификатор обращения к оператору.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Ссылка на исходный диалог; история сообщений остаётся в Conversation.
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ID текущего ответственного оператора; NULL, пока обращение не назначено.
    assigned_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    # Статус: pending (в очереди), in_progress (в работе), resolved (решено), closed (закрыто).
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    # Причина передачи обращения оператору (например, недостаточно знаний у AI).
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Дата и время создания обращения.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Дата и время последнего обновления записи обращения.
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Когда обращение в последний раз назначили оператору; до назначения NULL.
    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Время решения проблемы; до решения NULL.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Диалог, из которого было создано обращение.
    conversation: Mapped["Conversation"] = relationship(
        back_populates="tickets",
    )

    # Текущий назначенный оператор (User) либо None.
    assigned_operator: Mapped["User | None"] = relationship(
        back_populates="assigned_tickets",
        foreign_keys=[assigned_operator_id],
    )

    # Все назначения операторов на это обращение в хронологическом порядке.
    assignment_history: Mapped[list["TicketAssignmentHistory"]] = relationship(
        back_populates="ticket",
        order_by="TicketAssignmentHistory.assigned_at",
    )

    # Запросы на передачу обращения другим операторам.
    transfer_requests: Mapped[list["TicketTransferRequest"]] = relationship(
        back_populates="ticket",
    )

    # Ограничения и индексы: CHECK контролируют статусы и назначение; частичный уникальный индекс запрещает два активных обращения на один диалог; индекс очереди ускоряет её получение.
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'in_progress', 'resolved', 'closed')",
            name="ck_support_tickets_status",
        ),
        CheckConstraint(
            "status <> 'pending' OR assigned_operator_id IS NULL",
            name="ck_support_tickets_pending",
        ),
        CheckConstraint(
            "status <> 'in_progress' OR "
            "(assigned_operator_id IS NOT NULL "
            "AND assigned_at IS NOT NULL)",
            name="ck_support_tickets_in_progress",
        ),
        Index(
            "uq_support_tickets_active_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=sql_text("status IN ('pending', 'in_progress')"),
        ),
        Index(
            "ix_support_tickets_queue",
            "status",
            "created_at",
        ),
    )


# Модель: история назначений операторов.
class TicketAssignmentHistory(Base):
    __tablename__ = "ticket_assignment_history"

    # Уникальный идентификатор записи истории назначения.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Ссылка на обращение, к которому относится назначение.
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("support_tickets.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ID оператора, которому было назначено обращение.
    operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # Момент начала ответственности оператора за обращение.
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Момент окончания назначения; NULL означает, что назначение активно.
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Обращение, для которого создана запись назначения.
    ticket: Mapped["SupportTicket"] = relationship(
        back_populates="assignment_history",
    )

    # Оператор, работавший с обращением в период этого назначения.
    operator: Mapped["User"] = relationship(
        back_populates="ticket_assignments",
    )

    # Ограничения и индексы: CHECK проверяет хронологию; частичный уникальный индекс запрещает два незавершённых назначения одного обращения.
    __table_args__ = (
        CheckConstraint(
            "released_at IS NULL OR released_at >= assigned_at",
            name="ck_ticket_assignment_dates",
        ),
        Index(
            "uq_ticket_active_assignment",
            "ticket_id",
            unique=True,
            postgresql_where=sql_text("released_at IS NULL"),
        ),
    )


# Модель: запрос на передачу обращения коллеге.
class TicketTransferRequest(Base):
    __tablename__ = "ticket_transfer_requests"

    # Уникальный идентификатор запроса на передачу обращения.
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Ссылка на передаваемое обращение.
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("support_tickets.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ID оператора, который инициировал передачу.
    from_operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # ID оператора, которому предлагают принять обращение.
    to_operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    # Статус запроса: pending, accepted, rejected или cancelled.
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    # Причина передачи обращения коллеге.
    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    # Дата и время создания запроса на передачу.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Время принятия, отказа или отмены; пока запрос ожидает решения — NULL.
    responded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Обращение, которое хотят передать.
    ticket: Mapped["SupportTicket"] = relationship(
        back_populates="transfer_requests",
    )

    # Оператор — инициатор передачи.
    from_operator: Mapped["User"] = relationship(
        foreign_keys=[from_operator_id],
    )

    # Оператор — предполагаемый получатель обращения.
    to_operator: Mapped["User"] = relationship(
        foreign_keys=[to_operator_id],
    )

    # Ограничения и индексы: CHECK ограничивают статусы, запрещают передачу самому себе и согласуют время ответа; частичный индекс запрещает параллельные ожидающие передачи.
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected', 'cancelled')",
            name="ck_ticket_transfer_status",
        ),
        CheckConstraint(
            "from_operator_id <> to_operator_id",
            name="ck_ticket_transfer_different_operators",
        ),
        CheckConstraint(
            "(status = 'pending' AND responded_at IS NULL) OR "
            "(status <> 'pending' AND responded_at IS NOT NULL)",
            name="ck_ticket_transfer_response",
        ),
        Index(
            "uq_ticket_pending_transfer",
            "ticket_id",
            unique=True,
            postgresql_where=sql_text("status = 'pending'"),
        ),
        Index(
            "ix_ticket_transfers_recipient_status",
            "to_operator_id",
            "status",
        ),
    )
