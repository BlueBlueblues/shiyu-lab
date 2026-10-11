import pytest
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

from app.main import admin_router, create_app
from test_a05 import setup, admin


@pytest.fixture
def client(setup, tmp_path):
    app = create_app(setup[0])
    directory = tmp_path / "management_templates"
    directory.mkdir()
    for template, content in {
        "projects.html": '{{ projects[0].name }}',
        "project_new.html": '创建项目测试模板',
        "workspace.html": '{{ project.name }} {{ project.status }}',
    }.items():
        (directory / template).write_text(content, encoding="utf-8")
    app.state.templates = Jinja2Templates(directory=str(directory))
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("page", ["/", "/projects/new", "/projects/{id}"])
def test_management_auth_and_context(client, setup, page):
    page = page.replace("{id}", setup[1])
    assert client.get(page).status_code == 401
    response = client.get(page, **admin(setup))
    assert response.status_code == 200
    context = {key: value for key, value in response.context.items() if key != "request"}
    if page == "/":
        assert response.template.name == "projects.html"
        assert context == {"projects": client.get("/api/projects", **admin(setup)).json()["data"]}
    elif page == "/projects/new":
        assert response.template.name == "project_new.html" and context == {}
    else:
        assert response.template.name == "workspace.html"
        assert context == client.get(f"/api/projects/{setup[1]}", **admin(setup)).json()["data"]
        assert set(context) == {"project", "plan", "experiment", "latest_report_meta", "running_run_id"}


def test_missing_project(client, setup):
    response = client.get("/projects/missing", **admin(setup))
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("page", ["/", "/projects/new", "/projects/{id}"])
def test_missing_template_is_explicit(setup, tmp_path, page):
    app = create_app(setup[0])
    empty = tmp_path / "empty"
    empty.mkdir()
    app.state.templates = Jinja2Templates(directory=str(empty))
    with TestClient(app) as client:
        response = client.get(page.replace("{id}", setup[1]), **admin(setup))
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "页面集成未完成" in response.json()["error"]["message"]


def test_routes_registered_once(client):
    paths = [route.path for route in admin_router.routes if "GET" in getattr(route, "methods", set())]
    for path in ("/", "/projects/new", "/projects/{project_id}"):
        assert paths.count(path) == 1
