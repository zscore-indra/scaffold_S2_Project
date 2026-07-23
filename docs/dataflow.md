# ExpenseFlow API — Dataflow Documentation

Data flow, module map, and data models for the ExpenseFlow PoC.

Single user journey: **submit an expense → convert it to the base currency (INR) → approve or reject it.**

> All diagrams below are [Mermaid](https://mermaid.js.org/); they render inline on GitHub and in most Markdown viewers.

---

## 1. System at a glance

| Concern | Choice |
| --- | --- |
| Framework | FastAPI + Uvicorn (Python 3.12) |
| Persistence | SQLAlchemy ORM on SQLite (`expenseflow.db`) |
| External call | `httpx` GET to an FX-rate provider (Frankfurter by default, keyless) |
| Serialization | Pydantic v2 (`ExpenseCreate` in, `ExpenseOut` out) |
| Money | Integer **minor units** (paise/cents) — never float |
| Base currency | **INR**; every amount is normalised to base **on write** |

### External entities & trust boundary

- **API Client** — submits and decides expenses over HTTP/JSON.
- **FX Provider** — external HTTP service returning `rates.INR`. Configurable via `FX_API_URL` / `FX_API_KEY`.
- **SQLite store** — the `expenses` table in `expenseflow.db`.

---

## 2. Module map

Each source module and the data it owns:

| Module | Role | Owns / Produces | Inputs | Outputs |
| --- | --- | --- | --- | --- |
| `app/main.py` | Composition root / entrypoint | The `FastAPI` app instance | Env vars (via `python-dotenv`) | Mounted router; `init_db()` on startup |
| `app/db.py` | DB engine & session lifecycle | `engine`, `SessionLocal`, `Base`, `init_db()`, `get_db()` | `DATABASE_URL` | Per-request `Session` (dependency) |
| `app/models.py` | ORM layer | `Expense` table, status/currency constants | — | Persistent rows |
| `app/schemas.py` | API contract (validation & serialization) | `ExpenseCreate`, `ExpenseOut` | Raw JSON / ORM objects | Validated DTOs / JSON |
| `app/routes.py` | Endpoints + business logic | 4 routes, `get_fx_rate()`, `_to_base_minor()`, `_decide()`, `_get_or_404()` | HTTP requests, `Session`, FX rate | `ExpenseOut`, HTTP errors |

### Module dependency graph

```mermaid
flowchart TD
    main["app/main.py<br/>entrypoint · loads .env · lifespan"]
    routes["app/routes.py<br/>endpoints + FX + conversion"]
    schemas["app/schemas.py<br/>ExpenseCreate / ExpenseOut"]
    models["app/models.py<br/>Expense ORM + constants"]
    db["app/db.py<br/>engine · SessionLocal · get_db"]

    main --> routes
    main --> db
    routes --> schemas
    routes --> models
    routes --> db
    models --> db
    routes -. "httpx.get" .-> FX([FX Provider])
    db -. "SQLAlchemy" .-> SQLite[("expenseflow.db")]
```

---

## 3. Data models

### 3.1 Wire models (Pydantic v2) and ORM model

```mermaid
classDiagram
    class ExpenseCreate {
      <<request DTO>>
      +str description  «min_len 1»
      +int amount_minor «gt 0»
      +str currency     «3-alpha, upper-cased»
    }
    class Expense {
      <<ORM · table: expenses>>
      +int id  «PK»
      +str description
      +int amount_minor
      +str currency
      +int amount_base_minor
      +float fx_rate
      +str status
      +datetime created_at
      +base_currency() str
    }
    class ExpenseOut {
      <<response DTO>>
      +int id
      +str description
      +int amount_minor
      +str currency
      +int amount_base_minor
      +str base_currency
      +float fx_rate
      +str status
      +datetime created_at
    }
    ExpenseCreate ..> Expense : mapped on write (+ fx enrichment)
    Expense ..> ExpenseOut : serialised (from_attributes)
```

### 3.2 Data dictionary — `expenses` table

| Field | Type | Unit / Constraint | Source |
| --- | --- | --- | --- |
| `id` | int, PK | autoincrement | DB |
| `description` | str, not null | min length 1 | Client |
| `amount_minor` | int, not null | minor units of `currency`, > 0 | Client |
| `currency` | str(3), not null | ISO-4217, upper-cased | Client (normalised) |
| `amount_base_minor` | int, not null | **INR** minor units | Computed: `round_half_up(amount_minor × fx_rate)` |
| `fx_rate` | float, not null | base per 1 source unit | FX Provider (or `1.0`) |
| `status` | str, not null | `pending` \| `approved` \| `rejected` | System |
| `created_at` | datetime, not null | UTC | System default |

Constants (`app/models.py`): `BASE_CURRENCY = "INR"`, `STATUS_PENDING/APPROVED/REJECTED`.
`base_currency` on `ExpenseOut` is a read-only property returning `BASE_CURRENCY` — it is not a stored column.

---

## 4. Dataflow diagrams

### 4.1 Level 0 — Context diagram

```mermaid
flowchart LR
    Client([API Client])
    FX([External FX Provider])
    DB[("SQLite · expenses")]
    System[["ExpenseFlow API"]]

    Client -->|"submit / get / approve / reject (JSON)"| System
    System -->|"ExpenseOut JSON · errors"| Client
    System -->|"GET ?base=&symbols=INR (+access_key?)"| FX
    FX -->|"rates.INR"| System
    System -->|"INSERT / SELECT / UPDATE"| DB
    DB -->|"Expense rows"| System
```

### 4.2 Level 1 — Processes, stores, and flows

```mermaid
flowchart TB
    Client([API Client])
    FX([FX Provider])
    DB[("D1 · expenses table")]

    subgraph EF["ExpenseFlow API"]
        P1["1.0 Validate & Submit<br/>POST /expenses"]
        P2["2.0 Fetch FX Rate<br/>get_fx_rate()"]
        P3["3.0 Normalise to Base<br/>_to_base_minor()"]
        P4["4.0 Retrieve Expense<br/>GET /expenses/{id}"]
        P5["5.0 Decide Expense<br/>approve / reject"]
    end

    %% Submit path
    Client -->|"ExpenseCreate"| P1
    P1 -->|"source currency"| P2
    P2 -->|"GET request"| FX
    FX -->|"rate"| P2
    P2 -->|"fx_rate"| P3
    P1 -->|"amount_minor"| P3
    P3 -->|"amount_base_minor"| P1
    P1 -->|"INSERT pending row"| DB
    DB -->|"row (id, created_at)"| P1
    P1 -->|"201 ExpenseOut"| Client

    %% Read path
    Client -->|"expense_id"| P4
    DB -->|"row / none"| P4
    P4 -->|"200 ExpenseOut / 404"| Client

    %% Decision path
    Client -->|"expense_id + action"| P5
    DB -->|"row / none"| P5
    P5 -->|"UPDATE status"| DB
    P5 -->|"200 ExpenseOut / 404 / 409"| Client
```

---

## 5. Endpoint flows (input → output)

| Method & path | Input | Success | Errors |
| --- | --- | --- | --- |
| `POST /expenses` | `ExpenseCreate` JSON | `201` + `ExpenseOut` (pending) | `422` invalid body · `502` FX unreadable · httpx error on FX HTTP failure |
| `GET /expenses/{id}` | path `id` | `200` + `ExpenseOut` | `404` not found |
| `POST /expenses/{id}/approve` | path `id` | `200` + `ExpenseOut` (approved) | `404` · `409` already decided |
| `POST /expenses/{id}/reject` | path `id` | `200` + `ExpenseOut` (rejected) | `404` · `409` already decided |

### 5.1 Submit & convert — sequence

```mermaid
sequenceDiagram
    actor C as API Client
    participant R as routes.submit_expense
    participant S as ExpenseCreate (validate)
    participant FXf as get_fx_rate()
    participant FX as FX Provider
    participant Cv as _to_base_minor()
    participant DB as SQLite

    C->>R: POST /expenses {description, amount_minor, currency}
    R->>S: validate + upper-case currency
    alt invalid payload
        S-->>C: 422 Unprocessable Entity
    end
    R->>FXf: get_fx_rate(currency)
    alt currency == INR
        FXf-->>R: 1.0  (no network call)
    else foreign currency
        FXf->>FX: GET ?base=CUR&symbols=INR (+access_key?)
        FX-->>FXf: {"rates": {"INR": rate}}
        alt rate missing / malformed
            FXf-->>C: 502 Bad Gateway
        else HTTP >= 400
            FXf-->>C: httpx.HTTPStatusError
        end
        FXf-->>R: rate
    end
    R->>Cv: _to_base_minor(amount_minor, rate)
    Cv-->>R: amount_base_minor (ROUND_HALF_UP)
    R->>DB: INSERT Expense(status=pending)
    DB-->>R: row (id, created_at)
    R-->>C: 201 ExpenseOut
```

### 5.2 Approve / Reject — sequence

```mermaid
sequenceDiagram
    actor C as API Client
    participant R as approve_expense / reject_expense
    participant D as _decide() / _get_or_404()
    participant DB as SQLite

    C->>R: POST /expenses/{id}/approve|reject
    R->>D: _get_or_404(id)
    D->>DB: SELECT by id
    alt not found
        DB-->>D: None
        D-->>C: 404 Not Found
    end
    DB-->>D: Expense
    alt status != pending
        D-->>C: 409 Conflict ("already {status}")
    else pending
        D->>DB: UPDATE status = approved|reject; commit; refresh
        DB-->>D: refreshed row
        D-->>C: 200 ExpenseOut
    end
```

---

## 6. Expense lifecycle (state)

```mermaid
stateDiagram-v2
    [*] --> pending: POST /expenses
    pending --> approved: POST /{id}/approve
    pending --> rejected: POST /{id}/reject
    approved --> [*]
    rejected --> [*]
    note right of approved
        Terminal. Any further
        approve/reject → 409
    end note
```

---

## 7. Money normalisation (the core transformation)

Conversion happens **once, on write**, so every downstream read is already in base currency.

```
amount_base_minor = ROUND_HALF_UP( amount_minor × fx_rate )     # integer minor units
fx_rate           = 1.0                     if currency == INR   (no network call)
                  = rates.INR from provider otherwise
```

Worked example — submit `10000` USD minor units at rate `83.0`:

```
_to_base_minor(10000, 83.0) = round_half_up(Decimal(10000) × Decimal("83.0")) = 830000  # ₹8,300.00
```

`Decimal` + `ROUND_HALF_UP` is used deliberately to avoid binary-float drift on money.

---

## 8. Configuration & runtime

Environment variables (loaded from `.env` via `python-dotenv`; see `.env.example`):

| Var | Default | Used by |
| --- | --- | --- |
| `FX_API_URL` | `https://api.frankfurter.dev/v1/latest` | `routes.get_fx_rate` |
| `FX_API_KEY` | *(unset)* | added as `access_key` param only if present |
| `DATABASE_URL` | `sqlite:///expenseflow.db` | `db.create_engine` |

**Startup:** `main.py` loads env → `init_db()` (via lifespan) creates tables → router mounted.
**Per request:** `get_db()` yields a `Session` and closes it when the request ends.

---

## 9. Notes / observations

- `CLAUDE.md` still says the project is "not yet implemented" — that is **stale**; all `app/` modules, tests, and the DB now exist. Worth updating.
- The working tree's `app/main.py` re-introduces the `lifespan`/`init_db()` handler that commit `c05bfaa` had removed (`git status` shows it modified). Tests deliberately **skip** lifespan (no `with TestClient(...)`) so the real `expenseflow.db` is never touched, and stub `get_fx_rate` so no network is hit.
- FX failures surface two ways: a malformed/missing rate → `502`; an HTTP ≥ 400 from the provider → the raw `httpx.HTTPStatusError` propagates.
