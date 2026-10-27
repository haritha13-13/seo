import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from portal_auto.db.models import Portal
from portal_auto.services._common import commit_unique, required
from portal_auto.services.errors import InUse, Invalid, NotFound

_URL_PREFIXES = ("http://", "https://")
_HOSTNAME = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")


class PortalService:
    def __init__(self, session: Session):
        self._session = session

    def create(self, name: str, url: str, extra_hosts: str = "") -> Portal:
        portal = Portal(name=required(name, "Portal name"), url=_clean_url(url), extra_hosts=_clean_hosts(extra_hosts))
        self._session.add(portal)
        return self._commit(portal)

    def update(self, portal_id: int, name: str, url: str, extra_hosts: str | None = None) -> Portal:
        portal = self.get(portal_id)
        portal.name = required(name, "Portal name")
        if extra_hosts is not None:
            portal.extra_hosts = _clean_hosts(extra_hosts)
        new_url = _clean_url(url)
        if new_url != portal.url:
            for workflow in portal.workflows:  # scripts were generated against the old URL
                workflow.steps_revision += 1
        portal.url = new_url
        return self._commit(portal)

    def set_active(self, portal_id: int, active: bool) -> Portal:
        portal = self.get(portal_id)
        portal.active = active
        return self._commit(portal)

    def get(self, portal_id: int) -> Portal:
        portal = self._session.get(Portal, portal_id)
        if portal is None:
            raise NotFound("Portal not found")
        return portal

    def list(self, active_only: bool = False) -> list[Portal]:
        query = select(Portal).order_by(Portal.name)
        if active_only:
            query = query.where(Portal.active)
        return list(self._session.scalars(query))

    def delete(self, portal_id: int) -> None:
        portal = self.get(portal_id)
        if portal.credentials or portal.workflows:
            raise InUse("Portal has credentials or workflows; deactivate it instead")
        self._session.delete(portal)
        self._session.commit()

    def _commit(self, portal: Portal) -> Portal:
        return commit_unique(self._session, portal, "A portal with this name already exists")


def _clean_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(_URL_PREFIXES):
        raise Invalid("Portal URL must start with http:// or https://")
    return url


def _clean_hosts(text: str) -> str:
    hosts = [host.strip().lower() for host in text.replace("\n", ",").split(",") if host.strip()]
    for host in hosts:
        if not _HOSTNAME.match(host):
            raise Invalid(f"Extra host {host!r} must be a bare hostname such as adobe.com (no scheme, path or spaces)")
    return ", ".join(hosts)
