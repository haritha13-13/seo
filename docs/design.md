# Design (MVP)

Vocabulary: `/GLOSSARY.md` (domain) and the codebase-design skill (module, interface, seam, adapter, depth).

## Layers
```
web/       Flask blueprints + Jinja + CSS. Thin: parse form -> call a service -> render.
services/  Deep modules holding all rules. Take a SQLAlchemy Session; return results.
db/        SQLAlchemy 2 models + session factory. Alembic migrations in /migrations.
engine/    (existing portal_auto: browser/, agent/, codegen/, recording, literals) generation + replay machinery.
worker.py  Process entry point: `python -m portal_auto.worker <run_id>`.
```
Web never touches the engine; services never render HTML; the engine never imports Flask or SQLAlchemy.

## Deep modules and their interfaces
| Module | Interface (small) | Hides |
|---|---|---|
| `RunService` | `start_run(workflow_id, credential_id, show_browser, force_generate) -> Run`; `get_run(id)`; `list_runs(filters)`; `recover_stale_runs()` | single-flight rule, state machine, subprocess launch, timeout watchdog, redaction |
| `RunWorker` (in `worker.py`) | `execute_run(run_id, generator, executor)` | choose generate vs replay, script versioning, status transitions, output capture |
| `WorkflowService` | `create/update/get`, `replace_steps(workflow_id, steps)`, `current_script(workflow_id)` | step ordering, `steps_revision` bump, derived staleness, verify-step warning |
| `CredentialVault` | `encrypt(plain) -> str`, `decrypt(token) -> str` | Fernet, key handling, key-missing errors |
| `PortalService` / `CredentialService` | CRUD + `set_active` + guarded `delete` | "no hard delete when runs exist" |

**Seams (each has two adapters, so they are real):**
- `ScriptGenerator.generate(url, steps, headless, env, allowed_hosts) -> GenerationResult(code, final_page_url)` : `AgentScriptGenerator` (crewai, existing code) / `FakeScriptGenerator` (tests). `final_page_url` is the http(s) address the agent's browser ended on, or None.
- `ScriptExecutor.execute(code, env, headless, timeout) -> ExecutionResult(exit_code, output, screenshot_path)` : `SubprocessScriptExecutor` / `FakeScriptExecutor`.

No repository layer: services use the Session directly (a repo would be pass-through, fails the deletion test).

## Data model (Postgres)
```
portal(id, name UNIQUE, url, active)
credential(id, portal_id FK, label, username, password_encrypted, active, UNIQUE(portal_id,label))
workflow(id, portal_id FK, name, active, steps_revision INT DEFAULT 1, UNIQUE(portal_id,name))
workflow_step(id, workflow_id FK, position INT, instruction TEXT, UNIQUE(workflow_id,position))
script_version(id, workflow_id FK, version INT, steps_revision INT, code TEXT, created_at, UNIQUE(workflow_id,version))
run(id, workflow_id FK, credential_id FK, credential_label, script_version_id FK NULL, status, show_browser,
    trigger, output TEXT, error TEXT, screenshot_path, pid, queued_at, started_at, finished_at)
```
- **Staleness is derived**, not stored: a Script Version is current iff `script_version.steps_revision == workflow.steps_revision` (latest such). Editing Steps bumps `steps_revision`; nothing else to update.
- **Single-flight enforced by the database**: partial unique index `ON run ((1)) WHERE status IN ('queued','generating','running')`. `start_run` catches the violation and raises `RunAlreadyActive`.
- Passwords: only `password_encrypted` exists; no plaintext column.

## Run lifecycle
```
queued -> generating ------------> succeeded | failed   (live Generation: the agent's work is the Run)
queued -> running (replay) ------> succeeded | failed
```
1. `start_run`: validate active records, insert Run(queued), `Popen(python -m portal_auto.worker <id>)`, store pid, start watchdog thread (kills + marks failed after timeout).
2. Worker: load Run -> if no current Script Version or `force_generate`: status `generating`, `generator.generate(...)` (the agent does the Steps for real), refuse a script holding a credential or upload path, store the new Script Version -> `succeeded` with output "Generated live: ... The stored script (vN) is used from the next Run." plus `Final page: <url>` when known (redacted); the script is NOT executed, so no session refresh (ADR 0001, amended). Otherwise status `running`, `executor.execute(...)` -> store output (password redacted), screenshot, exit code -> `succeeded|failed`.
3. App start: `recover_stale_runs()` marks active Runs whose pid is dead as `failed` ("interrupted").

Step placeholders: Steps use `{{username}}`/`{{password}}`; before Generation they are mapped to `{{ENV:PORTAL_USERNAME}}`/`{{ENV:PORTAL_PASSWORD}}`, which the existing engine already turns into `os.environ[...]` in scripts.

## Folder layout (target)
```
docker-compose.yml        postgres only
migrations/               alembic
src/portal_auto/
  config.py               + database_url, vault_key, timeouts
  db/        models.py session.py
  services/  portals.py credentials.py workflows.py runs.py vault.py errors.py
  runtime/   generator.py executor.py   # the two seams + adapters
  worker.py
  web/       __init__.py (app factory) portals.py credentials.py workflows.py executor.py runs.py
             templates/ (base.html + one folder per screen)  static/app.css
  browser/ agent/ codegen/ recording.py literals.py cli.py   # existing engine
tests/       unit (services w/ fakes, vault) + web (Flask test client) + e2e marker
```

