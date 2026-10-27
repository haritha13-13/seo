# 16 Runs use the Saved Session
Do: `open_context` shared by BrowserSession and the script template; worker temp file (0600) + env vars; real Chrome channel with fallback; refresh on success only; cookie values redacted from output; temp files always removed.
Accept (fakes + one real browser test against a local server that sets a cookie): script starts signed in; failed Run does not overwrite the session; no cookie value in stored output or in the script.
