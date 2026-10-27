from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from portal_auto.db.models import Credential, Run
from portal_auto.services._common import commit_unique, required
from portal_auto.services.errors import InUse, NotFound
from portal_auto.services.portals import PortalService


class Vault(Protocol):
    """What the service needs from a vault; callers may pass one that only builds its key on first use."""

    def encrypt(self, plain: str) -> str: ...


class CredentialService:
    def __init__(self, session: Session, vault: Vault):
        self._session = session
        self._vault = vault

    def create(self, portal_id: int, label: str, username: str, password: str | None = None) -> Credential:
        PortalService(self._session).get(portal_id)
        credential = Credential(
            portal_id=portal_id,
            label=required(label, "Label"),
            username=required(username, "Username"),
            password_encrypted=self._vault.encrypt(password) if password else None,
        )
        self._session.add(credential)
        return self._commit(credential)

    def update(self, credential_id: int, label: str, username: str, password: str | None = None) -> Credential:
        credential = self.get(credential_id)
        credential.label = required(label, "Label")
        credential.username = required(username, "Username")
        if password:
            credential.password_encrypted = self._vault.encrypt(password)
        return self._commit(credential)

    def set_active(self, credential_id: int, active: bool) -> Credential:
        credential = self.get(credential_id)
        credential.active = active
        return self._commit(credential)

    def get(self, credential_id: int) -> Credential:
        credential = self._session.get(Credential, credential_id)
        if credential is None:
            raise NotFound("Credential not found")
        return credential

    def list_for_portal(self, portal_id: int, active_only: bool = False) -> list[Credential]:
        query = select(Credential).where(Credential.portal_id == portal_id).order_by(Credential.label)
        if active_only:
            query = query.where(Credential.active)
        return list(self._session.scalars(query))

    def delete(self, credential_id: int) -> None:
        credential = self.get(credential_id)
        used = self._session.scalar(select(Run.id).where(Run.credential_id == credential_id).limit(1))
        if used is not None:
            raise InUse("Credential has been used in Runs; deactivate it instead")
        self._session.delete(credential)
        self._session.commit()

    def _commit(self, credential: Credential) -> Credential:
        return commit_unique(self._session, credential, "This portal already has a credential with this label")
