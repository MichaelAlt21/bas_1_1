"""Launcher of the standalone Windows build: one folder, no installed Python or Node.js.

Layout of the build (see scripts/build_windows.py):
    KUMOsystems.cmd        double-click entry point
    runtime/python.exe     private CPython with the service and its packages
    app/web/               built interface
    data/                  SQLite database and server settings, created on first start

The console window is the server: closing it stops KUMOsystems.
"""
import argparse
import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

HOST = '127.0.0.1'
FIRST_PORT = 8000
PORTS_TRIED = 20


def bundle_root() -> Path:
    # runtime/python.exe sits one level below the root of the build.
    return Path(os.environ.get('KUMO_HOME') or Path(sys.executable).resolve().parent.parent)


def data_folder(root: Path) -> Path:
    """data\\ next to the launcher; %LOCALAPPDATA%\\KUMOsystems\\data when the folder is read-only (Program Files)."""
    folders = [root / 'data']
    local = os.environ.get('LOCALAPPDATA') or os.environ.get('USERPROFILE')
    if local:
        folders.append(Path(local) / 'KUMOsystems' / 'data')
    for folder in folders:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            probe = folder / '.write-test'
            probe.write_text('ok', encoding='ascii')
            probe.unlink()
            return folder
        except OSError:
            continue
    raise SystemExit('Нет папки с правами на запись для данных KUMOsystems.')


def health(port: int, timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f'http://{HOST}:{port}/api/health', timeout=timeout) as response:
            data = json.loads(response.read())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and 'planner_mode' in data else None


def port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def choose_port(first: int = FIRST_PORT, count: int = PORTS_TRIED) -> int:
    for port in range(first, first + count):
        if port_is_free(port):
            return port
    raise SystemExit(f'Порты {first}–{first + count - 1} заняты: закройте другие программы или укажите --port.')


def open_when_ready(url: str, port: int, attempts: int = 120):
    for _ in range(attempts):
        if health(port, timeout=.5):
            webbrowser.open(url)
            return
        time.sleep(.5)
    print('Сервер не ответил за минуту — откройте адрес вручную:', url)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='KUMOsystems', description='Запуск KUMOsystems без установки')
    parser.add_argument('--port', type=int, help='порт сервера; по умолчанию первый свободный начиная с 8000')
    parser.add_argument('--no-browser', action='store_true', help='не открывать браузер')
    args = parser.parse_args(argv)
    root = bundle_root()
    data = data_folder(root)
    # The database (bas.db) and server settings (.runtime) are resolved from the working directory.
    os.chdir(data)
    os.environ.setdefault('BAS_STATIC_DIR', str(root / 'app' / 'web'))
    if args.port is None and health(FIRST_PORT):
        # Already running (the window is probably minimised): just bring the interface up.
        print(f'KUMOsystems уже запущен: http://{HOST}:{FIRST_PORT}')
        if not args.no_browser:
            webbrowser.open(f'http://{HOST}:{FIRST_PORT}')
        return 0
    port = args.port or choose_port()
    url = f'http://{HOST}:{port}'
    print(f'KUMOsystems запущен: {url}\nДанные: {data}\nЗакройте это окно, чтобы остановить сервер.\n', flush=True)
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, port), daemon=True).start()
    import uvicorn
    uvicorn.run('bas.main:app', host=HOST, port=port, log_level='warning')
    return 0


if __name__ == '__main__':
    sys.exit(main())
