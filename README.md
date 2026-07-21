# ExpenseFlow API

A small expense submission and approval API. **Proof of concept — not production.**

One user journey: **submit** an expense → **convert** it to the base currency (INR) →
**approve or reject** it.

## Stack

- Python 3.12 (runs on 3.10+), [FastAPI](https://fastapi.tiangolo.com/) + Uvicorn
- SQLAlchemy ORM on SQLite (`expenseflow.db`)
- [httpx](https://www.python-httpx.org/) for the external FX rate call
- pydantic v2 for request/response models
- pytest for tests

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optional; sensible defaults work out of the box
```

## Run

```bash
python -m uvicorn app.main:app --reload
```

Then open the interactive docs:

- Swagger UI (try requests live): http://127.0.0.1:8000/docs
- ReDoc (read-only reference): http://127.0.0.1:8000/redoc

Tables are created automatically on startup.

## Test

```bash
python -m pytest -q                       # full suite
python -m pytest -q tests/test_fx.py      # a single file
python -m pytest -q tests/test_fx.py::test_foreign_currency_parses_rate   # a single test
```

The FX call is mocked in tests, so the suite runs fully offline.

## Endpoints

| Method | Path                          | Description                                   |
|--------|-------------------------------|-----------------------------------------------|
| POST   | `/expenses`                   | Submit an expense; converts to INR on write   |
| GET    | `/expenses/{expense_id}`      | Fetch one expense                             |
| POST   | `/expenses/{expense_id}/approve` | Approve a pending expense                  |
| POST   | `/expenses/{expense_id}/reject`  | Reject a pending expense                   |

### Example

```bash
# Submit USD $100.00 (amount is in integer minor units: 10000 = $100.00)
curl -X POST http://127.0.0.1:8000/expenses \
  -H 'Content-Type: application/json' \
  -d '{"description":"Conference hotel","amount_minor":10000,"currency":"USD"}'
```

```json
{
  "id": 1,
  "description": "Conference hotel",
  "amount_minor": 10000,
  "currency": "USD",
  "amount_base_minor": 962300,
  "base_currency": "INR",
  "fx_rate": 96.23,
  "status": "pending",
  "created_at": "2026-07-21T17:17:12.967095"
}
```

```bash
curl -X POST http://127.0.0.1:8000/expenses/1/approve   # -> status: "approved"
```

Status codes: `201` on submit, `409` if approving/rejecting an already-decided
expense, `404` for an unknown id, `422` for invalid input (e.g. non-positive amount).

## Design notes

- **Money is stored as integer minor units** (paise / cents), never float. Conversion
  uses `Decimal` with half-up rounding.
- **Base currency is INR.** Every amount is normalised to base *on write*, so reads never
  need to convert. A submission already in INR uses rate `1.0` and makes no network call.
- **FX rates** come from [Frankfurter](https://frankfurter.dev/) (ECB data, no API key).
  The provider URL and an optional key are read from the environment.
- **Configuration** is read from environment variables (via python-dotenv); see
  `.env.example`. Nothing secret is hardcoded.

## Layout

```
app/
  main.py      # FastAPI app + startup (creates tables)
  db.py        # engine, session, get_db dependency
  models.py    # SQLAlchemy Expense model
  schemas.py   # pydantic v2 request/response models
  routes.py    # endpoints + FX lookup and conversion
tests/         # end-to-end journey tests + mocked-httpx FX unit tests
```

## Configuration

| Variable       | Default                                | Purpose                                  |
|----------------|----------------------------------------|------------------------------------------|
| `FX_API_URL`   | `https://api.frankfurter.dev/v1/latest`| External FX rate provider                |
| `FX_API_KEY`   | _(unset)_                              | Sent as `access_key` if your provider needs one |
| `DATABASE_URL` | `sqlite:///expenseflow.db`             | SQLAlchemy database URL                  |
