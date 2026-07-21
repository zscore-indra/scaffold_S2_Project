"""SQLAlchemy ORM models.

Money is stored as integer minor units (paise / cents), never float. Every
expense is normalised to the base currency (INR) on write, so downstream reads
never need to convert.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

BASE_CURRENCY: str = "INR"

# Expense lifecycle states.
STATUS_PENDING: str = "pending"
STATUS_APPROVED: str = "approved"
STATUS_REJECTED: str = "rejected"


class Expense(Base):
    """A submitted expense and its normalised base-currency value."""

    __tablename__ = "expenses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(String, nullable=False)

    # Original amount, in integer minor units of `currency` (e.g. cents).
    amount_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)

    # Amount normalised to base-currency (INR) minor units, computed on write.
    amount_base_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    # FX rate used at write time: base-currency units per one source unit.
    fx_rate: Mapped[float] = mapped_column(Float, nullable=False)

    status: Mapped[str] = mapped_column(String, nullable=False, default=STATUS_PENDING)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    @property
    def base_currency(self) -> str:
        """The base currency all amounts are normalised to."""
        return BASE_CURRENCY
