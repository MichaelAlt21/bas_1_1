import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bas.main import create_app
from bas.domain.models import Scenario


@pytest.fixture
def scenario():
    return json.loads((Path(__file__).parents[2] / "contracts/scenario.example.json").read_text(encoding="utf-8"))


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")) as client:
        yield client


def test_saved_scenario_survives_restart(tmp_path, scenario):
    url = f"sqlite:///{(tmp_path / 'persistent.db').as_posix()}"
    with TestClient(create_app(url)) as client:
        response = client.post("/api/scenarios", json=scenario)
        assert response.status_code == 201
        scenario_id = response.json()["id"]
        expected = response.json()['scenario']
    with TestClient(create_app(url)) as client:
        assert client.get(f"/api/scenarios/{scenario_id}").json()["scenario"] == expected
        assert {p['id'] for p in expected['aircraft_profiles']} == {a['profile_id'] for a in scenario['fleet']}


def test_built_frontend_is_served_without_shadowing_the_api(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export {}", encoding="utf-8")
    url = f"sqlite:///{(tmp_path / 'static.db').as_posix()}"
    with TestClient(create_app(url, static_dir=dist)) as client:
        root = client.get("/")
        assert root.status_code == 200
        assert "text/html" in root.headers["content-type"]
        assert '<div id="root">' in root.text
        # Windows' registry-backed mimetypes can report text/plain for .js, which
        # browsers refuse to execute as a module script (see main.py's mimetypes.add_type).
        script = client.get("/assets/app.js")
        assert script.status_code == 200
        assert script.headers["content-type"].startswith("application/javascript")
        assert client.get("/api/health").status_code == 200


def test_missing_frontend_build_leaves_api_working(tmp_path):
    url = f"sqlite:///{(tmp_path / 'nostatic.db').as_posix()}"
    with TestClient(create_app(url, static_dir=tmp_path / "no-such-dist")) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").status_code == 200


@pytest.mark.parametrize("coordinates", [
    [[[37,55],[38,55],[38,56],[37,56]]],
    [[[37,55],[38,56],[37,56],[38,55],[37,55]]],
    [[[181,55],[182,55],[182,56],[181,55]]],
])
def test_invalid_geometry_is_rejected(client, scenario, coordinates):
    scenario["survey_area"]["coordinates"] = coordinates
    assert client.post("/api/scenarios", json=scenario).status_code == 422


def test_planner_rejects_incomplete_scenario(client, scenario):
    scenario["allowed_airspace"] = None
    scenario_id = client.post("/api/scenarios", json=scenario).json()["id"]
    response = client.post("/api/planning-runs", json={"scenario_id":scenario_id})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "AIRSPACE_REQUIRED"


def test_invalid_wind_is_rejected(client, scenario):
    scenario["weather"]["wind_speed_mps"] = -1
    assert client.post("/api/scenarios", json=scenario).status_code == 422


def test_unknown_scenario(client):
    assert client.get("/api/scenarios/00000000-0000-0000-0000-000000000000").status_code == 404


def test_catalog_and_health(client):
    assert client.get("/api/health").json()["planner_ready"] is True
    assert len(client.get("/api/catalog/aircraft").json()) == 3



def test_json_declares_utf8_for_windows_clients(tmp_path):
    # Windows PowerShell 5.1 reads a JSON body without a charset as Latin-1.
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'charset.db').as_posix()}")) as client:
        assert client.get('/api/health').headers['content-type'] == 'application/json; charset=utf-8'
        missing = client.get('/api/plans/00000000-0000-0000-0000-000000000000')
        assert missing.status_code == 404 and missing.headers['content-type'] == 'application/json; charset=utf-8'
        assert missing.json() == {'detail': 'План не найден'}
        invalid = client.post('/api/scenarios', json={})
        assert invalid.status_code == 422 and invalid.headers['content-type'] == 'application/json; charset=utf-8'
