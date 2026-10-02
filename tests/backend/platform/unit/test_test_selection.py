# test-catalog-begin
# {
#   "purpose": "平台 / test_selection（测试、参与范围选择）：Business-scope selection and default resource gates; no live dependencies.",
#   "keywords": [
#     "平台",
#     "选择范围",
#     "来源证据",
#     "test_selection",
#     "selection",
#     "source"
#   ],
#   "covers": [
#     "tests/conftest.py",
#     "tests/run.py",
#     "tests/suites.json"
#   ],
#   "environment": [
#     "real_llm",
#     "external_data"
#   ]
# }
# test-catalog-end

"""Business-scope selection and default resource gates; no live dependencies."""
from types import SimpleNamespace

import pytest

from tests import conftest as policy
from tests import run as runner
from tests.run import load_suites, select_scopes
from tests.support.python.paths import PROJECT_ROOT


def test_related_scopes_include_business_consumers_once():
    names, scopes = select_scopes(load_suites(), ['backend.analysis', 'backend.analysis'], related=True)
    assert names == ['backend.analysis', 'backend.quant_strategy', 'frontend.analysis']
    assert len(scopes) == len(set(scopes)) == 3


def test_unit_filter_stays_within_selected_module():
    _, scopes = select_scopes(load_suites(), ['frontend.market'], ['unit'])
    assert scopes == [PROJECT_ROOT / 'tests/frontend/market/unit']


@pytest.mark.parametrize('modules,levels', [(['missing.module'], []), (['frontend.market'], ['contract'])])
def test_unknown_or_empty_selection_fails_closed(modules, levels):
    with pytest.raises(ValueError):
        select_scopes(load_suites(), modules, levels)


def test_catalog_has_existing_source_and_test_paths():
    suites = load_suites()
    for name, suite in suites.items():
        assert (PROJECT_ROOT / suite['test_path']).is_dir(), name
        for source in suite['source_paths']:
            assert (PROJECT_ROOT / source).exists(), (name, source)
        assert set(suite['related']) <= suites.keys(), name


class _Item:
    def __init__(self, path, fixtures=(), marks=()):
        self.path = PROJECT_ROOT / path
        self.fixturenames = list(fixtures)
        self.marks = set(marks)

    def get_closest_marker(self, name):
        return name if name in self.marks else None

    def add_marker(self, mark):
        self.marks.add(mark.name)


@pytest.mark.parametrize('path,fixtures,marks,option', [
    ('tests/ai/sector/integration/test_sector_news_analyst.py', ['real_llm'], [], '--allow-live'),
    ('tests/backend/analysis/integration/test_task_flow.py', [], [], '--allow-db'),
    ('tests/data/ingest/integration/test_incremental.py', [], [], '--allow-db'),
    ('tests/ai/event_study/unit/test_legacy.py', ['predict_db', 'db'], [], '--allow-db'),
    ('tests/ai/event_study/integration/test_routing.py', [], ['requires_db'], '--allow-db'),
    ('tests/ai/market/unit/test_external.py', [], ['external_data'], '--allow-external'),
], ids=['live', 'backend-db', 'data-db', 'ai-db-fixture', 'ai-db-marker', 'external'])
def test_resource_gate_excludes_by_dependency_then_allows_explicit_opt_in(path, fixtures, marks, option):
    excluded = []
    item = _Item(path, fixtures, marks)
    config = SimpleNamespace(getoption=lambda name: False,
                             hook=SimpleNamespace(pytest_deselected=lambda items: excluded.extend(items)))
    selected = [item]
    policy.pytest_collection_modifyitems(config, selected)
    assert selected == [] and excluded == [item]
    config.getoption = lambda name: name == option
    selected = [item]
    policy.pytest_collection_modifyitems(config, selected)
    assert selected == [item]


def test_dynamic_real_fixture_request_cannot_bypass_default_gate():
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: False))
    with pytest.raises(pytest.skip.Exception):
        policy.pytest_fixture_setup(SimpleNamespace(argname='real_toolkit'), request)


def test_dynamic_database_fixture_request_cannot_bypass_default_gate():
    request = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: False))
    with pytest.raises(pytest.skip.Exception):
        policy.pytest_fixture_setup(SimpleNamespace(argname='routing_db'), request)


def test_python_launcher_creates_temp_parent_before_starting_pytest(tmp_path, monkeypatch):
    scope = tmp_path / 'tests/backend/demo/unit'
    scope.mkdir(parents=True)
    (scope / 'test_demo.py').write_text('def test_demo(): pass', encoding='utf-8')
    monkeypatch.setattr(runner, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(runner, 'load_suites', lambda: {'backend.demo': {
        'test_path': 'tests/backend/demo', 'related': [],
    }})
    called = []

    def invoke(command, **kwargs):
        from pathlib import Path
        temporary_path = Path(command[command.index('--basetemp') + 1])
        assert temporary_path.parent.is_dir()
        assert temporary_path.parent == tmp_path / 'var/tmp/pytest'
        assert kwargs['cwd'] == tmp_path
        assert kwargs['env']['LIVEPROFIT_ALLOW_LIVE_E2E'] == '0'
        called.append(command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner.subprocess, 'run', invoke)
    assert runner.main(['--module', 'backend.demo', '--level', 'unit']) == 0
    assert len(called) == 1
