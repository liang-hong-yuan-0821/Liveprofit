# test-catalog-begin
# {
#   "purpose": "证券数据 / test_environment（测试、环境）：Check CLI database isolation without opening a connection or launching the CLI.",
#   "keywords": [
#     "证券数据",
#     "test_environment"
#   ],
#   "covers": [
#     "tests/support/python/instrument_env.py"
#   ],
#   "environment": [
#     "local"
#   ]
# }
# test-catalog-end

"""Check CLI database isolation without opening a connection or launching the CLI."""
import pytest

from tests.support.python.instrument_env import TEST_DB_NAME, isolated_cli_env


def test_cli_environment_overrides_ambient_main_database(monkeypatch):
    monkeypatch.setenv('PG_CONNECTION_STRING', 'dbname=liveprofit')
    dsn = f'host=localhost dbname={TEST_DB_NAME} user=test'
    assert isolated_cli_env(dsn)['PG_CONNECTION_STRING'] == dsn


@pytest.mark.parametrize('dsn', ['dbname=liveprofit', 'host=localhost', 'dbname=other_test'])
def test_cli_environment_rejects_non_designated_database(dsn):
    with pytest.raises(ValueError, match='designated isolated database'):
        isolated_cli_env(dsn)
