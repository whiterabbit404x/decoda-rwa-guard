from __future__ import annotations

import sys
import types
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))


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
    _pg_errors = _make_stub(
        'psycopg.errors',
        UniqueViolation=type('UniqueViolation', (Exception,), {}),
        OperationalError=type('OperationalError', (Exception,), {}),
        DeadlockDetected=type('DeadlockDetected', (Exception,), {}),
        ForeignKeyViolation=type('ForeignKeyViolation', (Exception,), {}),
        IntegrityError=type('IntegrityError', (Exception,), {}),
        UndefinedTable=type('UndefinedTable', (Exception,), {}),
        UndefinedColumn=type('UndefinedColumn', (Exception,), {}),
    )
    _pg = _make_stub(
        'psycopg',
        connect=lambda *a, **kw: None,
        errors=_pg_errors,
        OperationalError=type('OperationalError', (Exception,), {}),
    )
    _pg.errors = _pg_errors


# -- redis --------------------------------------------------------------------
if 'redis' not in sys.modules:
    _redis_asyncio = _make_stub('redis.asyncio')
    _redis = _make_stub('redis', asyncio=_redis_asyncio)
