# test-catalog-begin
# {
#   "purpose": "测试索引 / test_index（测试）：File-index regression checks use synthetic files, never live services.",
#   "keywords": [
#     "测试索引",
#     "元数据",
#     "关键词",
#     "代码反查",
#     "过期索引",
#     "静态解析"
#   ],
#   "covers": [
#     "tests/index.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""File-index regression checks use synthetic files, never live services."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import index


def _header(metadata):
    return '# ' + index.BEGIN + '\n' + '\n'.join(
        '# ' + line for line in json.dumps(metadata, ensure_ascii=False, indent=2).splitlines()
    ) + '\n# ' + index.END + '\n'


@pytest.fixture
def repository(tmp_path):
    source = tmp_path / 'backend/refresh.py'
    source.parent.mkdir()
    source.write_text('def refresh(): pass\n', encoding='utf-8')
    tests = tmp_path / 'tests/backend/demo/unit'
    tests.mkdir(parents=True)
    metadata = {'purpose': '行情刷新任务复用', 'keywords': ['行情刷新', '复用'],
                'covers': ['backend/refresh.py'], 'environment': ['local']}
    body = 'raise RuntimeError("This test module must never be imported")\n\ndef test_duplicate_job():\n    """重复请求复用任务。"""\n    assert True\n'
    file = tests / 'test_refresh.py'
    file.write_text(_header(metadata) + body, encoding='utf-8')
    suite = {'modules': {'backend.demo': {'test_path': 'tests/backend/demo', 'related': []}}}
    (tmp_path / 'tests/suites.json').write_text(json.dumps(suite), encoding='utf-8')
    return SimpleNamespace(root=tmp_path, file=file, metadata=metadata, body=body)


def test_catalog_is_static_and_preserves_scenario_locations(repository):
    catalog = index.build_catalog(repository.root)
    entry = catalog['tests'][0]
    assert catalog['test_file_count'] == 1
    assert catalog['by_source']['backend/refresh.py'] == ['tests/backend/demo/unit/test_refresh.py']
    case = entry['scenarios'][0]
    assert repository.file.read_text(encoding='utf-8').splitlines()[case['line'] - 1].startswith('def test_duplicate_job')
    assert case['description'] == '重复请求复用任务。'


def test_keyword_and_code_lookup_select_the_same_file(repository):
    catalog = index.build_catalog(repository.root)
    assert index.search(catalog, query='行情刷新 复用') == index.search(catalog, source='backend/refresh.py')
    assert len(index.search(catalog, source='backend', module='backend.demo')) == 1
    assert not index.search(catalog, query='行情刷新 不存在')


@pytest.mark.parametrize('mutation,error', [
    ('missing-header', 'Exactly one'),
    ('missing-source', 'Missing file'),
    ('outside-source', 'inside the repository'),
    ('missing-keywords', 'keywords'),
    ('invalid-environment', 'environment'),
    ('bad-related', 'related_tests'),
])
def test_invalid_metadata_fails_without_generating_partial_output(repository, mutation, error):
    metadata = dict(repository.metadata)
    if mutation == 'missing-header':
        repository.file.write_text(repository.body, encoding='utf-8')
    else:
        if mutation == 'missing-source':
            metadata['covers'] = ['backend/missing.py']
        elif mutation == 'outside-source':
            metadata['covers'] = ['../outside.py']
        elif mutation == 'missing-keywords':
            metadata['keywords'] = []
        elif mutation == 'invalid-environment':
            metadata['environment'] = ['production']
        elif mutation == 'bad-related':
            metadata['related_tests'] = ['tests/backend/demo/unit/missing.py']
        repository.file.write_text(_header(metadata) + repository.body, encoding='utf-8')
    with pytest.raises(ValueError, match=error):
        index.build_catalog(repository.root)
    assert not (repository.root / index.JSON_PATH).exists()


def test_generation_check_detects_changed_tests_and_removal(repository, monkeypatch):
    monkeypatch.setattr(index, 'PROJECT_ROOT', repository.root)
    monkeypatch.setattr(index, 'build_catalog', lambda: original_build(repository.root))
    assert index.main([]) == 0
    assert index.main(['--check']) == 0
    repository.file.write_text(repository.file.read_text(encoding='utf-8') + '\ndef test_new_boundary(): pass\n', encoding='utf-8')
    assert index.main(['--check']) == 1
    assert index.main([]) == 0
    repository.file.unlink()
    assert index.main(['--check']) == 1


