"""Streamlit front-end for the ExpenseFlow API.

Talks to the API over httpx. The base URL is read from the ``API_BASE``
environment variable (default ``http://127.0.0.1:8000``). Provides a form to
submit an expense, a KPI summary plus a table of existing expenses, and a
button that generates spending insights. Network and API errors are surfaced
as friendly messages rather than tracebacks.

Styling uses a validated, colourblind-safe palette: a blue brand colour and the
reserved status hues (green / amber / red), each status shown with an icon and
label so meaning is never carried by colour alone.
"""

import html
import os
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

import httpx
import streamlit as st

API_BASE: str = os.getenv("API_BASE", "http://127.0.0.1:8000")

# Injected once per run: buttons, KPI tiles, expense table, and status badges.
_CSS = """
<style>
:root {
  --brand: #2a78d6;
  --card: #ffffff;
  --ink: #0b0b0b;
  --ink-muted: #52514e;
  --border: rgba(11, 11, 11, 0.10);
  --good: #0ca30c;
  --warning: #fab219;
  --critical: #d03b3b;
}
.stButton > button, .stFormSubmitButton > button {
  border-radius: 10px;
  font-weight: 600;
  border: 1px solid var(--border);
  transition: transform .12s ease, box-shadow .12s ease;
}
.stButton > button:hover, .stFormSubmitButton > button:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 16px rgba(42, 120, 214, .22);
}
h1, h2, h3 { letter-spacing: -0.01em; }
.kpi-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 14px;
  margin: 6px 0 18px;
}
.kpi {
  background: var(--card);
  border: 1px solid var(--border);
  border-left: 5px solid var(--accent, var(--brand));
  border-radius: 12px;
  padding: 14px 16px;
  box-shadow: 0 1px 2px rgba(11, 11, 11, .04);
}
.kpi-label {
  font-size: .78rem; color: var(--ink-muted); font-weight: 600;
  text-transform: uppercase; letter-spacing: .03em;
}
.kpi-value { font-size: 1.6rem; font-weight: 700; color: var(--ink); margin-top: 2px; }
.expense-table { width: 100%; border-collapse: separate; border-spacing: 0; font-size: .92rem; }
.expense-table th {
  text-align: left; font-size: .72rem; text-transform: uppercase; letter-spacing: .04em;
  color: var(--ink-muted); padding: 8px 12px; border-bottom: 2px solid var(--border);
}
.expense-table td { padding: 10px 12px; border-bottom: 1px solid var(--border); color: var(--ink); }
.expense-table tr:hover td { background: rgba(42, 120, 214, .05); }
.expense-table .num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
.badge {
  display: inline-block; padding: 2px 10px; border-radius: 999px;
  font-size: .78rem; font-weight: 600; white-space: nowrap;
}
.badge-approved { background: rgba(12, 163, 12, .14); color: #0a7d0a; }
.badge-pending  { background: rgba(250, 178, 25, .18); color: #9a6b00; }
.badge-rejected { background: rgba(208, 59, 59, .14); color: #b02e2e; }
</style>
"""


class APIError(Exception):
    """Raised when the API is unreachable or returns a non-2xx response."""


def _extract_detail(response: httpx.Response) -> str:
    """Pull a human-readable error message out of an API error response."""
    try:
        body = response.json()
    except ValueError:
        return response.text or "no further details"
    if isinstance(body, dict) and "detail" in body:
        detail = body["detail"]
        # FastAPI validation errors arrive as a list of {"msg": ...} dicts.
        if isinstance(detail, list):
            return "; ".join(str(item.get("msg", item)) for item in detail)
        return str(detail)
    return str(body)


