from html.parser import HTMLParser
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from bas.main import create_app
from bas.infrastructure.database import make_engine, plans
from test_planning import ready


def calculate(client, scenario):
    source = client.post('/api/scenarios', json=scenario)
    assert source.status_code == 201
    result = client.post('/api/planning-runs', json={'scenario_id':source.json()['id']})
    assert result.status_code == 200
    return source.json(), result.json()


def test_archive_survives_restart_and_pages_without_full_routes(tmp_path, ready):
    url = f"sqlite:///{(tmp_path/'archive.db').as_posix()}"
    with TestClient(create_app(url)) as client:
        assert client.get('/api/plans').json()['total'] == 0
        first_source, first = calculate(client, ready)
        ready['name'] = 'Другой сценарий'
        second_source, second = calculate(client, ready)
    with TestClient(create_app(url)) as client:
        page = client.get('/api/plans?limit=1').json()
        assert page['total'] == 2
        assert page['items'][0]['id'] == second['id']
        assert page['items'][0]['name'] == 'Другой сценарий'
        assert page['items'][0]['calculated_at'] == second['created_at']
        assert 'missions' not in page['items'][0]
        assert client.get('/api/plans?limit=1&offset=1').json()['items'][0]['id'] == first['id']
        assert client.get('/api/plans?offset=2').json()['items'] == []
        restored = client.get(f"/api/plans/{first['id']}").json()
        assert restored == first
        assert client.get(f"/api/scenarios/{restored['scenario_id']}").json() == first_source


def test_legacy_plan_uses_scenario_date(tmp_path, ready):
    url = f"sqlite:///{(tmp_path/'legacy.db').as_posix()}"
    with TestClient(create_app(url)) as client:
        source, result = calculate(client,ready)
        del result['created_at']
        engine = make_engine(url)
        with engine.begin() as connection:
            connection.execute(update(plans).where(plans.c.id==result['id']).values(payload=result))
        engine.dispose()
        row = client.get('/api/plans').json()['items'][0]
        assert row['created_at'] == source['created_at']
        assert row['calculated_at'] is None
        report = client.get(f"/api/plans/{result['id']}/report")
        assert report.status_code == 200
        assert 'время расчета не записано' in report.text


class ReportParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        assert not any(name.lower().startswith('on') for name, _ in attrs)


def test_report_uses_saved_inputs_and_escapes_user_text(tmp_path, ready, monkeypatch):
    ready['name'] = '<script>alert("name")</script>'
    ready['sites'][0]['name'] = '<img src=x onerror=alert(1)>'
    with TestClient(create_app(f"sqlite:///{(tmp_path/'report.db').as_posix()}")) as client:
        source, result = calculate(client,ready)
        def no_recalculation(*_):
            raise AssertionError('Archive and report must not recalculate')
        monkeypatch.setattr('bas.main.plan',no_recalculation)
        report = client.get(f"/api/plans/{result['id']}/report")
        assert report.status_code == 200
        assert report.headers['content-type'].startswith('text/html')
        assert "default-src 'none'" in report.headers['content-security-policy']
        assert report.headers['cache-control'] == 'no-store'
        parser = ReportParser(); parser.feed(report.text)
        assert 'script' not in parser.tags and 'img' not in parser.tags
        assert '&lt;script&gt;' in report.text and '&lt;img' in report.text
        assert source['id'] in report.text and result['id'] in report.text
        assert 'ручной ввод' in report.text and 'Геометрическое покрытие' in report.text
        assert all(m['aircraft_id'] in report.text for m in result['missions'])
        assert all(w in report.text for w in result['warnings'])
        assert client.get('/api/plans').json()['total'] == 1


@pytest.mark.parametrize('query',['limit=0','limit=51','offset=-1','offset=1000001'])
def test_archive_rejects_invalid_pagination(tmp_path,query):
    with TestClient(create_app(f"sqlite:///{(tmp_path/'bounds.db').as_posix()}")) as client:
        assert client.get('/api/plans?'+query).status_code == 422


def test_unknown_report_is_404(tmp_path):
    with TestClient(create_app(f"sqlite:///{(tmp_path/'empty.db').as_posix()}")) as client:
        assert client.get(f'/api/plans/{uuid4()}/report').status_code == 404
