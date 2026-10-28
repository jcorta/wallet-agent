import unicodedata
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class TransferArgs(BaseModel):
    """Arguments the model supplies to `request_transfer`."""

    contact: str = Field(
        description="Name or alias of the destination contact, as the user said it."
    )
    amount: Decimal = Field(
        description="Amount in Argentine pesos as a plain number, e.g. 50000 or 1500.50."
    )


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold().strip()


def resolve_contact(reference: str, contacts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Contacts matching what the user said: an exact full name wins, otherwise every word
    of the reference must appear among a contact's name or alias words."""
    wanted = _normalize(reference)
    exact = [c for c in contacts if _normalize(c["name"]) == wanted]
    if exact:
        return exact
    words = set(wanted.split())
    if not words:
        return []
    return [
        c
        for c in contacts
        if words <= set(_normalize(c["name"]).split()) | set(_normalize(c["alias"]).split())
    ]


def format_ars(amount: Decimal) -> str:
    """50000 -> $50.000,00"""
    text = f"{amount:,.2f}"
    return "$" + text.replace(",", "\0").replace(".", ",").replace("\0", ".")
