import pytest
from cryptography.fernet import Fernet

from portal_auto.db.session import init_schema, make_engine, make_session_factory


@pytest.fixture(autouse=True)
def isolated_cwd(request, tmp_path, monkeypatch):
    """Settings() roots artifacts and uploads at the working directory, so no test may run in the project's own.
    e2e tests read the project's .env first and change directory themselves."""
    if request.node.get_closest_marker("e2e") is None:
        monkeypatch.chdir(tmp_path)


@pytest.fixture
def session_factory(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    init_schema(engine)
    return make_session_factory(engine)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s


@pytest.fixture
def vault_key() -> str:
    return Fernet.generate_key().decode()
