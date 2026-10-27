# Interface contract (agents build to this exactly)

Domain words: `/GLOSSARY.md`. Rules: `docs/requirements.md`. Design: `docs/design.md` (DB is **SQLite** for now; URL via `Settings.database_url`; schema via `init_schema`; tables already in `src/portal_auto/db/models.py`; errors in `services/errors.py`).
All services take a SQLAlchemy `Session` in the constructor, commit their own writes, and raise `DomainError` subclasses (`NotFound`, `Invalid`, `Duplicate`, `InUse`, `RunAlreadyActive`) with operator-safe messages. Never return or log plaintext passwords except where stated.

## services/vault.py
```python
class CredentialVault:
    def __init__(self, key: str | None): ...   # raises Invalid("PORTAL_AUTO_VAULT_KEY is not set or invalid") if missing/invalid
    def encrypt(self, plain: str) -> str
    def decrypt(self, token: str) -> str       # raises Invalid on wrong key / corrupt token
```

## services/portals.py
```python
class PortalService:
    def __init__(self, session)
    def create(self, name: str, url: str) -> Portal      # name non-empty, url must start http:// or https://; Duplicate on name clash
    def update(self, portal_id: int, name: str, url: str) -> Portal
    def set_active(self, portal_id: int, active: bool) -> Portal
    def get(self, portal_id: int) -> Portal               # NotFound
    def list(self, active_only: bool = False) -> list[Portal]   # ordered by name
    def delete(self, portal_id: int) -> None              # InUse if it has credentials, workflows or (via them) runs
```

## services/credentials.py
```python
class CredentialService:
    def __init__(self, session, vault: CredentialVault)
    def create(self, portal_id: int, label: str, username: str, password: str) -> Credential   # Duplicate on (portal,label); password required
    def update(self, credential_id: int, label: str, username: str, password: str | None = None) -> Credential  # None/"" keeps old password
    def set_active(self, credential_id: int, active: bool) -> Credential
    def get(self, credential_id: int) -> Credential
    def list_for_portal(self, portal_id: int, active_only: bool = False) -> list[Credential]
    def delete(self, credential_id: int) -> None          # InUse if any Run used it
```
Only `password_encrypted` is stored; the model must never hold plaintext.

## services/workflows.py
```python
class WorkflowService:
    def __init__(self, session)
    def create(self, portal_id: int, name: str, steps: list[str]) -> Workflow        # Duplicate on (portal,name); at least 1 non-blank step
    def update(self, workflow_id: int, name: str) -> Workflow
    def replace_steps(self, workflow_id: int, steps: list[str]) -> bool   # blank lines dropped, positions 1..n; bumps steps_revision ONLY if the instruction list changed; returns changed
    def set_active(self, workflow_id: int, active: bool) -> Workflow
    def get(self, workflow_id: int) -> Workflow
    def list(self, portal_id: int | None = None, active_only: bool = False) -> list[Workflow]
    def current_script(self, workflow_id: int) -> ScriptVersion | None   # newest ScriptVersion whose steps_revision == workflow.steps_revision
    def has_verify_step(self, workflow_id: int) -> bool                  # any step containing "verify" (case-insensitive)
    def delete(self, workflow_id: int) -> None                           # InUse if it has script versions or runs
```

## runtime/interfaces.py (the two seams)
```python
@dataclass(frozen=True)
class ExecutionResult:
    exit_code: int
    output: str                     # combined stdout+stderr
    screenshot_path: str | None

class ScriptGenerator(Protocol):
    def generate(self, url: str, steps: list[str], headless: bool, env: dict[str, str]) -> str:  # returns script source
        ...                          # raises GenerationFailed(DomainError) when the agent cannot complete

class ScriptExecutor(Protocol):
    def execute(self, code: str, env: dict[str, str], headless: bool, timeout_seconds: int) -> ExecutionResult: ...
```
Adapters: `FakeScriptGenerator`, `FakeScriptExecutor` (tests, in `runtime/fakes.py`), `AgentScriptGenerator`, `SubprocessScriptExecutor` (real, `runtime/generator.py`, `runtime/executor.py`).
Step placeholders `{{username}}` / `{{password}}` are mapped to `{{ENV:PORTAL_USERNAME}}` / `{{ENV:PORTAL_PASSWORD}}` before generation (`runtime/placeholders.py: to_engine_steps(steps) -> list[str]`). Scripts get `PORTAL_USERNAME`, `PORTAL_PASSWORD`, `PORTAL_ARTIFACTS_DIR`, `HEADLESS` ("1"/"0") via `env`.

## worker.py
```python
def execute_run(session, run_id: int, *, generator, executor, vault, settings) -> Run
```
Lifecycle (ADR 0001): load Run; decrypt the credential password; if `run.force_generate` or `WorkflowService.current_script` is None -> status `generating`, generate (steps from the workflow), store a new `ScriptVersion` (version = max+1, `steps_revision` = workflow's) and link `run.script_version_id`; else link the current one. Status `running`, execute with `settings.run_timeout_seconds`. Store `output` with the password replaced by `***`, `screenshot_path`, `error` (short reason on failure), `finished_at`; status `succeeded` iff exit_code == 0. A failed replay never calls the generator. `GenerationFailed` -> status `failed`, error set. On every terminal state set `active_slot = None`. Also: `main()` entry (`python -m portal_auto.worker <run_id>`) wiring real adapters from `Settings.from_env()`.

## services/runs.py
```python
class RunService:
    def __init__(self, session_factory, settings, launcher: Callable[[int], int] | None = None)  # launcher(run_id) -> pid; default spawns `python -m portal_auto.worker <id>`
    def start_run(self, workflow_id: int, credential_id: int, show_browser: bool = False, force_generate: bool = False) -> Run
        # Invalid if portal/workflow/credential inactive or credential not of workflow's portal; RunAlreadyActive if another Run is active
        # (enforced by unique run.active_slot=1 -> catch IntegrityError); stores credential_label; sets pid; starts a watchdog that, after
        # settings.run_timeout_seconds, kills the pid and marks the Run failed ("timed out") if still active
    def get_run(self, run_id: int) -> Run
    def list_runs(self, portal_id: int | None = None, workflow_id: int | None = None, status: str | None = None, limit: int = 100) -> list[Run]  # newest first
    def active_run(self) -> Run | None
    def recover_stale_runs(self) -> int      # active Runs whose pid is dead -> failed ("interrupted"); returns count
```
