"""Standalone Windows build of KUMOsystems: runs without installed Python or Node.js.

Run with the backend environment (it also provides the packages to bundle):
    backend\\.venv\\Scripts\\python.exe scripts\\build_windows.py [--with-maptiler-key] [--skip-smoke] [--no-zip]

The private runtime is assembled from the local CPython installation (the base of the virtual
environment) and the installed packages the service depends on; nothing is downloaded. The
interface is built into the bundle with Vite; the build machine needs Node.js, the target does not.
"""
import argparse
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
import tomllib
import urllib.request
import zipfile
from importlib import metadata
from pathlib import Path

from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]
BACKEND, FRONTEND = ROOT / 'backend', ROOT / 'frontend'
OUTPUT = ROOT / 'dist-windows'

BASE_FILES = ['python.exe', 'pythonw.exe', 'python3.dll', f'python{sys.version_info.major}{sys.version_info.minor}.dll',
              'vcruntime140.dll', 'vcruntime140_1.dll', 'LICENSE.txt']
# Parts of the standard library a web service never imports: GUI, tests, packaging tools.
STDLIB_SKIP = {'site-packages', 'test', 'idlelib', 'tkinter', 'turtledemo', 'ensurepip', 'lib2to3', 'venv'}
DLLS_SKIP = {'_tkinter.pyd', 'tcl86t.dll', 'tk86t.dll', '_test*.pyd'}
# Bundled test suites of third-party packages, never imported at run time.
PACKAGE_TESTS = ['numpy/tests', 'numpy/_core/tests', 'numpy/lib/tests', 'numpy/linalg/tests', 'numpy/fft/tests',
                 'numpy/random/tests', 'numpy/ma/tests', 'numpy/polynomial/tests', 'numpy/typing/tests', 'numpy/f2py',
                 'shapely/tests', 'pyproj/tests']
# Microsoft Visual C++ runtime: not part of Windows, redistributable next to the application.
VC_RUNTIME = ('msvcp140', 'vcruntime140', 'concrt140', 'vcomp140', 'vccorlib140')
SYSTEM32 = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32'


def step(text):
    print(f'\n== {text}', flush=True)


