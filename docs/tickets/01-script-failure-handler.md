# 01 Fix generated-script failure handler
Problem: if the browser/page is gone, `page.screenshot()` in the error handler raises `TargetClosedError` and hides the real error.
Do: in `script.py.j2` print the original error first, take the screenshot best-effort inside try/except, read the artifacts dir from `PORTAL_ARTIFACTS_DIR` (fallback to current default).
Accept: renderer test proves the template contains the guarded screenshot and env var; script still valid Python; demo script still passes.
