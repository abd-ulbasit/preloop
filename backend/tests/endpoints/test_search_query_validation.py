"""Search routes validate and document supported query choices."""

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from preloop.api.endpoints import comments, issues, search


@pytest.fixture
def search_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = FastAPI()
    user = MagicMock()
    for module in (comments, issues, search):
        app.include_router(module.router)
        app.dependency_overrides[module.get_db] = lambda: MagicMock()
        app.dependency_overrides[module.get_current_active_user] = lambda: user
    for module in (comments, issues):
        monkeypatch.setattr(module.crud_tracker, "get_for_account", lambda *a, **kw: [])
    monkeypatch.setattr(
        search, "perform_search", AsyncMock(return_value={"results": [], "total": 0})
    )
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("path", ["/comments/search", "/issues/search", "/search"])
@pytest.mark.parametrize("value", [None, "fulltext", "similarity"])
def test_search_type_accepts_supported_values(
    search_client: TestClient, path: str, value: str | None
) -> None:
    params = {} if value is None else {"search_type": value}
    assert search_client.get(path, params=params).status_code == 200


@pytest.mark.parametrize("path", ["/comments/search", "/issues/search", "/search"])
def test_search_type_rejects_unsupported_values(
    search_client: TestClient, path: str
) -> None:
    response = search_client.get(path, params={"search_type": "unsupported"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "search_type"]


@pytest.mark.parametrize("value", [None, "newest"])
def test_search_sort_accepts_supported_values(
    search_client: TestClient, value: str | None
) -> None:
    params = {} if value is None else {"sort": value}
    assert search_client.get("/search", params=params).status_code == 200


def test_search_sort_rejects_unsupported_values(search_client: TestClient) -> None:
    response = search_client.get("/search", params={"sort": "unsupported"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "sort"]


@pytest.mark.parametrize("path", ["/comments/search", "/issues/search", "/search"])
def test_search_type_schema_lists_supported_values(
    search_client: TestClient, path: str
) -> None:
    parameters = search_client.get("/openapi.json").json()["paths"][path]["get"][
        "parameters"
    ]
    schema = next(p["schema"] for p in parameters if p["name"] == "search_type")
    assert schema["enum"] == ["fulltext", "similarity"]
    assert schema["default"] == "fulltext"


def test_search_sort_schema_documents_optional_newest(
    search_client: TestClient,
) -> None:
    parameters = search_client.get("/openapi.json").json()["paths"]["/search"]["get"][
        "parameters"
    ]
    schema = next(p["schema"] for p in parameters if p["name"] == "sort")
    assert {"const": "newest", "type": "string"} in schema["anyOf"]
    assert {"type": "null"} in schema["anyOf"]


def test_application_registers_search_routes() -> None:
    from preloop.api.app import create_app

    paths = create_app().openapi()["paths"]
    for path in ("/api/v1/comments/search", "/api/v1/issues/search", "/api/v1/search"):
        assert "get" in paths[path]