def version():
    return tomllib.loads((BACKEND / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']


def runtime_distributions():
    """Installed distributions the service needs at run time: pyproject dependencies and their closure."""
    project = tomllib.loads((BACKEND / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    environment = default_environment()
    queue = [(Requirement(r), '') for r in project['dependencies']]
    found = {}
    while queue:
        requirement, extra = queue.pop()
        if requirement.marker and not requirement.marker.evaluate({**environment, 'extra': extra}):
            continue
        name = canonicalize_name(requirement.name)
        wanted_extras = set(requirement.extras)
        if name in found and wanted_extras <= found[name][1]:
            continue
        distribution = metadata.distribution(requirement.name)
        extras = (found[name][1] if name in found else set()) | wanted_extras
        found[name] = (distribution, extras)
        for text in distribution.requires or []:
            child = Requirement(text)
            for active in [''] + sorted(extras):
                if child.marker is None or child.marker.evaluate({**environment, 'extra': active}):
                    queue.append((child, active))
                    break
    return [d for d, _ in found.values()]


def copy_python(runtime):
    base = Path(sys.base_prefix)
    runtime.mkdir(parents=True)
    for name in BASE_FILES:
        shutil.copy2(base / name, runtime / name)
    (runtime / 'DLLs').mkdir()
    for item in (base / 'DLLs').iterdir():
        if not any(item.match(pattern) for pattern in DLLS_SKIP):
            shutil.copy2(item, runtime / 'DLLs' / item.name)
    lib = runtime / 'Lib'
    top = base / 'Lib'
    shutil.copytree(top, lib, ignore=lambda folder, names: [n for n in names if n == '__pycache__' or (Path(folder) == top and n in STDLIB_SKIP)])
    (lib / 'site-packages').mkdir()


def copy_packages(site):
    distributions = runtime_distributions()
    for distribution in distributions:
        root = Path(distribution.locate_file(''))
        for entry in distribution.files or []:
            source = Path(distribution.locate_file(entry)).resolve()
            try:
                relative = source.relative_to(root.resolve())
            except ValueError:
                continue  # console scripts in ..\Scripts
            if '__pycache__' in relative.parts or not source.is_file():
                continue
            target = site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    for folder in PACKAGE_TESTS:
        shutil.rmtree(site / folder, ignore_errors=True)
    shutil.copytree(BACKEND / 'src' / 'bas', site / 'bas', ignore=shutil.ignore_patterns('__pycache__'))
    return sorted(f"{d.metadata['Name']} {d.version}" for d in distributions)


def build_interface(web, with_key):
    env = dict(os.environ)
    if not with_key:
        # Variables already in the environment win over .env files: the shipped build carries no MapTiler key.
        env['VITE_MAPTILER_KEY'] = ''
    tools = FRONTEND / 'node_modules' / '.bin'
    subprocess.run([str(tools / 'tsc.cmd'), '--noEmit'], cwd=FRONTEND, check=True, env=env)
    subprocess.run([str(tools / 'vite.cmd'), 'build', '--outDir', str(web), '--emptyOutDir', '--logLevel', 'warn'], cwd=FRONTEND, check=True, env=env)
    if not with_key:
        key = next((line.split('=', 1)[1].strip() for line in _env_lines() if line.startswith('VITE_MAPTILER_KEY=')), '')
        if key and any(key in path.read_text(encoding='utf-8', errors='ignore') for path in web.rglob('*.js')):
            raise SystemExit('Ключ MapTiler попал в сборку интерфейса — сборка остановлена.')


def _env_lines():
    lines = []
    for name in ('.env', '.env.local', '.env.production', '.env.production.local'):
        path = FRONTEND / name
        if path.is_file():
            lines += path.read_text(encoding='utf-8', errors='ignore').splitlines()
    return lines


def pe_imports(path):
    """DLL names a PE file imports, normal and delay-loaded."""
    data = path.read_bytes()
    if data[:2] != b'MZ':
        return []
    pe = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe:pe + 4] != b'PE\0\0':
        return []
    sections, = struct.unpack_from('<H', data, pe + 6)
    optional_size, = struct.unpack_from('<H', data, pe + 20)
    optional = pe + 24
    directories = optional + (112 if struct.unpack_from('<H', data, optional)[0] == 0x20B else 96)
    table = [struct.unpack_from('<8sIIII', data, optional + optional_size + 40 * i) for i in range(sections)]

    def offset(rva):
        for _, size, address, raw_size, raw in table:
            if address <= rva < address + max(size, raw_size):
                return rva - address + raw
        return None
    names = []
    for index, width, field in ((1, 20, 12), (13, 32, 4)):
        # Data directory 1 is the import table, 13 the delay-load table; each entry names one DLL.
        rva = struct.unpack_from('<I', data, directories + 8 * index)[0]
        position = offset(rva) if rva else None
        while position is not None and position + width <= len(data):
            entry = data[position:position + width]
            if not any(entry):
                break
            start = offset(struct.unpack_from('<I', entry, field)[0])
            if start is None:
                break
            names.append(data[start:data.index(b'\0', start)].decode('ascii', 'replace'))
            position += width
    return names


def audit_dlls(bundle, runtime):
    """Every DLL the binaries import must be in the bundle or part of Windows itself."""
    binaries = [p for p in bundle.rglob('*') if p.suffix.lower() in ('.dll', '.pyd', '.exe')]
    present = {p.name.lower() for p in binaries}
    missing = set()
    for binary in binaries:
        for name in pe_imports(binary):
            lower = name.lower()
            if lower in present or lower.startswith(('api-ms-win-', 'ext-ms-')):
                continue
            if lower.startswith(VC_RUNTIME):
                source = SYSTEM32 / name
                if not source.is_file():
                    raise SystemExit(f'{binary.name} требует {name}, а его нет ни в сборке, ни в System32.')
                shutil.copy2(source, runtime / lower)
                present.add(lower)
                print(f'   + {lower} (Visual C++ runtime, для {binary.name})')
            elif (SYSTEM32 / name).is_file():
                continue  # part of Windows
            else:
                missing.add(f'{name} (для {binary.relative_to(bundle)})')
    if missing:
        raise SystemExit('Не найдены библиотеки:\n  ' + '\n  '.join(sorted(missing)))
    return len(binaries)


def write_launcher(bundle, number, with_key):
    (bundle / 'KUMOsystems.cmd').write_text(
        '@echo off\r\n'
        f'title KUMOsystems {number}\r\n'
        'rem Isolated mode: no installed Python, PYTHON* variables or user packages can interfere.\r\n'
        '"%~dp0runtime\\python.exe" -I -X utf8 -m bas.desktop %*\r\n'
        'if errorlevel 1 pause\r\n', encoding='ascii')
    readme = f"""KUMOsystems {number} — планировщик авиационных работ БАС (автономная сборка для Windows)

Требования: Windows 10 или 11, 64 бит. Python и Node.js устанавливать не нужно.

Запуск
  1. Распакуйте архив в любую папку, где у вас есть права на запись (например, Документы).
  2. Дважды щелкните KUMOsystems.cmd. Откроется окно сервера и браузер с интерфейсом
     (обычно http://127.0.0.1:8000; если порт занят — следующий свободный).
  3. Чтобы остановить KUMOsystems, закройте окно сервера.
  Повторный запуск при работающем сервере просто откроет интерфейс в браузере.

  Если Windows предупреждает о файле из Интернета, подтвердите запуск или заранее снимите
  блокировку: свойства ZIP-архива -> «Разблокировать», затем распакуйте.

Данные
  Сценарии, планы и настройки сервера хранятся в папке data рядом с KUMOsystems.cmd
  (база SQLite data\\bas.db). Если в папку нельзя писать (например, Program Files),
  данные хранятся в %LOCALAPPDATA%\\KUMOsystems\\data — путь показан в окне сервера.
  Для резервной копии или переноса скопируйте папку data.
  Подключение к PostgreSQL компании настраивается в интерфейсе («БД компании»).

Без интернета
  Расчеты работают без сети. Если карта не загружается, выберите подложку
  «Без подложки · офлайн». Погоду в этом случае задайте вручную.
  Спутниковая подложка MapTiler в этой сборке {'включена' if with_key else 'не настроена'}; свою подложку можно добавить в «Мои карты».

Руководство оператора: docs\\USER_GUIDE.md. Примеры сценариев и зон: examples\\.

Состав: CPython {sys.version.split()[0]} (runtime\\LICENSE.txt) и пакеты с их лицензиями
в runtime\\Lib\\site-packages\\*.dist-info. Демонстрационный расчет: не задание для автопилота.
"""
    (bundle / 'README.txt').write_text(readme, encoding='utf-8-sig')


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def smoke_test(bundle):
    """Run the bundle the way a clean machine would: bundled interpreter, no Python on PATH."""
    python = bundle / 'runtime' / 'python.exe'
    env = {'SystemRoot': os.environ['SystemRoot'], 'PATH': str(SYSTEM32), 'TEMP': os.environ['TEMP'], 'TMP': os.environ['TEMP']}
    probe = subprocess.run([str(python), '-I', '-c', (
        'import sys, json, importlib; '
        'mods = [importlib.import_module(m) for m in ("bas.main", "numpy", "shapely", "pyproj", "pydantic_core", "sqlalchemy", "psycopg", "zoneinfo")]; '
        'import zoneinfo; zoneinfo.ZoneInfo("Europe/Moscow"); '
        'print(json.dumps({"prefix": sys.prefix, "path": sys.path, "files": [getattr(m, "__file__", "") for m in mods]}))')],
        env=env, capture_output=True, text=True, timeout=120)
    if probe.returncode:
        raise SystemExit('Сборка не импортируется:\n' + probe.stderr)
    report = json.loads(probe.stdout)
    outside = [p for p in report['path'] + report['files'] if p and not Path(p).resolve().is_relative_to(bundle.resolve())]
    if outside:
        raise SystemExit('Сборка использует файлы вне себя:\n  ' + '\n  '.join(outside))
    port = free_port()
    server = subprocess.Popen([str(python), '-I', '-X', 'utf8', '-m', 'bas.desktop', '--no-browser', '--port', str(port)],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        base = f'http://127.0.0.1:{port}'
        for _ in range(120):
            try:
                health = json.loads(urllib.request.urlopen(base + '/api/health', timeout=1).read())
                break
            except OSError:
                time.sleep(.5)
        else:
            server.terminate()
            raise SystemExit('Сервер сборки не ответил за минуту:\n' + server.communicate(timeout=20)[0].decode('utf-8', 'replace'))
        page = urllib.request.urlopen(base + '/', timeout=5).read().decode('utf-8')
        scenario = (ROOT / 'contracts' / 'scenario.example.json').read_bytes()
        post = lambda path, body: json.loads(urllib.request.urlopen(urllib.request.Request(
            base + path, data=body, headers={'Content-Type': 'application/json'}), timeout=120).read())
        stored = post('/api/scenarios', scenario)
        plan = post('/api/planning-runs', json.dumps({'scenario_id': stored['id']}).encode())
        analysis = post('/api/scenarios/' + stored['id'] + '/fleet-analysis', b'')
        print(f"   API {health['version']}, интерфейс {'найден' if '<div id=\"root\">' in page else 'НЕ НАЙДЕН'}, "
              f"план демо: {plan['metrics']['completion_time_s'] / 60:.1f} мин, анализ парка: {len(analysis['rows'])} строк")
        if '<div id="root">' not in page:
            raise SystemExit('Сервер сборки не отдает интерфейс.')
    finally:
        server.terminate()
        server.wait(timeout=20)
    # The test must not ship its database: data\ is created on the user's first start.
    shutil.rmtree(bundle / 'data', ignore_errors=True)


def folder_size(path):
    return sum(p.stat().st_size for p in path.rglob('*') if p.is_file())


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--with-maptiler-key', action='store_true', help='встроить ключ MapTiler из frontend/.env.local (архив будет содержать ключ)')
    parser.add_argument('--skip-smoke', action='store_true', help='не запускать проверку собранного сервера')
    parser.add_argument('--no-zip', action='store_true', help='не упаковывать в ZIP')
    args = parser.parse_args()
    if sys.platform != 'win32' or struct.calcsize('P') != 8:
        raise SystemExit('Сборка выполняется на 64-битной Windows.')
    if sys.prefix == sys.base_prefix:
        raise SystemExit('Запустите сборку из виртуального окружения backend: backend\\.venv\\Scripts\\python.exe')
    number = version()
    name = f'KUMOsystems-{number}-win64'
    bundle = OUTPUT / name
    started = time.monotonic()
    step(f'Сборка {name}')
    shutil.rmtree(bundle, ignore_errors=True)
    runtime = bundle / 'runtime'
    step(f'Python {sys.version.split()[0]} из {sys.base_prefix}')
    copy_python(runtime)
    step('Пакеты сервиса')
    for line in copy_packages(runtime / 'Lib' / 'site-packages'):
        print('  ', line)
    step('Интерфейс' + (' (с ключом MapTiler)' if args.with_maptiler_key else ' (без ключа MapTiler)'))
    build_interface(bundle / 'app' / 'web', args.with_maptiler_key)
    step('Документы и примеры')
    (bundle / 'docs').mkdir()
    shutil.copy2(ROOT / 'docs' / 'USER_GUIDE.md', bundle / 'docs' / 'USER_GUIDE.md')
    shutil.copy2(ROOT / 'CHANGELOG.md', bundle / 'docs' / 'CHANGELOG.md')
    shutil.copytree(ROOT / 'contracts', bundle / 'examples', ignore=shutil.ignore_patterns('README.md'))
    write_launcher(bundle, number, args.with_maptiler_key)
    step('Проверка зависимостей DLL')
    print(f'   проверено файлов: {audit_dlls(bundle, runtime)}')
    step('Компиляция байт-кода')
    # With the bundled interpreter, so the bytecode matches it even if the build Python differs.
    subprocess.run([str(runtime / 'python.exe'), '-I', '-m', 'compileall', '-q', '-j', '0', str(runtime / 'Lib')], check=True)
    if not args.skip_smoke:
        step('Проверка собранного сервера без установленного Python')
        smoke_test(bundle)
    size = folder_size(bundle)
    if not args.no_zip:
        step('Архив')
        archive = OUTPUT / f'{name}.zip'
        archive.unlink(missing_ok=True)
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
            for path in sorted(bundle.rglob('*')):
                zipped.write(path, Path(name) / path.relative_to(bundle))
        print(f'   {archive} — {archive.stat().st_size / 2**20:.1f} МБ')
    print(f'\nГотово за {time.monotonic() - started:.0f} с: {bundle} ({size / 2**20:.1f} МБ)')


if __name__ == '__main__':
    main()
