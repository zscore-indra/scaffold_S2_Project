"""HTTP endpoints for the expense submit / convert / approve-reject journey."""

import os
from decimal import ROUND_HALF_UP, Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    BASE_CURRENCY,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Expense,
)
from app.schemas import ExpenseCreate, ExpenseOut

router = APIRouter(prefix="/expenses", tags=["expenses"])

# External FX provider (Frankfurter: ECB data, keyless). Configured via env.
# Expects `?base=<src>&symbols=<base>` and returns `{"rates": {"<base>": rate}}`.
FX_API_URL: str = os.getenv("FX_API_URL", "https://api.frankfurter.dev/v1/latest")


def get_fx_rate(source_currency: str, base_currency: str = BASE_CURRENCY) -> float:
    """Return the FX rate as base-currency units per one source-currency unit.

    Calls the external FX provider over httpx. When the source already equals
    the base currency the rate is 1.0 and no network call is made.
    """
    if source_currency == base_currency:
        return 1.0

    params: dict[str, str] = {"base": source_currency, "symbols": base_currency}
    api_key = os.getenv("FX_API_KEY")
    if api_key:
        params["access_key"] = api_key

    response = httpx.get(FX_API_URL, params=params, timeout=10.0)
    response.raise_for_status()
    data = response.json()
    try:
        rate = data["rates"][base_currency]
    except (KeyError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not read FX rate from provider response.",
        ) from exc
    return float(rate)


def _to_base_minor(amount_minor: int, rate: float) -> int:
    """Convert source minor units to base minor units using `rate` (half-up)."""
    converted = Decimal(amount_minor) * Decimal(str(rate))
    return int(converted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _get_or_404(db: Session, expense_id: int) -> Expense:
    """Fetch an expense by id or raise 404."""
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Expense not found.")
    return expense


def _decide(db: Session, expense_id: int, new_status: str) -> Expense:
    """Move a pending expense to `new_status`, or 409 if it is already decided."""
    expense = _get_or_404(db, expense_id)
    if expense.status != STATUS_PENDING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Expense is already {expense.status}.",
        )
    expense.status = new_status
    db.commit()
    db.refresh(expense)
    return expense


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
def submit_expense(payload: ExpenseCreate, db: Session = Depends(get_db)) -> Expense:
    """Submit an expense, normalise it to the base currency, and store it as pending."""
    rate = get_fx_rate(payload.currency)
    expense = Expense(
        description=payload.description,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        amount_base_minor=_to_base_minor(payload.amount_minor, rate),
        fx_rate=rate,
        status=STATUS_PENDING,
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.get("/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Retrieve a single expense by id."""
    return _get_or_404(db, expense_id)


@router.post("/{expense_id}/approve", response_model=ExpenseOut)
def approve_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Approve a pending expense."""
    return _decide(db, expense_id, STATUS_APPROVED)


@router.post("/{expense_id}/reject", response_model=ExpenseOut)
def reject_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Reject a pending expense."""
    return _decide(db, expense_id, STATUS_REJECTED)
