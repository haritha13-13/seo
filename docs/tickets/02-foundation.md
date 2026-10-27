# 02 Foundation (tracer: app boots, DB connects)
Do: deps (flask, sqlalchemy>=2, psycopg[binary], alembic, cryptography, flask-wtf or own CSRF); `docker-compose.yml` (postgres:16, port 5432, volume); config additions; app factory + `/health` (checks DB); `base.html` + `app.css`; Alembic baseline; `.env.example` entries; `make`-style run notes in README.
Accept: `docker compose up -d`, `alembic upgrade head`, `flask run -p 5001` serves `/health` 200; test with Flask client.
