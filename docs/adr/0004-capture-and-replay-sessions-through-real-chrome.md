# Sessions are captured and replayed through real Chrome

Some sign-ins cannot be scripted (Google or SSO, emailed codes, CAPTCHA). The operator signs in once by hand in a real Chrome window the app starts with a local debugging port, and the app keeps the resulting session as the Credential's Saved Session. Runs that load a Saved Session use real Chrome (`channel="chrome"`, bundled Chromium only as a fallback), because providers such as Google trust a normal Chrome more than an automation build.

Saved Sessions follow ADR 0002: encrypted at rest, handed to a Run only as a temporary file path in the environment, never written into a script. The cost is a hard dependency on an installed Chrome for these Runs, and a debugging port open on `127.0.0.1` for the duration of a Capture.

## Profile (revised 2026-10-25)
Capture first used a throwaway profile, deleted afterwards, so that the only stored copy of a session was the encrypted one. It now keeps **one private, persistent Chrome profile per Credential** (default `~/.portal-auto/chrome-profiles/credential-<id>`, mode 0700, never deleted by the app; `PORTAL_AUTO_CHROME_PROFILES_DIR=temp` restores throwaway profiles). Reasons: Credentials never share a browser's sign-ins, and capturing a Credential again finds it still signed in. Capture only opens the Portal's page; nothing is typed or injected.

Root cause of the early Google rejections ("Couldn't sign you in: this browser or app may not be secure"), found 2026-10-25 by comparing a page's view of the app's Chrome with the one started by the operator's working `save-auth.js`: Chrome started with `--remote-debugging-port=0` sets `navigator.webdriver` to **true**, and Google refuses the sign-in; any explicit port leaves it false. The profile was never the cause. The launcher therefore passes an explicit free loopback port, and waits for Chrome itself to answer on it (Chrome writes no `DevToolsActivePort` file for an explicit port). A real-Chrome test asserts `navigator.webdriver` is false.

Consequence: signed-in browser state also lives in the profile folders, protected only by the operator's own user account, in addition to the encrypted Saved Session.

## Considered options
- **Automate the sign-in with the agent**: rejected, since the sign-in is exactly what bot detection and codes block.
- **A throwaway profile per Capture**: tried first and rejected by Google (see above); still available through the setting.
