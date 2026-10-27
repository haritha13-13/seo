"""Runtime settings, read once from the environment (.env is loaded by the CLI)."""
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    llm_model: str = "ollama/gpt-oss:120b"
    llm_base_url: str | None = "https://ollama.com"
    llm_api_key: str | None = None
    database_url: str = "sqlite:///portal_auto.db"
    vault_key: str | None = None
    run_timeout_seconds: int = 600
    agent_max_iterations: int = 40
    artifacts_dir_name: str = "artifacts"
    chrome_command: str = "google-chrome"
    chrome_profiles_dir: Path | None = field(default_factory=lambda: Path.home() / ".portal-auto" / "chrome-profiles")
    capture_ttl_seconds: int = 900
    root: Path = field(default_factory=Path.cwd)

    @property
    def artifacts_dir(self) -> Path:
        return self.root / self.artifacts_dir_name

    @property
    def uploads_dir(self) -> Path:
        return self.root / "uploads"

    @property
    def scripts_dir(self) -> Path:
        return self.root / "generated" / "scripts"

    @property
    def actions_dir(self) -> Path:
        return self.root / "generated" / "actions"

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            llm_model=os.environ.get("PORTAL_AUTO_LLM_MODEL", defaults.llm_model),
            llm_base_url=os.environ.get("PORTAL_AUTO_LLM_BASE_URL", defaults.llm_base_url) or None,
            llm_api_key=os.environ.get("PORTAL_AUTO_LLM_API_KEY") or None,
            database_url=os.environ.get("PORTAL_AUTO_DATABASE_URL", defaults.database_url),
            vault_key=os.environ.get("PORTAL_AUTO_VAULT_KEY") or None,
            run_timeout_seconds=int(os.environ.get("PORTAL_AUTO_RUN_TIMEOUT_SECONDS", defaults.run_timeout_seconds)),
            agent_max_iterations=int(os.environ.get("PORTAL_AUTO_AGENT_MAX_ITERATIONS", defaults.agent_max_iterations)),
            chrome_command=os.environ.get("PORTAL_AUTO_CHROME_COMMAND") or defaults.chrome_command,
            chrome_profiles_dir=_profiles_dir(os.environ.get("PORTAL_AUTO_CHROME_PROFILES_DIR"), defaults.chrome_profiles_dir),
            capture_ttl_seconds=int(os.environ.get("PORTAL_AUTO_CAPTURE_TTL_SECONDS", defaults.capture_ttl_seconds)),
        )


def _profiles_dir(setting: str | None, default: Path | None) -> Path | None:
    """Unset keeps the default folder of per-Credential profiles; 'temp' means a throwaway profile every capture."""
    if not setting:
        return default
    return None if setting.strip().lower() == "temp" else Path(setting).expanduser()
