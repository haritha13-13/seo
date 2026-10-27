# Credentials are encrypted at rest and reach scripts only as environment variables

Passwords are stored encrypted (key held in the environment, never in Postgres) and passed to the script process as `PORTAL_USERNAME` / `PORTAL_PASSWORD` when a Run starts. Generated scripts therefore contain no secrets, so a Script Version can be stored, logged and shared safely, and one script works with any Credential of its Portal. The trade-off is that losing the encryption key makes stored passwords unrecoverable.
