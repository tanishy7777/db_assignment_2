from __future__ import annotations
import sys
from collections import deque
from contextlib import contextmanager
import inspect
from pathlib import Path
from urllib.parse import urlencode
from anyio.from_thread import start_blocking_portal
import httpx
import mysql.connector.pooling
import pytest
from starlette.requests import Request

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


class _DummyPool:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
    def get_connection(self):
        raise RuntimeError("Database access must be overridden in tests.")
mysql.connector.pooling.MySQLConnectionPool = _DummyPool

if "app" not in inspect.signature(httpx.Client.__init__).parameters:
    _httpx_client_init = httpx.Client.__init__
    def _compat_httpx_client_init(self, *args, app=None, **kwargs):
        return _httpx_client_init(self, *args, **kwargs)
    httpx.Client.__init__ = _compat_httpx_client_init


class ASGITestClient:
    def __init__(self, app, base_url: str = "http://testserver"):
        self.app = app
        self.base_url = base_url
        self._portal_cm = None
        self._portal = None
        self._client = None
    def __enter__(self):
        self._portal_cm = start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        async def _build_client():
            transport = httpx.ASGITransport(app=self.app)
            return httpx.AsyncClient(transport=transport, base_url=self.base_url)
        self._client = self._portal.call(_build_client)
        return self
    def __exit__(self, exc_type, exc, tb):
        if self._client is not None:
            self._portal.call(self._client.aclose)
        self._portal_cm.__exit__(exc_type, exc, tb)
    def request(self, method, url, **kwargs):
        return self._portal.call(self._client.request, method, url, **kwargs)
    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)
    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def normalize_sql(query: str) -> str:
    return " ".join(str(query).split())


class ScriptedDB:
    def __init__(self, *, fetchone_values=None, fetchall_values=None, execute_side_effects=None):
        self.fetchone_values = deque(fetchone_values or [])
        self.fetchall_values = deque(fetchall_values or [])
        self.execute_side_effects = deque(execute_side_effects or [])
        self.executed: list[tuple[str, object]] = []
    def execute(self, query, params=None):
        self.executed.append((normalize_sql(query), params))
        if self.execute_side_effects:
            effect = self.execute_side_effects.popleft()
            if isinstance(effect, Exception):
                raise effect
            if callable(effect):
                effect(query, params)
    def fetchone(self):
        return self.fetchone_values.popleft() if self.fetchone_values else None
    def fetchall(self):
        return self.fetchall_values.popleft() if self.fetchall_values else []


class GuardDB(ScriptedDB):
    def __init__(self, allowed_substrings=None, **kwargs):
        super().__init__(**kwargs)
        self.allowed_substrings = None if allowed_substrings is None else tuple(allowed_substrings)
    def execute(self, query, params=None):
        normalized = normalize_sql(query)
        if self.allowed_substrings is None:
            raise AssertionError(f"Unexpected SQL executed: {normalized}")
        if not any(token in normalized for token in self.allowed_substrings):
            raise AssertionError(f"Unexpected SQL executed: {normalized}")
        super().execute(query, params)


def make_request(path: str, method: str = "GET", query_string: str = "", cookies=None, form_data=None) -> Request:
    header_items = []
    body = b""
    if cookies:
        cookie_value = "; ".join(f"{key}={value}" for key, value in cookies.items())
        header_items.append((b"cookie", cookie_value.encode()))
    if form_data is not None:
        body = urlencode(form_data, doseq=True).encode()
        header_items.append((b"content-type", b"application/x-www-form-urlencoded"))
        header_items.append((b"content-length", str(len(body)).encode()))
    sent = False
    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": query_string.encode(),
        "headers": header_items,
        "client": ("127.0.0.1", 12345),
    }
    return Request(scope, receive)


@pytest.fixture
def admin_user():
    return {"user_id": 1, "username": "admin", "role": "Admin", "member_id": 1}


@pytest.fixture
def coach_user():
    return {"user_id": 2, "username": "coach", "role": "Coach", "member_id": 2}


@pytest.fixture
def player_user():
    return {"user_id": 3, "username": "player", "role": "Player", "member_id": 3}


@pytest.fixture
def client_factory():
    from app.auth.dependencies import get_current_user
    from app.database import get_auth_db, get_track_db
    from app.main import app
    @contextmanager
    def _factory(*, user=None, track_db=None, auth_db=None):
        app.dependency_overrides.clear()
        if user is not None:
            app.dependency_overrides[get_current_user] = lambda: user
        if track_db is not None:
            app.dependency_overrides[get_track_db] = lambda: track_db
        if auth_db is not None:
            app.dependency_overrides[get_auth_db] = lambda: auth_db
        with ASGITestClient(app) as client:
            yield client
        app.dependency_overrides.clear()
    return _factory
