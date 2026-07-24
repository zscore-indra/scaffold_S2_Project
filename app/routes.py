"""HTTP endpoints for the expense submit / convert / approve-reject journey."""

import logging
import os
from decimal import ROUND_HALF_UP, Decimal

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.insights import generate_insight
from app.models import (
    BASE_CURRENCY,
    CONVERSION_CONVERTED,
    CONVERSION_FX_UNAVAILABLE,
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    Expense,
)
from app.schemas import ExpenseCreate, ExpenseOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/expenses", tags=["expenses"])
reports_router = APIRouter(prefix="/reports", tags=["reports"])

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


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Require a valid ``X-API-Key`` header on mutating requests.

    Enforced only when ``API_KEY`` is set in the environment (read at request
    time so tests can toggle it): a missing or non-matching key is rejected with
    401. When ``API_KEY`` is unset the check is skipped, so local runs and the
    existing test suite need no key.
    """
    expected = os.getenv("API_KEY")
    if expected and x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key.",
        )


@router.post(
    "",
    response_model=ExpenseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_api_key)],
)
def submit_expense(payload: ExpenseCreate, db: Session = Depends(get_db)) -> Expense:
    """Submit an expense, normalise it to the base currency, and store it as pending.

    If the FX provider is unreachable the expense is still stored, but with no
    base-currency amount and ``conversion_status = "fx_unavailable"`` so the
    failed conversion is explicit rather than silently wrong.
    """
    try:
        rate = get_fx_rate(payload.currency)
    except httpx.HTTPError:
        logger.warning(
            "FX provider unreachable; storing %s expense unconverted.", payload.currency
        )
        expense = Expense(
            description=payload.description,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            amount_base_minor=None,
            fx_rate=None,
            status=STATUS_PENDING,
            conversion_status=CONVERSION_FX_UNAVAILABLE,
        )
    else:
        expense = Expense(
            description=payload.description,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            amount_base_minor=_to_base_minor(payload.amount_minor, rate),
            fx_rate=rate,
            status=STATUS_PENDING,
            conversion_status=CONVERSION_CONVERTED,
        )
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.get("", response_model=list[ExpenseOut])
def list_expenses(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> list[Expense]:
    """List stored expenses, newest first, optionally filtered by ``?status=``."""
    stmt = select(Expense).order_by(Expense.id.desc())
    if status_filter is not None:
        stmt = stmt.where(Expense.status == status_filter)
    return list(db.scalars(stmt).all())


@router.get("/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Retrieve a single expense by id."""
    return _get_or_404(db, expense_id)


@router.post(
    "/{expense_id}/approve",
    response_model=ExpenseOut,
    dependencies=[Depends(require_api_key)],
)
def approve_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Approve a pending expense."""
    return _decide(db, expense_id, STATUS_APPROVED)


@router.post(
    "/{expense_id}/reject",
    response_model=ExpenseOut,
    dependencies=[Depends(require_api_key)],
)
def reject_expense(expense_id: int, db: Session = Depends(get_db)) -> Expense:
    """Reject a pending expense."""
    return _decide(db, expense_id, STATUS_REJECTED)


@reports_router.get("/insights")
def reports_insights(db: Session = Depends(get_db)) -> dict[str, object]:
    """Summarise every stored expense into a spending-insight JSON object.

    The ``insight`` value is the object returned by ``generate_insight``
    (``{"summary": str, "bullets": [str, str, str]}``).
    """
    expenses = db.scalars(select(Expense)).all()
    expense_dicts: list[dict] = [
        {
            "id": e.id,
            "description": e.description,
            "amount_minor": e.amount_minor,
            "currency": e.currency,
            "amount_base_minor": e.amount_base_minor,
            "fx_rate": e.fx_rate,
            "status": e.status,
            "created_at": e.created_at,
        }
        for e in expenses
    ]
    return {"insight": generate_insight(expense_dicts)}
