
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
    text as sql_text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bank_ai.infrastructure.database.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        default="client",
        server_default="client",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    customer: Mapped["Customer"] = relationship(
        back_populates="user",
        uselist=False,
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="sender",
    )

    assigned_tickets: Mapped[list["SupportTicket"]] = relationship(
        back_populates="assigned_operator",
        foreign_keys="SupportTicket.assigned_operator_id",
    )

    ticket_assignments: Mapped[list["TicketAssignmentHistory"]] = relationship(
        back_populates="operator",
    )


    __table_args__ = (
        CheckConstraint(
            "role IN ('client', 'operator', 'admin')",
            name="ck_users_role",
        ),
    )


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )

    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    external_customer_id: Mapped[str | None] = mapped_column(
        String(100),
        unique=True,
        nullable=True,
    )

    user: Mapped["User"] = relationship(
        back_populates="customer",
    )

    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="customer",
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        default="open",
        server_default="open",
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    customer: Mapped["Customer"] = relationship(
        back_populates="conversations",
    )

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
    )

    tickets: Mapped[list["SupportTicket"]] = relationship(
        back_populates="conversation",
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'waiting_operator', 'closed')",
            name="ck_conversations_status",
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    sender_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    conversation: Mapped["Conversation"] = relationship(
        back_populates="messages",
    )

    sender: Mapped["User | None"] = relationship(
        back_populates="messages",
    )

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


class SupportTicket(Base):
    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    assigned_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="pending",
        server_default="pending",
    )

    reason: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    assigned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    conversation: Mapped["Conversation"] = relationship(
        back_populates="tickets",
    )

    assigned_operator: Mapped["User | None"] = relationship(
        back_populates="assigned_tickets",
        foreign_keys=[assigned_operator_id],
    )

    assignment_history: Mapped[list["TicketAssignmentHistory"]] = (
        relationship(
            back_populates="ticket",
            order_by="TicketAssignmentHistory.assigned_at",
        )
    )

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
            postgresql_where=sql_text(
                "status IN ('pending', 'in_progress')"
            ),
        ),
        Index(
            "ix_support_tickets_queue",
            "status",
            "created_at",
        ),
    )


class TicketAssignmentHistory(Base):
    __tablename__ = "ticket_assignment_history"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("support_tickets.id", ondelete="RESTRICT"),
        nullable=False,
    )

    operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    ticket: Mapped["SupportTicket"] = relationship(
        back_populates="assignment_history",
    )

    operator: Mapped["User"] = relationship(
        back_populates="ticket_assignments",
    )

    __table_args__ = (
        CheckConstraint(
            "released_at IS NULL OR released_at >= assigned_at",
            name="ck_ticket_assignment_dates",
        ),
        Index(
            "uq_ticket_active_assignment",
            "ticket_id",
            unique=True,
            postgresql_where=sql_text(
                "released_at IS NULL"
            ),
        ),
    )
