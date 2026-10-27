"""Behavior shared by the CRUD services."""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal_auto.services.errors import Duplicate, Invalid


def required(value: str, field: str) -> str:
    """Strip `value`; the operator must have typed something."""
    value = value.strip()
    if not value:
        raise Invalid(f"{field} is required")
    return value


def commit_unique(session: Session, entity, duplicate_message: str):
    """Commit; a uniqueness violation becomes a Duplicate with an operator-safe message."""
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise Duplicate(duplicate_message) from None
    return entity
