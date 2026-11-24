from bank_ai.infrastructure.database.base import Base
from bank_ai.infrastructure.database.models import (
    Conversation,
    Customer,
    Message,
    SupportTicket,
    TicketAssignmentHistory,
    TicketTransferRequest,
    User,
)


def test_all_tables_registered():
    assert set(Base.metadata.tables) == {
        "users",
        "customers",
        "conversations",
        "messages",
        "support_tickets",
        "ticket_assignment_history",
        "ticket_transfer_requests",
    }


def test_user_model():
    assert User.__tablename__ == "users"
    assert User.__table__.c.email.unique
    assert "password_hash" in User.__table__.columns


def test_customer_model():
    assert Customer.__tablename__ == "customers"

    assert Customer.__table__.c.user_id.unique

    assert Customer.__table__.c.user_id.references(User.__table__.c.id)


def test_conversation_model():
    assert Conversation.__tablename__ == "conversations"

    assert Conversation.__table__.c.customer_id.references(Customer.__table__.c.id)


def test_message_model():
    assert Message.__tablename__ == "messages"

    assert Message.__table__.c.conversation_id.references(Conversation.__table__.c.id)

    assert Message.__table__.c.sender_user_id.references(User.__table__.c.id)


def test_customer_relationship():
    assert User.customer.property.uselist is False
    assert Customer.conversations.property.uselist is True
    assert Conversation.messages.property.uselist is True


def test_support_ticket_relationships():
    assert SupportTicket.__tablename__ == "support_tickets"

    assert SupportTicket.__table__.c.conversation_id.references(
        Conversation.__table__.c.id
    )

    assert SupportTicket.__table__.c.assigned_operator_id.references(
        User.__table__.c.id
    )

    assert Conversation.tickets.property.uselist is True
    assert User.assigned_tickets.property.uselist is True


def test_assignment_history_relationships():
    assert TicketAssignmentHistory.__tablename__ == "ticket_assignment_history"

    assert TicketAssignmentHistory.__table__.c.ticket_id.references(
        SupportTicket.__table__.c.id
    )

    assert TicketAssignmentHistory.__table__.c.operator_id.references(
        User.__table__.c.id
    )


def test_ticket_unique_indexes():
    ticket_indexes = {index.name: index for index in SupportTicket.__table__.indexes}

    history_indexes = {
        index.name: index for index in TicketAssignmentHistory.__table__.indexes
    }

    assert ticket_indexes["uq_support_tickets_active_conversation"].unique

    assert history_indexes["uq_ticket_active_assignment"].unique


def test_support_ticket_relationships_additional():
    assert SupportTicket.__tablename__ == "support_tickets"

    assert SupportTicket.__table__.c.conversation_id.references(
        Conversation.__table__.c.id
    )

    assert SupportTicket.__table__.c.assigned_operator_id.references(
        User.__table__.c.id
    )

    assert Conversation.tickets.property.uselist is True
    assert User.assigned_tickets.property.uselist is True


def test_assignment_history_relationships_additional():
    assert TicketAssignmentHistory.__tablename__ == "ticket_assignment_history"

    assert TicketAssignmentHistory.__table__.c.ticket_id.references(
        SupportTicket.__table__.c.id
    )

    assert TicketAssignmentHistory.__table__.c.operator_id.references(
        User.__table__.c.id
    )


def test_ticket_unique_indexes_additional():
    ticket_indexes = {index.name: index for index in SupportTicket.__table__.indexes}

    history_indexes = {
        index.name: index for index in TicketAssignmentHistory.__table__.indexes
    }

    assert ticket_indexes["uq_support_tickets_active_conversation"].unique

    assert history_indexes["uq_ticket_active_assignment"].unique


def test_ticket_transfer_relationships():
    assert TicketTransferRequest.__tablename__ == "ticket_transfer_requests"

    assert TicketTransferRequest.__table__.c.ticket_id.references(
        SupportTicket.__table__.c.id
    )

    assert TicketTransferRequest.__table__.c.from_operator_id.references(
        User.__table__.c.id
    )

    assert TicketTransferRequest.__table__.c.to_operator_id.references(
        User.__table__.c.id
    )

    assert SupportTicket.transfer_requests.property.uselist is True


def test_ticket_transfer_indexes():
    indexes = {index.name: index for index in TicketTransferRequest.__table__.indexes}

    assert indexes["uq_ticket_pending_transfer"].unique

    assert "ix_ticket_transfers_recipient_status" in indexes
