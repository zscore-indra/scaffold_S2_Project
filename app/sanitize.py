"""PII redaction for free-text expense fields.

Provides best-effort masking of personally identifiable information found in
free-text (chiefly the expense ``description``) before it is logged or forwarded
to an external service such as the LLM insight generator. Emails, phone numbers,
and long digit runs that resemble card or account numbers are replaced with the
typed placeholders ``[EMAIL]``, ``[PHONE]``, and ``[CARD]``.

The matching is heuristic and errs toward redaction. Only free text is touched:
structured numeric fields (e.g. ``amount_base_minor``) are never masked.
"""

import re

# Order of application matters: emails are masked first so the digits inside an
# address are not re-matched as a phone/card; card-length runs (13+ digits) are
# masked before the shorter phone patterns.
_EMAIL_RE: re.Pattern[str] = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

# Card / account numbers: 13 or more digits, optionally grouped by single spaces
# or hyphens (e.g. "4111 1111 1111 1111", "4111-1111-1111-1111").
_CARD_RE: re.Pattern[str] = re.compile(r"\b\d(?:[ -]?\d){12,}\b")

# Phone numbers: optional leading "+", then 7-15 digits with up to two separator
# characters (space, dot, hyphen, parentheses) between digits.
_PHONE_RE: re.Pattern[str] = re.compile(r"\+?\d(?:[\s().-]{0,2}\d){6,14}")


def mask_pii(text: str) -> str:
    """Return ``text`` with emails, phone numbers, and card/account numbers masked.

    Each match is replaced with a typed placeholder: ``[EMAIL]``, ``[PHONE]``, or
    ``[CARD]``. Substitutions run in a fixed order (email, then card, then phone)
    so overlapping numeric matches are resolved consistently.
    """
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _CARD_RE.sub("[CARD]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    return text


def mask_expense(expense: dict) -> dict:
    """Return a shallow copy of ``expense`` with its ``description`` masked.

    Only the free-text ``description`` is passed through :func:`mask_pii`; all
    other fields — including structured numeric fields such as
    ``amount_base_minor`` — are copied through unchanged.
    """
    masked = dict(expense)
    description = masked.get("description")
    if isinstance(description, str):
        masked["description"] = mask_pii(description)
    return masked
