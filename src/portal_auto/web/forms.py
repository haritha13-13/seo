"""Shared helpers for the form-driven screens: reading form values and reporting domain errors."""
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from flask import flash, request

from portal_auto.services.errors import DomainError, NotFound


@dataclass
class Outcome:
    ok: bool = False


def text(name: str) -> str:
    return request.form.get(name, "").strip()


def raw(name: str) -> str:
    return request.form.get(name, "")


def checked(name: str) -> bool:
    return name in request.form


def integer(value: str | None) -> int | None:
    try:
        return int(value) if value else None
    except ValueError:
        return None


def success(message: str) -> None:
    flash(message, "success")


@contextmanager
def reporting_errors() -> Iterator[Outcome]:
    """Run a block; a DomainError becomes an error flash and leaves outcome.ok False."""
    outcome = Outcome()
    try:
        yield outcome
        outcome.ok = True
    except NotFound:
        raise  # an unknown id is a 404 page, not a form error
    except DomainError as error:
        flash(str(error), "error")


def toggle_active(setter: Callable[[int, bool], Any], entity_id: int, kind: str) -> None:
    """Shared body of every `POST .../active` handler: apply the posted flag and flash the outcome."""
    active = text("active") == "1"
    with reporting_errors():
        entity = setter(entity_id, active)
        success(f"{kind} {getattr(entity, 'name', None) or entity.label} {'activated' if active else 'deactivated'}")
