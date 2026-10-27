"""A Credential's Saved Session: import, validate, keep encrypted, hand out, refresh (ADR 0002, 0004)."""
import json
from datetime import datetime
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy import update
from sqlalchemy.orm import Session

from portal_auto.db.models import Credential, utcnow
from portal_auto.services.errors import Invalid, NotFound

MAX_SESSION_BYTES = 2 * 1024 * 1024
SOURCE_IMPORTED = "imported"
SOURCE_CAPTURED = "captured"
SOURCE_REFRESHED = "refreshed"


class SessionVault(Protocol):
    def encrypt(self, plain: str) -> str: ...

    def decrypt(self, token: str) -> str: ...


@dataclass(frozen=True)
class ImportResult:
    warnings: list[str]


class SavedSessionService:
    def __init__(self, session: Session, vault: SessionVault):
        self._session = session
        self._vault = vault

    def import_session(self, credential_id: int, raw: bytes, source: str = SOURCE_IMPORTED) -> ImportResult:
        credential = self._credential(credential_id)
        state = _parse(raw)
        self._store(credential, state, source)
        return ImportResult(_host_warnings(credential, state))

    def refresh(self, credential_id: int, state_json: str) -> None:
        credential = self._credential(credential_id)
        self._store(credential, _parse(state_json.encode()), SOURCE_REFRESHED)

    def refresh_if_unchanged(self, credential_id: int, state_json: str, expected_updated_at: datetime | None) -> bool:
        """Compare-and-swap: stores only while the session is still the one stamped `expected_updated_at`."""
        credential = self._credential(credential_id)
        state = _parse(state_json.encode())
        if expected_updated_at is None:
            return False
        stored = self._session.execute(
            update(Credential)
            .where(Credential.id == credential.id, Credential.session_updated_at == expected_updated_at,
                   Credential.session_encrypted.is_not(None))
            .values(session_encrypted=self._vault.encrypt(json.dumps(state)), session_updated_at=utcnow(),
                    session_source=SOURCE_REFRESHED)
        ).rowcount
        self._session.commit()
        return stored > 0

    def updated_at(self, credential_id: int) -> datetime | None:
        return self._credential(credential_id).session_updated_at

    def looks_logged_out(self, credential_id: int, previous_json: str, refreshed_json: str) -> bool:
        """A refreshed state with no cookie for the Portal, or under half the previous cookies, is probably logged out."""
        credential = self._credential(credential_id)
        previous, refreshed = _parse(previous_json.encode()), _parse(refreshed_json.encode())
        return not _matches_portal(credential, refreshed) or len(refreshed["cookies"]) * 2 < len(previous["cookies"])

    def load(self, credential_id: int) -> str | None:
        credential = self._credential(credential_id)
        return self._vault.decrypt(credential.session_encrypted) if credential.has_session else None

    def clear(self, credential_id: int) -> None:
        credential = self._credential(credential_id)
        credential.session_encrypted = credential.session_updated_at = credential.session_source = None
        self._session.commit()

    def _store(self, credential: Credential, state: dict, source: str) -> None:
        credential.session_encrypted = self._vault.encrypt(json.dumps(state))
        credential.session_updated_at = utcnow()
        credential.session_source = source
        self._session.commit()

    def _credential(self, credential_id: int) -> Credential:
        credential = self._session.get(Credential, credential_id)
        if credential is None:
            raise NotFound("Credential not found")
        return credential


def _parse(raw: bytes) -> dict:
    if len(raw) > MAX_SESSION_BYTES:
        raise Invalid("The session file is larger than 2 MB")
    try:
        state = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise Invalid("The session file is not valid JSON") from None
    if not _is_session_state(state):
        raise Invalid("Not a browser session file: expected a JSON object with 'cookies' and 'origins' lists")
    return state


def _is_session_state(state: object) -> bool:
    return (
        isinstance(state, dict)
        and isinstance(state.get("cookies"), list)
        and isinstance(state.get("origins"), list)
        and all(_is_named_value(cookie) for cookie in state["cookies"])
        and all(_is_origin(origin) for origin in state["origins"])
    )


def _is_named_value(item: object) -> bool:
    return isinstance(item, dict) and isinstance(item.get("name"), str) and isinstance(item.get("value"), str)


def _is_origin(origin: object) -> bool:
    if not isinstance(origin, dict):
        return False
    storage = origin.get("localStorage", [])
    return isinstance(storage, list) and all(_is_named_value(item) for item in storage)


def _hosts_in(state: dict) -> set[str]:
    hosts = {str(cookie.get("domain", "")).lstrip(".").lower() for cookie in state["cookies"]}
    hosts |= {(urlsplit(str(origin.get("origin", ""))).hostname or "").lower() for origin in state["origins"]}
    return hosts - {""}


def _related(host: str, other: str) -> bool:
    return host == other or host.endswith(f".{other}") or other.endswith(f".{host}")


def _wanted_hosts(credential: Credential) -> set[str]:
    portal = credential.portal
    return {(urlsplit(portal.url).hostname or "").lower(), *portal.allowed_hosts} - {""}


def _matches_portal(credential: Credential, state: dict) -> bool:
    return any(_related(have, want) for have in _hosts_in(state) for want in _wanted_hosts(credential))


def _host_warnings(credential: Credential, state: dict) -> list[str]:
    if _matches_portal(credential, state):
        return []
    return [f"This session has no cookies for {', '.join(sorted(_wanted_hosts(credential)))}; it may belong to a different site."]