def _request(method: str, path: str, **kwargs: object) -> httpx.Response:
    """Send a request to the API, translating failures into ``APIError``."""
    try:
        with httpx.Client(base_url=API_BASE, timeout=10.0) as client:
            response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response
    except httpx.RequestError as exc:
        raise APIError(
            f"Could not reach the ExpenseFlow API at {API_BASE}. "
            "Is the server running? Start it with "
            "`python -m uvicorn app.main:app --reload`."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise APIError(
            f"The API returned an error ({exc.response.status_code}): "
            f"{_extract_detail(exc.response)}"
        ) from exc


def _to_minor_units(amount: Decimal) -> int:
    """Convert a major-unit decimal amount (e.g. 12.50) to minor units (1250)."""
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _format_money(minor: int | None, currency: str) -> str:
    """Render integer minor units as a human amount, e.g. ``1,250.00 INR``.

    Returns a dash when the amount is missing (e.g. FX conversion failed and
    the expense was stored without a base-currency value).
    """
    if minor is None:
        return "—"
    return f"{Decimal(minor) / 100:,.2f} {currency}"


def _format_dt(iso: str) -> str:
    """Format an ISO-8601 timestamp as ``YYYY-MM-DD HH:MM`` for display."""
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _status_badge(status_value: str) -> str:
    """Return a coloured HTML pill (icon + label) for an expense status."""
    icons = {"approved": "✓", "pending": "⏳", "rejected": "✗"}
    icon = icons.get(status_value, "•")
    safe = html.escape(status_value)
    return f'<span class="badge badge-{safe}">{icon} {safe}</span>'


def _render_kpis(expenses: list[dict]) -> None:
    """Render a row of KPI stat tiles summarising the expense list."""
    base_total = sum(e["amount_base_minor"] or 0 for e in expenses)
    tiles = [
        ("Total expenses", str(len(expenses)), "var(--brand)"),
        ("Base total", _format_money(base_total, "INR"), "var(--brand)"),
        ("Pending", str(sum(e["status"] == "pending" for e in expenses)), "var(--warning)"),
        ("Approved", str(sum(e["status"] == "approved" for e in expenses)), "var(--good)"),
    ]
    cards = "".join(
        f'<div class="kpi" style="--accent:{color}">'
        f'<div class="kpi-label">{label}</div>'
        f'<div class="kpi-value">{value}</div></div>'
        for label, value, color in tiles
    )
    st.markdown(f'<div class="kpi-row">{cards}</div>', unsafe_allow_html=True)


def _render_table(expenses: list[dict]) -> None:
    """Render the expense list as a styled HTML table with status badges."""
    header = (
        "<tr><th>ID</th><th>Description</th><th>Amount</th>"
        "<th>Base (INR)</th><th>Status</th><th>Created</th></tr>"
    )
    body = "".join(
        "<tr>"
        f"<td>{e['id']}</td>"
        f"<td>{html.escape(e['description'])}</td>"
        f"<td class='num'>{html.escape(_format_money(e['amount_minor'], e['currency']))}</td>"
        f"<td class='num'>{html.escape(_format_money(e['amount_base_minor'], e['base_currency']))}</td>"
        f"<td>{_status_badge(e['status'])}</td>"
        f"<td>{html.escape(_format_dt(e['created_at']))}</td>"
        "</tr>"
        for e in expenses
    )
    st.markdown(f'<table class="expense-table">{header}{body}</table>', unsafe_allow_html=True)


st.set_page_config(page_title="ExpenseFlow", page_icon="💸", layout="wide")
st.markdown(_CSS, unsafe_allow_html=True)
st.title("💸 ExpenseFlow")
st.caption(f"Connected to API at `{API_BASE}`")

# Fetch once per run so both the KPI row and the table share the same data.
try:
    expenses: list[dict] | None = _request("GET", "/expenses").json()
except APIError as exc:
    st.error(str(exc))
    expenses = None

if expenses:
    _render_kpis(expenses)

col_form, col_list = st.columns([1, 1.4], gap="large")

# --- Submit a new expense --------------------------------------------------
with col_form:
    st.subheader("➕ Submit an expense")
    with st.form("submit_expense", clear_on_submit=True):
        amount = st.number_input("Amount", min_value=0.01, step=1.0, value=1.0, format="%.2f")
        currency = st.text_input("Currency (ISO 4217)", value="INR", max_chars=3)
        category = st.text_input("Category", placeholder="e.g. Travel")
        description = st.text_area("Description", placeholder="What was this expense for?")
        submitted = st.form_submit_button("Submit expense", type="primary")

    if submitted:
        clean_currency = currency.strip().upper()
        if not description.strip():
            st.warning("Please enter a description.")
        elif len(clean_currency) != 3 or not clean_currency.isalpha():
            st.warning("Currency must be a 3-letter alphabetic code, e.g. USD.")
        else:
            # The API has no category field, so fold it into the description.
            full_description = description.strip()
            if category.strip():
                full_description = f"[{category.strip()}] {full_description}"
            payload = {
                "description": full_description,
                "amount_minor": _to_minor_units(Decimal(str(amount))),
                "currency": clean_currency,
            }
            try:
                created = _request("POST", "/expenses", json=payload).json()
                st.success(f"Submitted expense #{created['id']} — status: {created['status']}.")
                st.rerun()  # refresh the KPI row and table with the new row
            except APIError as exc:
                st.error(str(exc))

# --- List existing expenses ------------------------------------------------
with col_list:
    st.subheader("📋 Expenses")
    st.button("🔄 Refresh")  # any interaction reruns the script and re-fetches
    if expenses is None:
        st.info("Expenses are unavailable while the API is unreachable.")
    elif not expenses:
        st.info("No expenses yet — submit one on the left.")
    else:
        _render_table(expenses)

# --- Insights --------------------------------------------------------------
st.subheader("✨ Insights")
if st.button("Generate insights", type="primary"):
    try:
        insight = _request("GET", "/reports/insights").json().get("insight", {})
    except APIError as exc:
        st.error(str(exc))
    else:
        summary = insight.get("summary", "")
        bullets = insight.get("bullets", [])
        if summary:
            st.markdown(f"#### Summary\n{summary}")
        if bullets:
            st.markdown("#### Highlights")
            for bullet in bullets:
                st.markdown(f"- {bullet}")
        if not summary and not bullets:
            st.info("No insights available yet.")
