# Runs execute in a subprocess, one at a time, without a job queue

A Run is started as a separate OS process by the web app, with its status written to Postgres, and starting a Run while another is active is rejected. We deliberately skipped Celery/Redis for the MVP: Playwright and the agent are heavy, a headed browser needs the operator's display, and one-at-a-time avoids resource fights. Moving to a real queue later means replacing the process launcher behind a single interface.
