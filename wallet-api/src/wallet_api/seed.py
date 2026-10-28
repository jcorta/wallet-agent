from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from wallet_api.models import Account, Contact, Transfer
from wallet_api.settings import Settings


def _account(session: Session, user_id: str, balance: str, daily_limit: str) -> Account:
    account = session.scalar(select(Account).where(Account.user_id == user_id))
    if account is None:
        account = Account(
            user_id=user_id, balance=Decimal(balance), daily_limit=Decimal(daily_limit)
        )
        session.add(account)
        session.flush()
    return account


def seed(session: Session, settings: Settings) -> None:
    """Loads sample data once; does nothing when the sample users already have accounts."""
    with session.begin():
        if session.scalar(select(Account).where(Account.user_id == settings.user1_id)):
            return

        ana = _account(session, settings.user1_id, "500000.00", "200000.00")
        juan = _account(session, settings.user2_id, "100000.00", "100000.00")
        maria = _account(session, "external:maria", "0.00", "0.00")
        gomez = _account(session, "external:gomez", "0.00", "0.00")
        carlos = _account(session, "external:carlos", "0.00", "0.00")

        session.add_all(
            [
                Contact(
                    user_id=ana.user_id,
                    alias="juan",
                    name="Juan Pérez",
                    destination_account_id=juan.id,
                ),
                Contact(
                    user_id=ana.user_id,
                    alias="juan.gomez",
                    name="Juan Gómez",
                    destination_account_id=gomez.id,
                ),
                Contact(
                    user_id=ana.user_id,
                    alias="maria",
                    name="María González",
                    destination_account_id=maria.id,
                ),
                Contact(
                    user_id=juan.user_id,
                    alias="ana",
                    name="Ana Fernández",
                    destination_account_id=ana.id,
                ),
                Contact(
                    user_id=juan.user_id,
                    alias="carlos",
                    name="Carlos Ruiz",
                    destination_account_id=carlos.id,
                ),
            ]
        )
        session.flush()

        now = datetime.now(UTC)
        contacts = {
            c.alias: c
            for c in session.scalars(select(Contact).where(Contact.user_id == ana.user_id))
        }
        session.add_all(
            [
                Transfer(
                    user_id=ana.user_id,
                    source_account_id=ana.id,
                    destination_account_id=juan.id,
                    contact_id=contacts["juan"].id,
                    amount=Decimal("10000.00"),
                    status="completed",
                    idempotency_key="seed-history-1",
                    created_at=now - timedelta(days=3),
                ),
                Transfer(
                    user_id=ana.user_id,
                    source_account_id=ana.id,
                    destination_account_id=maria.id,
                    contact_id=contacts["maria"].id,
                    amount=Decimal("25000.00"),
                    status="completed",
                    idempotency_key="seed-history-2",
                    created_at=now - timedelta(days=2),
                ),
            ]
        )
