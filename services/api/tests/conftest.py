from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


@pytest.fixture(autouse=True)
def _reset_rpc_provider_state():
    """Clear the process-local Base RPC provider backoff/health between tests.

    The 429 backoff state is module-level (shared by the worker, coverage probe,
    probe_rpc_health, and /system-health), so a test that arms it must not bleed
    into the next. Reset before and after every test.
    """
    def _reset():
        try:
            from services.api.app.evm_activity_provider import reset_rpc_provider_state
            reset_rpc_provider_state()
        except Exception:
            pass
        try:
            # QuickNode webhook log-sampler state is module-level so its once-per-window
            # rate limiting works across requests; clear it between tests so a sampled
            # line from a prior test never suppresses another test's assertion.
            from services.api.app.quicknode_streams import reset_quicknode_log_sampler_state
            reset_quicknode_log_sampler_state()
        except Exception:
            pass
        try:
            # The bounded chain-head cache is module-level (reused across webhook
            # batches on purpose), so a head cached by one test must not bleed into a
            # test that asserts on a different rpc_head. Reset between tests.
            from services.api.app.quicknode_streams import reset_chain_head_cache
            reset_chain_head_cache()
        except Exception:
            pass
    _reset()
    yield
    _reset()


# ---------------------------------------------------------------------------
# Lightweight stubs for packages that are not installed in the test runner.
# Add new entries here rather than installing the full packages.
# ---------------------------------------------------------------------------

def _make_stub(name: str, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# -- fastapi ------------------------------------------------------------------
if 'fastapi' not in sys.modules:
    try:
        import fastapi as _fa  # use real fastapi when installed
        # Register sub-modules so they are importable without re-importing
        import fastapi.testclient  # noqa: F401
        import fastapi.responses   # noqa: F401
        import fastapi.middleware  # noqa: F401
        import fastapi.middleware.cors  # noqa: F401
    except ImportError:
        class _HTTPException(Exception):
            def __init__(self, status_code: int = 400, detail: str = ''):
                self.status_code = status_code
                self.detail = detail
                super().__init__(detail)

        class _Request:
            def __init__(self, headers=None):
                self.headers = headers or {}
                # A real Starlette/FastAPI Request exposes query_params (an empty
                # multidict when no query string is present). The stub previously
                # omitted it, so any handler that reads request.query_params raised
                # AttributeError under the offline stub. Provide an empty mapping so
                # the stub matches real-Request behavior for the no-query-string case.
                self.query_params = {}

        class _Status:
            HTTP_200_OK = 200
            HTTP_201_CREATED = 201
            HTTP_400_BAD_REQUEST = 400
            HTTP_401_UNAUTHORIZED = 401
            HTTP_402_PAYMENT_REQUIRED = 402
            HTTP_403_FORBIDDEN = 403
            HTTP_404_NOT_FOUND = 404
            HTTP_409_CONFLICT = 409
            HTTP_422_UNPROCESSABLE_ENTITY = 422
            HTTP_500_INTERNAL_SERVER_ERROR = 500
            HTTP_502_BAD_GATEWAY = 502
            HTTP_503_SERVICE_UNAVAILABLE = 503

        _fastapi = _make_stub(
            'fastapi',
            HTTPException=_HTTPException,
            Request=_Request,
            status=_Status(),
            FastAPI=type('FastAPI', (), {'__init__': lambda self, **kw: None}),
            APIRouter=type('APIRouter', (), {'__init__': lambda self, **kw: None}),
        )
        _resp_names = ('JSONResponse', 'RedirectResponse', 'Response', 'StreamingResponse')
        _responses = _make_stub('fastapi.responses', **{c: type(c, (), {}) for c in _resp_names})
        _fastapi.responses = _responses
        _mw = _make_stub('fastapi.middleware')
        _cors = _make_stub('fastapi.middleware.cors', CORSMiddleware=type('CORSMiddleware', (), {}))
        _fastapi.middleware = _mw
        _mw.cors = _cors
        _make_stub('fastapi.testclient', TestClient=type('TestClient', (), {'__init__': lambda *a, **kw: None}))


# -- psycopg ------------------------------------------------------------------
if 'psycopg' not in sys.modules:
    # Mirror real psycopg's hierarchy: every concrete error subclasses psycopg.Error
    # so `except psycopg.Error` catches them (and `except psycopg.errors.SyntaxError`
    # still matches the specific class).
    _PgError = type('Error', (Exception,), {})
    _pg_errors = _make_stub(
        'psycopg.errors',
        UniqueViolation=type('UniqueViolation', (_PgError,), {}),
        OperationalError=type('OperationalError', (_PgError,), {}),
        DeadlockDetected=type('DeadlockDetected', (_PgError,), {}),
        ForeignKeyViolation=type('ForeignKeyViolation', (_PgError,), {}),
        IntegrityError=type('IntegrityError', (_PgError,), {}),
        UndefinedTable=type('UndefinedTable', (_PgError,), {}),
        UndefinedColumn=type('UndefinedColumn', (_PgError,), {}),
        # Names referenced by the app / tests; without these the stub raises
        # ImportError on `from psycopg.errors import SyntaxError` collection.
        SyntaxError=type('SyntaxError', (_PgError,), {}),
        CheckViolation=type('CheckViolation', (_PgError,), {}),
        InvalidColumnReference=type('InvalidColumnReference', (_PgError,), {}),
        # Transaction/permission/timeout classes used by runtime-status optional-query
        # classification. InFailedSqlTransaction must be distinguishable so an aborted
        # transaction is never mislabeled as an unavailable optional table.
        InFailedSqlTransaction=type('InFailedSqlTransaction', (_PgError,), {}),
        InsufficientPrivilege=type('InsufficientPrivilege', (_PgError,), {}),
        QueryCanceled=type('QueryCanceled', (_PgError,), {}),
        NotNullViolation=type('NotNullViolation', (_PgError,), {}),
    )
    _pg = _make_stub(
        'psycopg',
        connect=lambda *a, **kw: None,
        errors=_pg_errors,
        OperationalError=_pg_errors.OperationalError,
        # Base exception class used by `except psycopg.Error` handlers.
        Error=_PgError,
    )
    _pg.errors = _pg_errors


# -- redis --------------------------------------------------------------------
if 'redis' not in sys.modules:
    _redis_asyncio = _make_stub('redis.asyncio')
    _redis = _make_stub('redis', asyncio=_redis_asyncio)
