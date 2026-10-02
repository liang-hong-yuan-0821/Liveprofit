"""Lightweight collection policy; live services are explicit opt-ins."""

from pathlib import Path

import pytest
from dotenv import load_dotenv

from tests.support.python.paths import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / '.env', override=False)

LIVE_FIXTURES = frozenset({'real_llm', 'real_toolkit', 'real_memory'})
DB_FIXTURES = frozenset({'pg_env', 'operation_db', 'predict_db', 'routing_db'})


def pytest_addoption(parser):
    group = parser.getgroup('liveprofit')
    group.addoption('--allow-live', action='store_true', help='Human-only opt-in to real LLM/toolkit fixtures.')
    group.addoption('--allow-db', action='store_true', help='Run explicitly isolated database/Redis suites.')
    group.addoption('--allow-external', action='store_true', help='Human-only opt-in to real external data sources.')
    group.addoption('--allow-e2e', action='store_true', help='Run Python end-to-end suites.')


def pytest_collection_modifyitems(config, items):
    selected, deselected = [], []
    for item in items:
        path = Path(str(item.path)).relative_to(PROJECT_ROOT).parts
        live = bool(LIVE_FIXTURES.intersection(item.fixturenames)) or item.get_closest_marker('real_llm') is not None
        db = (
            (path[:2] == ('tests', 'backend') and 'integration' in path)
            or (path[:2] == ('tests', 'backend') and 'contract' in path and 'api' in path)
            or (path[:2] == ('tests', 'data') and 'integration' in path)
            or path[:2] == ('tests', 'e2e')
            or bool(DB_FIXTURES.intersection(item.fixturenames))
            or item.get_closest_marker('requires_db') is not None
        )
        e2e = path[:2] == ('tests', 'e2e') or item.get_closest_marker('e2e') is not None
        external = item.get_closest_marker('external_data') is not None
        if live:
            item.add_marker(pytest.mark.real_llm)
        if db:
            item.add_marker(pytest.mark.requires_db)
        if e2e:
            item.add_marker(pytest.mark.e2e)
        blocked = (
            (live and not config.getoption('--allow-live'))
            or (external and not config.getoption('--allow-external'))
            or (db and not config.getoption('--allow-db'))
            or (e2e and not config.getoption('--allow-e2e'))
        )
        (deselected if blocked else selected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
    items[:] = selected


@pytest.hookimpl(tryfirst=True)
def pytest_fixture_setup(fixturedef, request):
    # Also protect dynamic request.getfixturevalue(), which collection cannot see.
    if fixturedef.argname in LIVE_FIXTURES and not request.config.getoption('--allow-live'):
        pytest.skip('Real LLM/toolkit fixtures require explicit human --allow-live opt-in.')
    if fixturedef.argname in DB_FIXTURES and not request.config.getoption('--allow-db'):
        pytest.skip('Isolated database fixtures require explicit --allow-db opt-in.')
