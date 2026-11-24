import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransferCreateRequest(BaseModel):
    """Данные для создания запроса передачи обращения."""

    model_config = ConfigDict(extra="forbid")

    # UUID оператора, которому предлагается передать обращение.
    to_operator_id: uuid.UUID

    # Причина передачи обращения другому оператору.
    reason: str = Field(min_length=5, max_length=2000)

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        """Удалить пробелы по краям причины передачи."""
        if isinstance(value, str):
            return value.strip()
        return value


class TransferResponse(BaseModel):
    """Данные запроса передачи для ответа HTTP API."""

    model_config = ConfigDict(from_attributes=True)

    # Уникальный идентификатор запроса передачи.
    id: uuid.UUID

    # UUID обращения, которое передают.
    ticket_id: uuid.UUID

    # UUID оператора, инициировавшего передачу.
    from_operator_id: uuid.UUID

    # UUID оператора, которому предлагают обращение.
    to_operator_id: uuid.UUID

    # Текущий статус запроса передачи.
    status: Literal[
        "pending",
        "accepted",
        "rejected",
        "cancelled",
    ]

    # Причина передачи обращения.
    reason: str

    # Дата и время создания запроса.
    created_at: datetime

    # Дата и время принятия, отклонения или отмены.
    # Для ожидающего запроса значение NULL.
    responded_at: datetime | None
