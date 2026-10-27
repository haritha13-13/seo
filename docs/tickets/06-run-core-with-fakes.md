# 06 Run core with fakes (no browser, no LLM)
Do: `script_version`, `run` tables; `ScriptGenerator`/`ScriptExecutor` interfaces + Fake adapters; `execute_run` in worker.py implementing the lifecycle (generate-if-needed, replay, status, redaction, versioning, force_generate).
Accept: tests for: first run generates then runs; second run replays only; stale after step edit regenerates; failed replay does NOT call generator; password redacted from output.
