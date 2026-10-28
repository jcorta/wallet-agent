import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class BalanceResponse(BaseModel):
    balance: Decimal
    daily_limit: Decimal
    spent_today: Decimal
    currency: Literal["ARS"] = "ARS"


class ContactResponse(BaseModel):
    id: uuid.UUID
    alias: str
    name: str


class TransactionResponse(BaseModel):
    id: uuid.UUID
    direction: Literal["outgoing", "incoming"]
    status: Literal["completed", "rejected"]
    amount: Decimal
    counterparty_name: str | None
    rejection_reason: str | None
    created_at: datetime


class TransferRequest(BaseModel):
    contact_id: uuid.UUID
    # Sign is validated by the transfer controls so that rejected attempts are audited.
    amount: Annotated[Decimal, Field(max_digits=16, decimal_places=2)]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]


class TransferResponse(BaseModel):
    id: uuid.UUID
    status: Literal["completed"]
    amount: Decimal
    contact_id: uuid.UUID
    created_at: datetime


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
