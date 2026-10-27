# Portal Automation

Describe a workflow on a web portal once, in plain steps. An LLM agent carries it out in a real browser the first time and records a reusable Playwright script. Every later run replays that script: no LLM, no tokens, deterministic. Every run is logged.

Domain language: [GLOSSARY.md](GLOSSARY.md) · Requirements: [docs/requirements.md](docs/requirements.md) · Design: [docs/design.md](docs/design.md) · Decisions: [docs/adr](docs/adr)

## Setup

```bash
python3 -m venv venv
./venv/bin/pip install -e '.[dev]'
./venv/bin/playwright install chromium
cp .env.example .env        # then fill in the values below
```

In `.env`:

| Variable | Purpose |
|---|---|
| `PORTAL_AUTO_VAULT_KEY` | Fernet key that encrypts stored portal passwords. Generate once with `./venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` and **keep it**; if it is lost, stored passwords cannot be recovered. |
| `PORTAL_AUTO_LLM_MODEL`, `PORTAL_AUTO_LLM_BASE_URL`, `PORTAL_AUTO_LLM_API_KEY` | The model the agent uses for Generation (default: Ollama cloud `gpt-oss:120b`). |
| `PORTAL_AUTO_DATABASE_URL` | Default `sqlite:///portal_auto.db`. Any SQLAlchemy URL works. |
| `PORTAL_AUTO_RUN_TIMEOUT_SECONDS`, `PORTAL_AUTO_AGENT_MAX_ITERATIONS` | Limits (600 s, 40 iterations). |

## Use

```bash
./venv/bin/portal-auto serve        # http://127.0.0.1:5001 (localhost only, no login)
```

1. **Portals**: add the portal's name and start URL.
2. **Credentials**: add a labeled username/password for that portal (stored encrypted, always shown masked).
3. **Workflows**: pick the portal, then add ordered Steps in plain language. Use `{{username}}` and `{{password}}` for the chosen Credential. Include a "Verify ..." Step so success means something.
4. **Executor**: choose Portal, Workflow and Credential, tick *I have verified these details* and Execute. The first Run generates the script (agent), later Runs replay it. Tick *Show browser* to watch.
5. **Runs**: status, Run Log, failure screenshot. A failed Run offers *Regenerate script*; editing a Workflow's Steps makes its scripts Stale automatically.

The first Run does the real work with the agent: it carries out the Steps in a live browser (so a "Publish" Step really publishes) and stores the recorded script without running it again. Later Runs replay the stored script. *Regenerate script*, or editing the Steps, therefore repeats the Workflow's real effects once more through the agent.

## Sign-ins a script can't do (Saved Sessions)

For Google/SSO sign-in, emailed codes or CAPTCHA, sign in by hand once and keep the session against a Credential:

1. **Credentials**: add a Credential (the password is optional) and open its edit page.
2. Either **Capture sign-in** (the app opens your real Chrome on the portal; sign in, then click **Save session**) or **import** a Playwright session file, for example one made with `save-auth.js`.
3. **Workflows**: tick **Starts signed in** and write Steps that assume you are already signed in (no "click Sign in" steps).
4. **Executor**: only Credentials that have a saved session are offered for such a Workflow; ordinary Workflows keep using username and password.

A saved session is as powerful as a password: it is stored encrypted with the vault key, never shown or logged, handed to a Run as a private temporary file that is deleted afterwards, and refreshed after each successful Run. If a Run fails and used a session, capture the sign-in again. Runs that use a session need Google Chrome installed (set `PORTAL_AUTO_CHROME_COMMAND` if it is not `google-chrome`). Capture opens Chrome on the Portal's page with that Credential's own private profile (`~/.portal-auto/chrome-profiles/credential-<id>`), so Credentials never share a sign-in and capturing again finds the Credential still signed in. Set `PORTAL_AUTO_CHROME_PROFILES_DIR` to move the folder, or to `temp` for a throwaway profile each time.

## Inputs (values supplied at each Run)

Any placeholder in a Step other than `{{username}}`/`{{password}}` is an **Input**:

| Placeholder | Executor shows | Limit |
|---|---|---|
| `{{name}}` | a text box (pre-filled with the Workflow's last Run's value) | 5000 characters |
| `{{name:file}}` | a file chooser | 100 MB |
| `{{name:image}}` | an image chooser; the content must really be PNG, JPEG, GIF or WebP | 20 MB |

Names use letters, digits and underscores, start with a letter, ignore case, and keep one kind per Workflow. The Workflow editor lists the Inputs its Steps ask for. The Executor builds the form from the current Steps each time it is loaded, so adding or renaming a placeholder needs no other setup; every Input is required and Execute is rejected (listing every problem) when one is missing or invalid. Placeholders work in fill, select and upload Steps, e.g. `Click the Choose file button and upload {{presentation:file}}`.

The generated script never contains the values: it keeps the placeholders and reads them from the environment on replay, so each Run can use different values with the same Script Version. A Run page shows the text values and the original file names (never file contents or where they are stored). Uploaded files live in a private per-Run folder that is deleted when the Run ends. *Regenerate script* on a Workflow with Inputs sends you back to the Executor to choose the files again.

## Tests

```bash
./venv/bin/pytest                    # fast suite (no network, no LLM)
./venv/bin/pytest -m e2e             # real browser + LLM: the demo portal, saved-session and Inputs flows
./venv/bin/pytest -m chrome          # real Chrome capture test
```

## Layout

```
src/portal_auto/
  db/         models and session          services/   all rules (portals, credentials, workflows, runs, vault)
  runtime/    generator + executor seams  web/        Flask blueprints, Jinja templates, CSS
  worker.py   one Run per process         browser/ agent/ codegen/   the automation engine
```

## Known limits (MVP)

No authentication (bound to 127.0.0.1), one Run at a time, no MFA/CAPTCHA handling, SQLite by default, and no database migrations yet: after a schema change the old file cannot be opened by the new code, so back up `portal_auto.db` and copy its rows into a fresh database (a proper `upgrade-db` command is the next step). See `docs/requirements.md` for the full out-of-scope list.
