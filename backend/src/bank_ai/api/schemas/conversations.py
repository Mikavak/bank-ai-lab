import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: Literal["open", "waiting_operator", "closed"]
    created_at: datetime
    updated_at: datetime
