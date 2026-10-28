from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from wallet_api.errors import ApiError
from wallet_api.models import Account, Contact, Transfer
from wallet_api.schemas import TransferRequest

COMPLETED = "completed"
REJECTED = "rejected"


def _account_for(session: Session, user_id: str) -> Account:
    account = session.scalar(select(Account).where(Account.user_id == user_id))
    if account is None:
        raise ApiError(404, "account_not_found", "No account for this user")
    return account


def _start_of_today(timezone: str) -> datetime:
    zone = ZoneInfo(timezone)
    midnight = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(UTC)


def spent_today(session: Session, user_id: str, timezone: str) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(Transfer.amount), 0)).where(
            Transfer.user_id == user_id,
            Transfer.status == COMPLETED,
            Transfer.created_at >= _start_of_today(timezone),
        )
    )
    return Decimal(total).quantize(Decimal("0.01"))


def get_account(session: Session, user_id: str) -> Account:
    return _account_for(session, user_id)


def list_contacts(session: Session, user_id: str) -> list[Contact]:
    return list(
        session.scalars(select(Contact).where(Contact.user_id == user_id).order_by(Contact.name))
    )


def list_transactions(
    session: Session, user_id: str, limit: int
) -> list[tuple[Transfer, str, str | None]]:
    """Returns (transfer, direction, counterparty name) newest first."""
    account = _account_for(session, user_id)
    transfers = session.scalars(
        select(Transfer)
        .where(
            or_(
                Transfer.source_account_id == account.id,
                and_(Transfer.destination_account_id == account.id, Transfer.status == COMPLETED),
            )
        )
        .order_by(Transfer.created_at.desc())
        .limit(limit)
    ).all()
    names = {c.destination_account_id: c.name for c in list_contacts(session, user_id)}
    result: list[tuple[Transfer, str, str | None]] = []
    for transfer in transfers:
        if transfer.source_account_id == account.id:
            result.append((transfer, "outgoing", names.get(transfer.destination_account_id)))
        else:
            result.append((transfer, "incoming", names.get(transfer.source_account_id)))
    return result


def execute_transfer(
    session: Session, user_id: str, request: TransferRequest, timezone: str
) -> tuple[Transfer, bool]:
    """Applies all transfer controls atomically. Returns (transfer, replayed).

    Both accounts are locked in id order before anything is checked, so concurrent
    transfers cannot overdraw an account and opposite transfers cannot deadlock.
    """
    with session.begin():
        source_id = _account_for(session, user_id).id
        contact = session.scalar(
            select(Contact).where(Contact.id == request.contact_id, Contact.user_id == user_id)
        )
        destination_id = contact.destination_account_id if contact else None

        lock_ids = {source_id} | ({destination_id} if destination_id else set())
        locked = {
            account.id: account
            for account in session.scalars(
                select(Account)
                .where(Account.id.in_(lock_ids))
                .order_by(Account.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        }
        source = locked[source_id]

        existing = session.scalar(
            select(Transfer).where(
                Transfer.user_id == user_id, Transfer.idempotency_key == request.idempotency_key
            )
        )
        if existing is not None:
            if existing.contact_id != request.contact_id or existing.amount != request.amount:
                raise ApiError(
                    409,
                    "idempotency_conflict",
                    "Idempotency key was already used with a different request",
                )
            return existing, True

        reason = _first_violation(session, user_id, request, source, contact, timezone)
        transfer = Transfer(
            user_id=user_id,
            source_account_id=source_id,
            destination_account_id=destination_id,
            contact_id=request.contact_id,
            amount=request.amount,
            status=REJECTED if reason else COMPLETED,
            rejection_reason=reason,
            idempotency_key=request.idempotency_key,
        )
        if reason is None:
            source.balance -= request.amount
            locked[destination_id].balance += request.amount
        session.add(transfer)
        session.flush()
        session.refresh(transfer)
        return transfer, False


def _first_violation(
    session: Session,
    user_id: str,
    request: TransferRequest,
    source: Account,
    contact: Contact | None,
    timezone: str,
) -> str | None:
    if contact is None:
        return "unknown_contact"
    if contact.destination_account_id == source.id:
        return "self_transfer"
    if request.amount <= 0:
        return "invalid_amount"
    if source.balance < request.amount:
        return "insufficient_funds"
    if spent_today(session, user_id, timezone) + request.amount > source.daily_limit:
        return "daily_limit_exceeded"
    return None


REJECTION_MESSAGES = {
    "unknown_contact": "The contact does not exist",
    "self_transfer": "Cannot transfer to your own account",
    "invalid_amount": "Amount must be greater than zero",
    "insufficient_funds": "Insufficient funds",
    "daily_limit_exceeded": "Daily transfer limit exceeded",
}


def rejection_error(transfer: Transfer) -> ApiError:
    reason = transfer.rejection_reason or "rejected"
    return ApiError(422, reason, REJECTION_MESSAGES.get(reason, "Transfer rejected"))
