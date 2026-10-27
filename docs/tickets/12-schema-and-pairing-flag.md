# 12 Schema: optional password, session columns, Starts signed in
Do: nullable `password_encrypted`; `session_encrypted`, `session_updated_at`, `session_source` on Credential; `starts_signed_in` on Workflow (toggle bumps `steps_revision`). Credential list marks "Needs a password or a saved session".
Accept: services tests; creating a Credential without a password works; toggling the flag makes the current script None.
