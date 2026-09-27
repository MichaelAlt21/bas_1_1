"""Source archive of KUMOsystems for publication: the repository without local and build files.

    backend\\.venv\\Scripts\\python.exe scripts\\package_source.py        (or any Python 3.11+)

Files matched by .gitignore are left out: environments, node_modules, builds, databases, server
settings and the local .env files. The archive is checked for the developer's MapTiler key from
frontend/.env.local and not written if the key is found anywhere in it.
"""
import fnmatch
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'dist-source'
# Never packaged, whatever .gitignore says.
ALWAYS_SKIP = {'.git', 'dist-source'}


def patterns():
    lines = (ROOT / '.gitignore').read_text(encoding='utf-8').splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith('#')]


def ignored(relative, rules):
    parts = relative.parts
    if any(part in ALWAYS_SKIP for part in parts):
        return True
    path = relative.as_posix()
    for rule in rules:
        # As in git: a leading slash anchors the rule to the repository root.
        anchored, rule = rule.startswith('/'), rule.lstrip('/')
        if rule.endswith('/'):
            name = rule.rstrip('/')
            if anchored:
                if path.startswith(name + '/'):
                    return True
            # A directory rule matches any directory along the path, not the file name itself.
            elif any(fnmatch.fnmatch(part, name) for part in parts[:-1]):
                return True
        elif anchored:
            if fnmatch.fnmatch(path, rule):
                return True
        elif fnmatch.fnmatch(parts[-1], rule) or fnmatch.fnmatch(path, rule):
            return True
    return False


def secret():
    local = ROOT / 'frontend' / '.env.local'
    if not local.is_file():
        return None
    match = re.search(r'^VITE_MAPTILER_KEY=(\S+)', local.read_text(encoding='utf-8', errors='ignore'), re.M)
    return match.group(1) if match else None


def version():
    text = (ROOT / 'backend' / 'pyproject.toml').read_text(encoding='utf-8')
    return re.search(r'^version = "([^"]+)"', text, re.M).group(1)


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    rules = patterns()
    files = sorted(p for p in ROOT.rglob('*') if p.is_file() and not ignored(p.relative_to(ROOT), rules))
    packages = {p.parent for p in (ROOT / 'backend' / 'src' / 'bas').rglob('*.py')}
    missing = sorted(str(d.relative_to(ROOT)) for d in packages if not any(f.parent == d for f in files))
    if missing:
        raise SystemExit('Правила .gitignore исключают исходный код, архив не создан:\n  ' + '\n  '.join(missing))
    key = secret()
    if key:
        leaked = [str(p.relative_to(ROOT)) for p in files if key.encode() in p.read_bytes()]
        if leaked:
            raise SystemExit('Ключ MapTiler найден в файлах пакета, архив не создан:\n  ' + '\n  '.join(leaked))
    name = f'KUMOsystems-{version()}-src'
    OUTPUT.mkdir(exist_ok=True)
    archive = OUTPUT / f'{name}.zip'
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for path in files:
            zipped.write(path, Path(name) / path.relative_to(ROOT))
    size = sum(p.stat().st_size for p in files)
    print(f'{archive}: {len(files)} файлов, {size / 2**20:.1f} МБ исходников, архив {archive.stat().st_size / 2**20:.1f} МБ.')
    print('Ключ MapTiler в пакете не найден.' if key else 'Локального ключа MapTiler нет — проверять нечего.')


if __name__ == '__main__':
    main()
