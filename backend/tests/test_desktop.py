import socket
from pathlib import Path

from fastapi.testclient import TestClient

from bas import desktop
from bas.main import create_app


def test_choose_port_skips_a_taken_port():
    with socket.socket() as taken:
        taken.bind((desktop.HOST, 0))
        port = taken.getsockname()[1]
        assert not desktop.port_is_free(port)
        assert desktop.choose_port(port, 5) != port


def test_bundle_root_follows_kumo_home(monkeypatch, tmp_path):
    monkeypatch.setenv('KUMO_HOME', str(tmp_path))
    assert desktop.bundle_root() == tmp_path


def test_health_ignores_other_services():
    with socket.socket() as closed:
        closed.bind((desktop.HOST, 0))
        port = closed.getsockname()[1]
    assert desktop.health(port, timeout=.2) is None


def test_static_dir_from_environment(monkeypatch, tmp_path):
    web = tmp_path / 'web'
    web.mkdir()
    (web / 'index.html').write_text('<div id="root"></div>', encoding='utf-8')
    monkeypatch.setenv('BAS_STATIC_DIR', str(web))
    with TestClient(create_app(f"sqlite:///{(tmp_path / 'd.db').as_posix()}")) as client:
        assert 'id="root"' in client.get('/').text
        assert client.get('/api/health').json()['status'] == 'ok'


def test_data_folder_falls_back_when_bundle_is_read_only(monkeypatch, tmp_path):
    bundle, local = tmp_path / 'bundle', tmp_path / 'local'
    bundle.mkdir()
    (bundle / 'data').write_text('not a folder', encoding='utf-8')  # mkdir fails like a read-only location
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    assert desktop.data_folder(bundle) == local / 'KUMOsystems' / 'data'
    assert desktop.data_folder(tmp_path) == tmp_path / 'data'


def test_data_folder_without_profile_variables(monkeypatch, tmp_path):
    # A stripped environment (no LOCALAPPDATA/USERPROFILE) must still start from a writable bundle.
    for name in ('LOCALAPPDATA', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'HOME'):
        monkeypatch.delenv(name, raising=False)
    assert desktop.data_folder(tmp_path) == tmp_path / 'data'
