import os
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

os.environ.setdefault("APP_SECRET_KEY", "test-secret-key-for-tests")
os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key-for-tests")
os.environ["AI_API_KEY"] = ""
os.environ["GITHUB_TOKEN"] = ""

import pymysql
import pytest
from sqlalchemy.engine import make_url

from app import create_app
from core.database import db
from core.security import hash_password
from app.models import User

DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


def _test_database_url() -> str:
    explicit = os.getenv("TEST_DATABASE_URL")
    if explicit:
        url = make_url(explicit)
    else:
        base = os.getenv("DATABASE_URL")
        if not base:
            raise RuntimeError(
                "DATABASE_URL or TEST_DATABASE_URL must be set. SQLite is not supported by this project."
            )
        url = make_url(base).set(database=f"{make_url(base).database}_test")
    if not url.drivername.startswith("mysql"):
        raise RuntimeError("Tests require MySQL; SQLite is not supported by this project.")
    if not url.database or not DATABASE_NAME_PATTERN.match(url.database):
        raise RuntimeError("Invalid test database name")
    return url.render_as_string(hide_password=False)


TEST_DATABASE_URL = _test_database_url()


def _ensure_database(url_string: str) -> None:
    url = make_url(url_string)
    connection = pymysql.connect(
        host=url.host or "127.0.0.1",
        port=url.port or 3306,
        user=url.username or "root",
        password=url.password or "",
        autocommit=True,
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{url.database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
    finally:
        connection.close()


_ensure_database(TEST_DATABASE_URL)


def _clean_tables() -> None:
    db.session.rollback()
    for table in reversed(db.metadata.sorted_tables):
        db.session.execute(table.delete())
    db.session.commit()


@pytest.fixture(scope="session")
def flask_app():
    application = create_app(
        overrides={
            "TESTING": True,
            "SECRET_KEY": "test-secret-key-for-tests",
            "SQLALCHEMY_DATABASE_URI": TEST_DATABASE_URL,
            "SQLALCHEMY_ENGINE_OPTIONS": {"pool_pre_ping": True, "pool_recycle": 280},
            "WTF_CSRF_ENABLED": False,
            "SESSION_COOKIE_SECURE": False,
        }
    )
    with application.app_context():
        db.drop_all()
        db.create_all()
    yield application
    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture
def app(flask_app):
    with flask_app.app_context():
        _clean_tables()
        yield flask_app
        db.session.remove()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin(app):
    user = User(username="admin", password_hash=hash_password("correct-horse-battery"), is_admin=True)
    db.session.add(user)
    db.session.commit()
    return user


@pytest.fixture
def logged_in_client(client, admin):
    response = client.post(
        "/login",
        data={"username": "admin", "password": "correct-horse-battery"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    return client
