from pydantic import BaseModel, ConfigDict, Field, field_validator


class OperatorMessageCreateRequest(BaseModel):
    """Данные сообщения, отправляемого оператором."""

    model_config = ConfigDict(extra="forbid")

    # Текст сообщения оператора.
    # Допустимая длина: от 1 до 4000 символов.
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content", mode="before")
    @classmethod
    def strip_content(cls, value: str) -> str:
        """Удалить пробелы по краям сообщения."""
        if isinstance(value, str):
            return value.strip()
        return value
