"""Select business test suites without guessing scope from test filenames."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

from tests.support.python.paths import PROJECT_ROOT


def load_suites():
    return json.loads((PROJECT_ROOT / 'tests/suites.json').read_text(encoding='utf-8'))['modules']


def select_scopes(suites, modules, levels=(), related=False):
    names = list(dict.fromkeys(modules))
    for name in names:
        if name not in suites:
            raise ValueError(f'Unknown module: {name}')
    if related:
        names = list(dict.fromkeys(names + [r for name in names for r in suites[name]['related']]))
    scopes = []
    for name in names:
        if name not in suites:
            raise ValueError(f'Unknown related module: {name}')
        base = (PROJECT_ROOT / suites[name]['test_path']).resolve()
        if not base.is_relative_to((PROJECT_ROOT / 'tests').resolve()) or not base.is_dir():
            raise ValueError(f'Invalid test path for {name}: {base}')
        chosen = [base / level for level in levels] if levels else [base]
        scopes += [p for p in chosen if p.is_dir()]
    scopes = list(dict.fromkeys(scopes))
    if not scopes:
        raise ValueError('No test directories match the requested modules/levels.')
    return names, scopes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--module', nargs='+', default=[], help='e.g. backend.analysis frontend.market data.instrument')
    parser.add_argument('--level', nargs='+', choices=['unit', 'integration', 'contract'], default=[])
    parser.add_argument('--related', action='store_true', help='Include explicitly declared associated regression modules.')
    parser.add_argument('--list', action='store_true', help='Show modules/scopes; execute nothing.')
    parser.add_argument('--allow-db', action='store_true')
    parser.add_argument('--allow-live', action='store_true', help='Human-only real LLM/toolkit opt-in.')
    parser.add_argument('--allow-external', action='store_true', help='Human-only real external source opt-in.')
    parser.add_argument('--allow-e2e', action='store_true')
    args = parser.parse_args(argv)
    suites = load_suites()
    if not args.module:
        if not args.list:
            parser.error('Select --module, or use --list to inspect the catalog.')
        for name, suite in suites.items():
            print(f'{name}: {suite["test_path"]}')
        return 0
    try:
        names, scopes = select_scopes(suites, args.module, args.level, args.related)
    except ValueError as exc:
        parser.error(str(exc))
    print('Modules: ' + ', '.join(names), flush=True)
    for scope in scopes:
        print('Scope: ' + scope.relative_to(PROJECT_ROOT).as_posix(), flush=True)
    if args.list:
        return 0
    python_scopes, frontend_scopes, browser_files = [], [], []
    for scope in scopes:
        if any(scope.rglob('test_*.py')):
            python_scopes.append(scope.relative_to(PROJECT_ROOT).as_posix())
        if any(scope.rglob('*.test.ts')) or any(scope.rglob('*.test.tsx')):
            frontend_scopes.append(scope.relative_to(PROJECT_ROOT).as_posix())
        browser_files += list(scope.rglob('*.spec.ts'))
    if browser_files and not args.allow_e2e:
        parser.error('Browser suites require --allow-e2e; inspect their environment before running.')
    commands = []
    if python_scopes:
        temporary_root = PROJECT_ROOT / 'var/tmp/pytest'
        temporary_root.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, '-m', 'pytest', *python_scopes, '-q', '-p', 'no:cacheprovider',
                   '-W', 'ignore::pytest.PytestConfigWarning',
                   '--basetemp', str(temporary_root / uuid.uuid4().hex)]
        command += [f'--allow-{name}' for name in ('db', 'live', 'external', 'e2e') if getattr(args, f'allow_{name}')]
        commands.append(command)
    if frontend_scopes or browser_files:
        node = shutil.which('node')
        if not node:
            parser.error('Node.js is required for frontend/browser suites.')
        launcher = str(PROJECT_ROOT / 'frontend/test-runner.mjs')
        if frontend_scopes:
            commands.append([node, launcher, 'vitest', 'run', *frontend_scopes])
        if browser_files:
            commands.append([node, launcher, 'playwright', 'test', *[p.relative_to(PROJECT_ROOT).as_posix() for p in browser_files]])
    if not commands:
        parser.error('No test files found in the requested scope.')
    environment = os.environ.copy()
    # Ambient environment cannot accidentally enable the real-job browser scenario.
    environment['LIVEPROFIT_ALLOW_LIVE_E2E'] = '1' if args.allow_live else '0'
    for command in commands:
        result = subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=False)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
