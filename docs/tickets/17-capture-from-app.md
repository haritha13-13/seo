# 17 Capture sign-in from the app
Do: `CaptureService` (+ `ChromeLauncher` seam), routes and buttons: Capture / Save session / Cancel; one at a time; 15-minute expiry; cleanup on cancel, expiry and shutdown; debug port on 127.0.0.1, random free port, private temp profile.
Accept: with a fake launcher: lifecycle, single-flight, expiry, cleanup; with one opt-in real Chrome test (marker `chrome`).
