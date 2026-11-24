import uuid
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class TicketCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        min_length=5,
        max_length=2000,
    )

    @field_validator("reason", mode="before")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        if isinstance(value, str):
            return value.strip()
        return value


class TicketResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID

    assigned_operator_id: uuid.UUID | None

    status: Literal[
        "pending",
        "in_progress",
        "resolved",
        "closed",
    ]

    reason: str

    created_at: datetime
    updated_at: datetime

    assigned_at: datetime | None
    resolved_at: datetime | None
