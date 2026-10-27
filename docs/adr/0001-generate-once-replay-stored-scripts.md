# Generate a script once, then replay it instead of re-running the agent

The agent explores a Workflow only on the first Run (or after the Workflow goes Stale) and the resulting Script Version is stored in Postgres and replayed on every later Run. We chose this over invoking the agent on every Run because replays are free, fast and deterministic, at the cost that a portal redesign makes the script fail until the operator regenerates it.

A failed replay never silently falls back to the agent; it fails loudly and the operator chooses to regenerate.

**Amended 2026-10-26: the Run that generates IS the real Run.** The agent carries out the Steps for real in its live browser, so that Run has already done the work (signed in, uploaded, published). It is finished as succeeded once the Script Version is stored, and the new script is not executed in the same Run: generating and then replaying would repeat every side effect (a presentation was published twice). Consequences: a new script is first proven by the next Run, not by the Run that produced it, so a recording that cannot replay only shows up then; and regenerating (Stale Steps or *Regenerate script*) repeats the Workflow's real side effects once more, through the agent.
