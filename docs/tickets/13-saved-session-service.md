# 13 SavedSessionService (import, validate, encrypt, load, refresh, clear)
Accept: rejects non-JSON, wrong shape, > 2 MB; accepts a real file; warns when no cookie matches the Portal host or extra hosts; stored value is not plaintext; load round-trips; refresh replaces and stamps `session_source`.
