# 07 RunService: single-flight, launch, timeout, recovery
Do: partial unique index, `start_run` (validations, `RunAlreadyActive`), subprocess launcher behind small interface, watchdog timeout, `recover_stale_runs()` at startup.
Accept: concurrent start rejected by DB; timeout kills and marks failed; dead-pid Run recovered; inactive portal/workflow/credential rejected.
