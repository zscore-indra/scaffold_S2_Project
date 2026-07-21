"""Pydantic v2 request and response models."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ExpenseCreate(BaseModel):
    """Payload to submit a new expense."""

    description: str = Field(min_length=1)
    amount_minor: int = Field(gt=0, description="Amount in integer minor units (e.g. cents).")
    currency: str = Field(min_length=3, max_length=3, description="ISO 4217 code, e.g. USD.")

    @field_validator("currency")
    @classmethod
    def _normalise_currency(cls, value: str) -> str:
        """Uppercase and validate the currency is a 3-letter alphabetic code."""
        value = value.strip().upper()
        if not value.isalpha():
            raise ValueError("currency must be a 3-letter alphabetic ISO 4217 code")
        return value


class ExpenseOut(BaseModel):
    """Serialised view of a stored expense."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    description: str
    amount_minor: int
    currency: str
    amount_base_minor: int
    base_currency: str
    fx_rate: float
    status: str
    created_at: datetime
