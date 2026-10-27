"""Per-request wiring of services; the only place web code builds services."""
from flask import current_app, g

from portal_auto.services.errors import Invalid
from portal_auto.services.credentials import CredentialService
from portal_auto.services.portals import PortalService
from portal_auto.services.saved_sessions import SavedSessionService
from portal_auto.services.vault import CredentialVault
from portal_auto.services.workflows import WorkflowService


def portals() -> PortalService:
    return PortalService(g.db)


def workflows() -> WorkflowService:
    return WorkflowService(g.db)


class _LazyVault:
    """Builds the real CredentialVault only when a password is encrypted or decrypted, so a missing
    or invalid key does not break listing, activating, deleting or the Executor."""

    def __init__(self, key: str | None):
        self._key = key

    def encrypt(self, plain: str) -> str:
        return CredentialVault(self._key).encrypt(plain)

    def decrypt(self, token: str) -> str:
        return CredentialVault(self._key).decrypt(token)


def credentials() -> CredentialService:
    return CredentialService(g.db, _LazyVault(current_app.extensions["settings"].vault_key))


def saved_sessions() -> SavedSessionService:
    return SavedSessionService(g.db, _LazyVault(current_app.extensions["settings"].vault_key))


def vault_ready() -> bool:
    try:
        CredentialVault(current_app.extensions["settings"].vault_key)
    except Invalid:
        return False
    return True


def runs():
    """Shared RunService (one watchdog registry per process); tests may preset app.extensions["run_service"]."""
    from portal_auto.services.runs import RunService

    ext = current_app.extensions
    if "run_service" not in ext:
        ext["run_service"] = RunService(ext["session_factory"], ext["settings"])
    return ext["run_service"]


def capture():
    """Shared CaptureService (one Capture at a time per process); tests may preset app.extensions["capture_service"]."""
    from portal_auto.runtime.chrome import CdpStateReader, RealChromeLauncher
    from portal_auto.services.capture import CaptureService

    ext = current_app.extensions
    if "capture_service" not in ext:
        settings = ext["settings"]
        ext["capture_service"] = CaptureService(
            RealChromeLauncher(settings.chrome_command),
            CdpStateReader(),
            ttl_seconds=settings.capture_ttl_seconds,
            profiles_dir=settings.chrome_profiles_dir,
        )
    return ext["capture_service"]
