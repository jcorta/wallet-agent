import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

MONEY = Numeric(18, 2)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("balance >= 0", name="ck_accounts_balance_non_negative"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), unique=True)
    balance: Mapped[Decimal] = mapped_column(MONEY)
    daily_limit: Mapped[Decimal] = mapped_column(MONEY)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("user_id", "alias", name="uq_contacts_user_alias"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    alias: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    destination_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))


class Transfer(Base):
    """Every transfer attempt, including rejected ones, which makes it the audit trail."""

    __tablename__ = "transfers"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_transfers_user_idempotency_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    source_account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id"))
    destination_account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("accounts.id"))
    contact_id: Mapped[uuid.UUID] = mapped_column()
    amount: Mapped[Decimal] = mapped_column(MONEY)
    status: Mapped[str] = mapped_column(String(16))
    rejection_reason: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
