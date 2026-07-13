from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)

PREFIX = "/todo"


def test_status():
    res = client.get(PREFIX)
    assert res.status_code == 200


def test_create_task():
    res = client.post(f"{PREFIX}/api/tasks", json={"title": "Buy groceries", "done": False})
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "title" in data


def test_create_task_required_field():
    res = client.post(f"{PREFIX}/api/tasks", json={"title": "Write tests"})
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "title" in data


def test_create_task_missing_title():
    res = client.post(f"{PREFIX}/api/tasks", json={"done": False})
    assert res.status_code in (400, 422)


def test_list_tasks():
    client.post(f"{PREFIX}/api/tasks", json={"title": "Task for listing", "done": False})
    res = client.get(f"{PREFIX}/api/tasks")
    assert res.status_code == 200
    data = res.json()
    assert isinstance(data, list) or "tasks" in data or "items" in data


def test_list_tasks_contains_created():
    unique_title = "UniqueTask_TestFlowXYZ"
    client.post(f"{PREFIX}/api/tasks", json={"title": unique_title, "done": False})
    res = client.get(f"{PREFIX}/api/tasks")
    assert res.status_code == 200
    body = res.json()
    if isinstance(body, list):
        titles = [t.get("title", "") for t in body]
    elif "tasks" in body:
        titles = [t.get("title", "") for t in body["tasks"]]
    elif "items" in body:
        titles = [t.get("title", "") for t in body["items"]]
    else:
        titles = []
    assert unique_title in titles


def test_create_multiple_tasks():
    tasks = ["Alpha Task", "Beta Task", "Gamma Task"]
    for title in tasks:
        res = client.post(f"{PREFIX}/api/tasks", json={"title": title, "done": False})
        assert res.status_code == 200


def test_create_task_done_true():
    res = client.post(f"{PREFIX}/api/tasks", json={"title": "Completed Task", "done": True})
    assert res.status_code == 200
    data = res.json()
    assert data.get("ok") or "id" in data or "done" in data


def test_list_tasks_returns_list_structure():
    res = client.get(f"{PREFIX}/api/tasks")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, (list, dict))


def test_full_flow_create_and_read():
    unique_title = "FullFlowTask_SHIMS_Demo"
    create_res = client.post(f"{PREFIX}/api/tasks", json={"title": unique_title, "done": False})
    assert create_res.status_code == 200

    list_res = client.get(f"{PREFIX}/api/tasks")
    assert list_res.status_code == 200
    body = list_res.json()
    if isinstance(body, list):
        titles = [t.get("title", "") for t in body]
    elif "tasks" in body:
        titles = [t.get("title", "") for t in body["tasks"]]
    elif "items" in body:
        titles = [t.get("title", "") for t in body["items"]]
    else:
        titles = []
    assert unique_title in titles