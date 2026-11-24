from pydantic import BaseModel, Field
import uuid


class CurrentUserResponse(BaseModel):
    id: uuid.UUID
    email: str
    role: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