original_build = index.build_catalog


def test_unknown_module_and_outside_source_cannot_silently_select(repository, monkeypatch):
    monkeypatch.setattr(index, 'PROJECT_ROOT', repository.root)
    monkeypatch.setattr(index, 'build_catalog', lambda: original_build(repository.root))
    assert index.main(['--module', 'backend.missing']) == 2
    assert index.main(['--source', '../outside']) == 2
    assert index.main(['--query', '不存在']) == 1


def test_typescript_titles_are_read_without_node_or_browser():
    source = "test(`market ${width} ${theme}`, async () => {});\nit('重复任务', () => {});"
    assert [case['name'] for case in index.scenarios(source, '.ts')] == ['market ${width} ${theme}', '重复任务']


def test_usefixtures_marks_keep_cases_but_fixture_and_contextmanager_definitions_do_not():
    source = '''
@pytest.mark.usefixtures("pg_env")
def test_real_case(): pass
@pytest.fixture
def test_support_fixture(): pass
@contextmanager
def test_database_context(): pass
class TestCases:
    @pytest.mark.usefixtures("env")
    def test_method(self): pass
'''
    assert [case['name'] for case in index.scenarios(source, '.py')] == ['test_real_case', 'TestCases.test_method']


def test_generated_index_and_details_partition_entries_and_link_to_real_files(repository):
    catalog = index.build_catalog(repository.root)
    outputs = index.generated_outputs(catalog)
    compact = json.loads(outputs[index.JSON_PATH])
    entry = compact['tests'][0]
    assert 'scenarios' not in entry
    assert entry['scenario_count'] == 1
    details = json.loads(outputs[entry['detail_json']])
    assert details['tests'] == catalog['tests']
    assert '(测试详情/backend/demo.md)' in outputs[index.DOC_PATH]
    assert '(../../../../../tests/backend/demo/unit/test_refresh.py)' in outputs[entry['detail_doc']]
    assert '(../../../../../backend/refresh.py)' in outputs[entry['detail_doc']]


def test_query_defaults_to_summary_and_details_are_explicit(repository, monkeypatch, capsys):
    monkeypatch.setattr(index, 'PROJECT_ROOT', repository.root)
    monkeypatch.setattr(index, 'build_catalog', lambda: original_build(repository.root))
    assert index.main(['--query', '重复请求', '--json']) == 0
    summary = json.loads(capsys.readouterr().out)['tests'][0]
    assert 'scenarios' not in summary and summary['scenario_count'] == 1
    assert index.main(['--source', 'backend/refresh.py', '--json', '--details']) == 0
    detail = json.loads(capsys.readouterr().out)['tests'][0]
    assert detail['scenarios'][0]['name'] == 'test_duplicate_job'
    assert index.main(['--module', 'backend.demo']) == 0
    assert 'test_duplicate_job' not in capsys.readouterr().out
    assert index.main(['--module', 'backend.demo', '--details']) == 0
    assert 'test_duplicate_job' in capsys.readouterr().out


def test_check_covers_module_documents_and_removes_only_owned_obsolete_files(repository, monkeypatch):
    monkeypatch.setattr(index, 'PROJECT_ROOT', repository.root)
    monkeypatch.setattr(index, 'build_catalog', lambda: original_build(repository.root))
    assert index.main([]) == 0
    doc = repository.root / index.module_paths('backend.demo')[1]
    original = doc.read_text(encoding='utf-8')
    doc.write_text(original + 'stale', encoding='utf-8')
    assert index.main(['--check']) == 1
    assert index.main([]) == 0
    obsolete = doc.with_name('old.md')
    obsolete.write_text(original, encoding='utf-8')
    manual = doc.with_name('manual.md')
    manual.write_text('Maintainer notes', encoding='utf-8')
    assert index.main(['--check']) == 1
    assert index.main([]) == 0
    assert not obsolete.exists()
    assert manual.read_text(encoding='utf-8') == 'Maintainer notes'
    repository.file.unlink()
    assert index.main(['--check']) == 1
    assert index.main([]) == 0
    assert not doc.exists()
    assert not (repository.root / index.module_paths('backend.demo')[0]).exists()
    assert index.main(['--check']) == 0


def test_invalid_module_names_cannot_escape_generated_directories():
    with pytest.raises(ValueError, match='Invalid module name'):
        index.module_paths('backend.../outside')
