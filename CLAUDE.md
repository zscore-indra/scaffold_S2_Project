# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

This project is **not yet implemented**. The only source of truth so far is `poc_scope.txt`
(the project brief). None of the code, `app/` package, database, or virtualenv described below
exists yet — they are the intended target. Build against the brief; when the brief and any
future code disagree, ask before diverging.

## What this is

ExpenseFlow API — a small expense submission and approval API (PoC, not production).
Single user journey: submit an expense, convert it to a base currency, approve or reject it.

## Stack

- Python 3.12, FastAPI, Uvicorn
- SQLAlchemy ORM on SQLite (file: `expenseflow.db`)
- httpx for the external FX rate call
- pydantic v2 for request/response models
- pytest for tests

## Commands

- Run: `python -m uvicorn app.main:app --reload`
- Test (all): `python -m pytest -q`
- Test (single): `python -m pytest -q path/to/test_file.py::test_name`

## Intended layout

`app/main.py`, `app/db.py`, `app/models.py`, `app/schemas.py`, `app/routes.py`

## Conventions (from the brief — enforce these)

- **Money is stored as integer minor units** (paise / cents), never float.
- **Base currency is INR.** All amounts are normalised to base on write (not on read).
- Read secrets from environment variables via python-dotenv; never hardcode them.
- Type hints on every function. Docstrings on every endpoint.

## Guardrails (from the brief — do not violate)

- Do not edit `.venv`, `.git`, or `expenseflow.db` directly.
- Do not add new third-party dependencies without asking the user first.
- Do not invent endpoints that are not in the brief.
