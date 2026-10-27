# Requirements (MVP)

Terms are defined in `/GLOSSARY.md`. Decisions with trade-offs are in `docs/adr/`.

## Goal
An operator manages Portals, Credentials and Workflows in a web UI, then runs a Workflow on the Executor screen. The first Run generates a reusable script with the agent; later Runs replay it. Every Run is logged.

## Screens (server-rendered Jinja + plain CSS, no JS framework)
| Screen | Behavior |
|---|---|
| Portals | List/create/edit: name, url, active. |
| Credentials | Per Portal: label, username, password (masked, encrypted at rest). |
| Workflows | Portal (link field), name, active, ordered Steps as a child table (add, edit, reorder, remove). Warn on save if no "verify" Step. |
| Executor | Pick Portal -> Workflow -> Credential. Show steps, the credential (password masked) and whether a Current Script Version exists. "Show browser" checkbox (default off). Execute is disabled until Verification is ticked. |
| Runs | Filterable list (portal, workflow, status) and a detail page with the Run Log, screenshot, script version, status auto-refresh while active. Failed Run offers "Regenerate script". |

## Rules
1. Portal -> many Credentials, many Workflows. Workflow -> many Steps, Script Versions, Runs.
2. Workflow starts on its Portal's URL. A Step may navigate further.
3. Steps may use `{{username}}` and `{{password}}`; scripts read `PORTAL_USERNAME` / `PORTAL_PASSWORD` (ADR 0002).
4. Execute: Current Script Version exists -> replay it; otherwise the Run is a Generation: the agent does the Steps for real in its live browser, the new Script Version is stored and is replayed from the next Run on, never in the same Run (ADR 0001).
5. Editing Steps makes all Script Versions Stale. Old versions are kept.
6. A failed replay does not call the agent automatically.
7. Run states: queued, generating, running, succeeded, failed. Succeeded = script exit code 0, or, for a Run that generated, a stored Script Version.
8. One active Run at a time; a second Execute is rejected with a clear message (ADR 0003).
9. Any Run is killed after a configurable timeout (default 10 min); agent iteration cap is configurable (default 40).
10. Records with Runs cannot be hard-deleted; set inactive. Inactive records are hidden in Executor dropdowns.
11. Run Log stores: status, trigger, credential label (never the password), script version, timestamps, captured output, failure screenshot path.
12. LLM model/url/key live in `.env` only.

## Non-functional
- Postgres in Docker Compose; Flask app on the host at `127.0.0.1:5001` (5000 is taken).
- No authentication in the MVP; localhost binding is the control. Explicitly out of scope.
- Scripts stored in Postgres; temp files deleted after use.
- Passwords never appear in logs, scripts, pages (masked) or error messages.

## Out of scope
Auth/roles, job queue, step-level timelines, vision/screenshot input to the agent, dockerizing the app, scheduling. (MFA/SSO sign-ins are handled by Saved Sessions, see below.)

## Saved Sessions (added 2026-10-25)
Terms in `/GLOSSARY.md`; decision in `docs/adr/0004`.

13. A Credential may have a username and password, a Saved Session, or both. The password is optional; a Credential with neither is listed as "Needs a password or a saved session" and cannot be chosen for a Run. One Saved Session per Credential; capturing or importing again replaces it.
14. A Workflow has **Starts signed in** (default off). On: the Run loads the Credential's Saved Session, and only Credentials with one are offered. Off: no session is loaded and username/password work as before. Changing the setting makes the Workflow's Script Versions Stale.
15. **Import**: a Saved Session can be uploaded as a Playwright session file (JSON object with `cookies` and `origins` lists, at most 2 MB); invalid files are rejected, and a file with no cookie for the Portal's host or declared extra hosts is accepted with a warning.
16. **Capture** (from the Credentials screen): the app opens real Chrome on the Portal's URL with that Credential's own persistent private Chrome profile (ADR 0004; `PORTAL_AUTO_CHROME_PROFILES_DIR=temp` for a throwaway one); nothing is typed or injected into the page; the operator signs in by hand and clicks **Save session**; the app stores the session and removes the window and profile. One Capture at a time; an open Capture is closed after 15 minutes, on cancel, and when the server stops. Debug port bound to `127.0.0.1` on a random free port.
17. A Saved Session is encrypted at rest, never rendered or logged, handed to a Run as a private temporary file that is deleted afterwards, never written into a script, and its cookie values are scrubbed from stored Run output.
18. A Run with a Saved Session uses real Chrome when installed. After a **successful** Run the refreshed session replaces the stored one; a failed Run never overwrites it.
19. The Credentials list and the Executor show the session's age ("captured 3 days ago", "refreshed 2 hours ago") or "No saved session"; no expiry prediction. A failed Run that used a session adds "This Run used a saved session; it may have expired. Capture the sign-in again."

## Inputs (added 2026-10-25)
Terms in `/GLOSSARY.md`; decision in `docs/adr/0005`.

20. In Steps, any `{{name}}` other than `username` and `password` is an **Input**. Its kind is written after a colon: `{{name}}` = text (default), `{{name:file}}` = any file, `{{name:image}}` = an image file. Names are letters, digits and underscores, start with a letter; one name must have one kind within a Workflow. Placeholders only work in fill, select and upload actions (not in URLs). Kinds live in one registry (`inputs.py`) so a new kind (number, date, choice...) is added in one place.
21. The Executor builds its form **at request time** from the chosen Workflow's current Steps, so nothing is configured separately and adding, removing or renaming a placeholder changes the form immediately. It lists one required field per Input, in order of first appearance: a text box for text, a file chooser for file, an image chooser (`accept=image/*`) for image. Text boxes are pre-filled with the values of the Workflow's last Run; file Inputs never are. Execute is rejected server-side when an Input is missing or unknown.
22. A Run records its text Input values and the names of its files (shown on the Run page), never file contents. Uploaded files live in a private per-Run folder (mode 0700, not readable by others) and are deleted when the Run ends (and swept at startup if a worker died).
23. Generation uses the real values live, but the stored script contains only placeholders; the tool layer refuses to type a literal equal to an Input value. Replays read values from the environment, so each Run can use different values. Adding, removing or renaming an Input changes the Steps, so scripts become Stale as usual.
24. File and image Inputs are uploaded through a new `upload_file` agent tool that answers the page's file chooser (the visible trigger is clicked) or sets the file on a file input directly, and is recorded as the equivalent script step. Files up to 100 MB and images up to 20 MB are accepted on the Executor; other pages keep their smaller limits. An image must really be an image: PNG, JPEG, GIF or WebP, checked by its content, not by its name.
25. Each Input is exactly one value; no optional Inputs and no lists in the MVP (use several placeholders such as `{{tag1}}`, `{{tag2}}`).