## Quality gates
- Tests first for each service (TDD), through the interface; services tested against real Postgres (docker) and fakes at the two seams.
- Web tests via Flask test client; one e2e test against the demo portal (marked, opt-in).
- CSRF token on every POST form; output escaped by Jinja; all SQL via SQLAlchemy.
- Config validated at startup; missing vault key fails fast with a clear message.
- Review gate: `code-review` skill over each ticket's diff before it is considered done.

## As built (deviations from the design above)
- **Database:** SQLite by default (`PORTAL_AUTO_DATABASE_URL`); Postgres is a URL change. Single-flight uses a unique nullable `run.active_slot` (1 while active) instead of a partial index, because it is portable across both. Schema comes from `create_all`; **there are no migrations yet**, so a schema change means recreating the database (known limit).
- **Run** also records `generated` (this Run produced its Script Version) so screens never infer it. `RunService` returns detached Runs with workflow, portal and script version eagerly loaded.
- **Hardening added after review:** script process runs with an allow-listed environment; timeout kills the whole process tree (SIGTERM then SIGKILL on the group) and the worker enforces its own deadline; every worker exit path frees the slot; secrets are scrubbed by one `redaction.redact` (raw, JSON, repr and URL-encoded forms) and a script containing a credential value is rejected; during Generation the browser may only visit the Portal's origin and password placeholders may only be typed into password inputs; page snapshots never include typed values; Host-header allow-list, SameSite cookies, `.png`-only screenshot route.
- **Open items:** Alembic migrations; `Execution*` names (`ExecutionResult`, `execute_run`, `ScriptExecutor`) still conflict with the glossary's "Run"/"Executor"; `portal_auto.workflow.Workflow` (YAML spec) shares a name with the ORM `Workflow`; the "password placeholder" check keys on the substring `PASS` in the placeholder name.

## Saved Sessions (design)
- **Data:** `credential.password_encrypted` becomes nullable; add `credential.session_encrypted` (Text, nullable), `credential.session_updated_at`, `credential.session_source` ("imported" | "captured" | "refreshed"); `workflow.starts_signed_in` (bool). No migrations exist, so the dev database is recreated (known limit).
- **Deep modules (small interfaces):**
  - `SavedSessionService(session, vault)`: `import_session(credential_id, raw: bytes) -> ImportResult(warnings)`, `clear(credential_id)`, `load(credential_id) -> str | None`, `refresh(credential_id, state_json)`. Hides validation, size cap, host-match warning, encryption.
  - `CaptureService(settings, chrome_launcher, clock)`: `start(credential_id) -> CaptureInfo`, `finish(capture_id) -> state_json`, `cancel(capture_id)`, `active() -> CaptureInfo | None`. Hides profile choice (persistent by default, throwaway on request), 15-minute expiry, cleanup. Seam: `ChromeLauncher` (real: spawns `google-chrome` with `--remote-debugging-port`; fake for tests) and the CDP reader.
  - Browser start: one function `open_context(playwright, headless, storage_state_path | None)` used by both the agent's `BrowserSession` and the generated script template, choosing `channel="chrome"` when a session is supplied and Chrome exists.
- **Run flow:** `RunService.start_run` validates the pairing rule (requirement 14). The worker decrypts the session to a 0600 temp file, adds `PORTAL_STORAGE_STATE` (and `PORTAL_STORAGE_STATE_OUT`) to the script/Generation environment, and on success reads the OUT file back into `SavedSessionService.refresh`; temp files are removed in a `finally`. Scripts reference only the env var names.
- **Screens:** Credentials: optional password, upload form, session status, Capture / Save session / Cancel buttons. Workflows: Starts signed in checkbox. Executor: pairing filter and session age. Run detail: expired-session hint.

## Inputs (design)
- **Pure core `portal_auto/inputs.py`:** a registry of `InputKind`s (`text`, `file`, `image`; each knows if it is an upload, its HTML `accept`, its size limit and its validation), `parse_inputs(steps) -> list[InputSpec(name, kind)]` (first appearance order; Invalid on bad names, unknown kinds or one name with two kinds), `env_name(name) -> "PORTAL_INPUT_<UPPER>"`, and `UploadedFile(filename, content)`. Used by services, runtime and web; no dependencies. Adding a kind means adding one registry entry.
- **Run model:** `run.inputs` (JSON text) holds `{name: {"kind": "text", "value": ...} | {"kind": "file", "filename": ..., "path": ...}}`; no schema other than this one column.
- **`RunService.start_run(..., inputs: Mapping[str, str] | None, files: Mapping[str, UploadedFile] | None)`:** validates names against `parse_inputs(workflow steps)`; creates the Run; writes each file into a private per-Run folder `<root>/uploads/run-<id>` (0700, original basename kept) BEFORE launching the worker; stores the JSON.
- **Worker:** adds `PORTAL_INPUT_<NAME>` (text value or file path) to the env of Generation and the script; `to_engine_steps` maps `{{name}}`/`{{name:file}}` to `{{ENV:PORTAL_INPUT_NAME}}`; render allow-list extends to those names; the per-Run uploads folder is removed in a `finally` and swept at startup.
- **Engine:** new `UploadFile` action (`with page.expect_file_chooser() as chooser: <trigger>.click(); chooser.value.set_files(os.environ[...])`, or `set_input_files` for a file input) and an `upload_file` tool; the snapshot also lists `input[type=file]` even when hidden; fill/select/upload refuse a literal equal to an Input value.
- **Screens:** Workflows (shows the detected Inputs under the Steps), Executor (Inputs form, prefill from the last Run, multipart, 100 MB limit on that route), Run detail (Inputs used).
