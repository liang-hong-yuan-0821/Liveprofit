# test-catalog-begin
# {
#   "purpose": "组合暂停下多个未绑定BUY的数据库全集选择、真实捕获、逐结果封口和事务原子性；生命周期步骤保持关闭。",
#   "keywords": ["量化策略", "命令", "投资组合", "回撤", "未绑定订单", "封口", "事务回滚", "并发", "权限", "迁移"],
#   "covers": [
#     "backend/migrations/versions/0054_portfolio_unbound_drawdown_capture.py",
#     "backend/modules/quant_strategy/application/lifecycle_portfolio_command.py",
#     "backend/modules/quant_strategy/application/lifecycle_portfolio_selection.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_operation_models.py"
#   ],
#   "environment": ["db"]
# }
# test-catalog-end
"""Only random operation_db databases are migrated, written and dropped."""
import hashlib
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.modules.investment_workspace.infrastructure.models import Portfolio
from backend.modules.quant_strategy.application.errors import LifecycleInvalidStateError
from backend.modules.quant_strategy.application.lifecycle_bound_command import (
    stage_bound_order_status_command,
)
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    OrderStatusCommandRequest,
)
from backend.modules.quant_strategy.application.lifecycle_portfolio_command import (
    stage_portfolio_unbound_drawdown_command,
)
from backend.modules.quant_strategy.application.lifecycle_portfolio_selection import (
    PortfolioDrawdownCommandRequest,
)
from backend.modules.quant_strategy.application.lifecycle_unbound_command import (
    stage_unbound_order_status_command,
)
from backend.modules.quant_strategy.application.portfolio_drawdown_actions import (
    PortfolioDrawdownActions,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_history_repository import (
    _hash,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionIntent,
    SuggestedOrder,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_operation_models import (
    LifecycleBusinessCommand,
)
from tests.backend.quant_strategy.support import (
    lifecycle_operation_schema as schema_support,
)

operation_db = schema_support.operation_db
DAY = date(2026, 9, 24)


def _setup(operation_db):
    config, engine = operation_db
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        assert session.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
        portfolio = Portfolio(name=f"portfolio-command-{uuid.uuid4().hex}", risk_profile="AGGRESSIVE",
                              total_assets=Decimal(100000), available_cash=Decimal(75000),
                              max_drawdown_pct=Decimal('.25'), net_asset_value=Decimal(75000),
                              peak_net_asset_value=Decimal(100000), risk_facts_as_of=DAY)
        session.add(portfolio)
        session.commit()
        PortfolioDrawdownActions(session).pause_if_full(portfolio.id, valuation_date=DAY)
        session.commit()
        orders = [SuggestedOrder(portfolio_id=portfolio.id, market="CN", symbol="000001.SZ", side="BUY",
                                 quantity=Decimal(100), filled_quantity=Decimal(40 if status == 'PARTIALLY_FILLED' else 0),
                                 limit_price=Decimal('10.1234'), reserved_cash=Decimal('1012.34'),
                                 reason_code="TEST", status=status, revision=1)
                  for status in ('PROPOSED', 'EXECUTING', 'PARTIALLY_FILLED', 'RECONCILIATION_REQUIRED')]
        sell = SuggestedOrder(portfolio_id=portfolio.id, market="CN", symbol="000002.SZ", side="SELL",
                              quantity=Decimal(100), limit_price=Decimal(10), reason_code="TEST", status="PROPOSED", revision=1)
        session.add_all((*orders, sell))
        session.commit()
        ids = [o.id for o in orders]
        identity, sell_id = portfolio.id, sell.id
    command.upgrade(config, '0054')
    return config, engine, identity, ids, sell_id


def _stage(session, portfolio, *, key='batch', day=DAY):
    return stage_portfolio_unbound_drawdown_command(session, PortfolioDrawdownCommandRequest(portfolio, day, key), actor_ref='isolated-user')


def test_db_selects_all_results_and_caller_controls_commit(operation_db):
    _, engine, portfolio, orders, sell = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        staged = _stage(session, portfolio)
        assert not staged.replayed and len(staged.results) == 4
        assert {r['outcome'] for r in staged.results} == {'APPLIED', 'NO_STATE_CHANGE'}
        assert not staged.execution_authorized and not staged.continuous_history_known
        assert {r['before_row']['id'] for r in staged.results} == {str(x) for x in orders}
        assert session.scalar(text('SELECT count(*) FROM quant_execution_operation_changes WHERE business_command_id=:id'), {'id': staged.command_id}) == 3
        assert session.scalar(text('SELECT status FROM suggested_orders WHERE id=:id'), {'id': sell}) == 'PROPOSED'
        with engine.connect() as other:
            assert other.scalar(text('SELECT count(*) FROM lifecycle_business_commands WHERE id=:id'), {'id': staged.command_id}) == 0
        session.commit()
    with sessionmaker(bind=engine)() as session:
        replay = _stage(session, portfolio)
        assert replay.replayed and replay.command_id == staged.command_id and replay.results == staged.results
        session.commit()


def test_caller_rollback_removes_all_business_and_history_effects(operation_db):
    _, engine, portfolio, orders, _ = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        staged = _stage(session, portfolio)
        session.rollback()
        assert session.scalar(text('SELECT count(*) FROM lifecycle_business_commands WHERE id=:id'), {'id': staged.command_id}) == 0
        assert session.scalar(text('SELECT status FROM suggested_orders WHERE id=:id'), {'id': orders[0]}) == 'PROPOSED'
        assert session.scalar(text('SELECT count(*) FROM quant_execution_operation_changes WHERE business_command_id=:id'), {'id': staged.command_id}) == 0


def test_same_key_different_original_date_conflicts(operation_db):
    _, engine, portfolio, _, _ = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        _stage(session, portfolio)
        session.commit()
        with pytest.raises(DBAPIError, match='request key conflicts'):
            _stage(session, portfolio, day=date(2026, 9, 25))


@pytest.mark.parametrize('table,sql', [
    ('order', "UPDATE suggested_orders SET revision=revision+1 WHERE id=:id"),
    ('account', "UPDATE portfolios SET version=version+1 WHERE id=:id"),
    ('insert', "INSERT INTO suggested_orders(id,portfolio_id,market,symbol,side,quantity,filled_quantity,limit_price,reason_code,status,revision,reserved_cash,reserved_risk) VALUES(gen_random_uuid(),:id,'CN','000009.SZ','BUY',100,0,10,'TEST','PROPOSED',1,0,0)"),
])
def test_early_constraints_and_cleared_context_cannot_append_writes(operation_db, table, sql):
    _, engine, portfolio, _, sell = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        staged = _stage(session, portfolio)
        session.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        session.execute(text("SELECT set_config('liveprofit.lifecycle_command','',true)"))
        with pytest.raises(DBAPIError, match='sealed|cannot change'), session.begin_nested():
            session.execute(text(sql), {'id': sell if table == 'order' else portfolio})
        session.commit()
        session.rollback()
        assert _stage(session, portfolio).command_id == staged.command_id


def test_concurrent_same_request_commits_once(operation_db):
    _, engine, portfolio, _, _ = _setup(operation_db)
    factory = sessionmaker(bind=engine)

    def run():
        with factory() as session:
            result = _stage(session, portfolio)
            session.commit()
            return result.command_id, result.replayed

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: run(), range(2)))
    assert results[0][0] == results[1][0] and sorted(r[1] for r in results) == [False, True]


def test_bound_portfolio_is_explicitly_closed(operation_db):
    config, engine = operation_db
    portfolio, _, _ = schema_support._seed(engine)
    command.upgrade(config, '0054')
    with sessionmaker(bind=engine)() as session, pytest.raises(DBAPIError, match='lifecycle steps are not connected'):
        _stage(session, portfolio)


def test_downgrade_preserves_facts_and_removes_entry(operation_db):
    config, engine, portfolio, _, _ = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        staged = _stage(session, portfolio)
        session.commit()
    tables = ('lifecycle_business_commands', 'quant_execution_operation_sources', 'quant_execution_operation_changes')

    def frozen_rows():
        with engine.connect() as connection:
            return {table: connection.execute(text(f'SELECT row_to_json(r)::text FROM {table} r ORDER BY id')).scalars().all()
                    for table in tables}

    before = frozen_rows()
    command.downgrade(config, '0053')
    assert frozen_rows() == before
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM lifecycle_business_commands WHERE id=:id'), {'id': staged.command_id}) == 1
        assert connection.scalar(text('SELECT count(*) FROM quant_execution_operation_sources WHERE business_command_id=:id'), {'id': staged.command_id}) == 5
        assert connection.scalar(text("SELECT to_regprocedure('lc_record_portfolio_unbound_drawdown(bytea,text)')")) is None
    command.upgrade(config, '0054')
    assert frozen_rows() == before
    with sessionmaker(bind=engine)() as session:
        replay = _stage(session, portfolio)
        assert replay.replayed and replay.results == staged.results


def test_real_roundtrip_keeps_every_change_in_global_order(operation_db):
    _, engine, portfolio, orders, _ = _setup(operation_db)
    with engine.begin() as connection:
        connection.execute(text("""CREATE FUNCTION test_portfolio_roundtrip() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN IF pg_trigger_depth()=1 AND NEW.status='SUPERSEDED' THEN
            UPDATE suggested_orders SET status='PROPOSED',revision=revision+1 WHERE id=NEW.id;
            UPDATE suggested_orders SET status='SUPERSEDED',revision=revision+1 WHERE id=NEW.id;
          END IF; RETURN NULL; END $$;
          CREATE TRIGGER zz_portfolio_roundtrip AFTER UPDATE ON suggested_orders FOR EACH ROW EXECUTE FUNCTION test_portfolio_roundtrip();"""))
    with sessionmaker(bind=engine)() as session:
        staged = _stage(session, portfolio)
        result = next(r for r in staged.results if r['before_row']['id'] == str(orders[0]))
        assert result['change_count'] == 3 and result['after_row']['revision'] == 4
        assert session.execute(text("SELECT change_seq FROM quant_execution_operation_changes WHERE business_command_id=:id ORDER BY change_seq"),
                               {'id': staged.command_id}).scalars().all() == [1, 2, 3, 4, 5]
        session.commit()


@pytest.mark.parametrize('defect', ['bad_quantity', 'missing_result'])
def test_failure_mid_batch_rolls_back_all_orders_and_history(operation_db, defect):
    _, engine, portfolio, orders, _ = _setup(operation_db)
    with engine.begin() as connection:
        if defect == 'bad_quantity':
            connection.execute(text("""CREATE FUNCTION test_portfolio_bad() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN NEW.quantity:=NEW.quantity+1; RETURN NEW; END $$;
              CREATE TRIGGER zz_portfolio_bad BEFORE UPDATE ON suggested_orders FOR EACH ROW EXECUTE FUNCTION test_portfolio_bad();"""))
        else:
            connection.execute(text("""CREATE FUNCTION test_portfolio_missing() RETURNS trigger LANGUAGE plpgsql AS $$
              BEGIN IF NEW.source_role='ORDER_STATUS_RESULT' AND NEW.source_ordinal=2 THEN RETURN NULL; END IF; RETURN NEW; END $$;
              CREATE TRIGGER zz_portfolio_missing BEFORE INSERT ON quant_execution_operation_sources FOR EACH ROW EXECUTE FUNCTION test_portfolio_missing();"""))
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(DBAPIError, match='permitted fields|result set incomplete'):
            _stage(session, portfolio)
        session.rollback()
        assert session.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='batch'")) == 0
        assert session.scalar(text('SELECT count(*) FROM suggested_orders WHERE id=ANY(:ids) AND revision<>1'), {'ids': orders}) == 0
        assert session.scalar(text('SELECT count(*) FROM quant_execution_operation_changes')) == 0


@pytest.mark.parametrize('defect', ['empty', 'omitted'])
def test_db_refuses_forged_manifest_even_with_recomputed_digest(operation_db, defect):
    _, engine, portfolio, _, _ = _setup(operation_db)
    table = LifecycleBusinessCommand.__table__
    with sessionmaker(bind=engine)() as session:
        staged = _stage(session, portfolio)
        session.commit()
    with engine.begin() as connection:
        head = dict(connection.execute(select(table).where(table.c.id == staged.command_id)).mappings().one())
    head.update(id=uuid.uuid4(), request_key='forged', command_seq=head['command_seq']+1,
                previous_command_id=staged.command_id, previous_manifest_hash=head['manifest_hash'])
    head.pop('recorded_at')
    head['canonical_request'] = PortfolioDrawdownCommandRequest(portfolio, DAY, 'forged').canonical_request()
    head['request_hash'] = hashlib.sha256(head['canonical_request']).hexdigest()
    head['expected_unbound_orders'] = [] if defect == 'empty' else head['expected_unbound_orders'][:-1]
    head['expected_unbound_order_count'] = len(head['expected_unbound_orders'])
    head['manifest_hash'] = _hash({'steps': [], 'unbound_orders': head['expected_unbound_orders']})
    with pytest.raises(DBAPIError, match='manifest shape|participant set'), engine.begin() as connection:
        connection.execute(text("SELECT set_config('liveprofit.lifecycle_command',:id,true)"), {'id': str(head['id'])})
        connection.execute(insert(table).values(**head))


def test_driver_autocommit_cannot_commit_behind_stage_caller(operation_db):
    _, engine, portfolio, orders, _ = _setup(operation_db)
    with sessionmaker(bind=engine.execution_options(isolation_level='AUTOCOMMIT'))() as session, pytest.raises(LifecycleInvalidStateError, match='AUTOCOMMIT'):
        _stage(session, portfolio)
    with engine.connect() as connection:
        assert connection.scalar(text('SELECT revision FROM suggested_orders WHERE id=:id'), {'id': orders[0]}) == 1


@pytest.mark.parametrize('field,value', [('valuation_date', None), ('schema_version', True), ('expected_unbound_orders', [])])
def test_direct_db_entry_rejects_bad_shape_and_client_manifests(operation_db, field, value):
    _, engine, portfolio, _, _ = _setup(operation_db)
    payload = json.loads(PortfolioDrawdownCommandRequest(portfolio, DAY, 'bad-request').canonical_request())
    payload[field] = value
    raw = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()
    with sessionmaker(bind=engine)() as session, pytest.raises(DBAPIError, match='request/actor differs'):
        session.execute(text('SELECT * FROM lc_record_portfolio_unbound_drawdown(:request,:actor)'), {'request': raw, 'actor': 'test'})


@pytest.mark.parametrize('bound', [False, True])
def test_old_order_entries_work_under_new_shared_guards(operation_db, bound):
    config, engine = operation_db
    portfolio, _, orders = schema_support._seed(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE suggested_orders SET status='PROPOSED',lifecycle_id=CASE WHEN :bound THEN lifecycle_id ELSE NULL END WHERE id=:id"),
                           {'id': orders[1], 'bound': bound})
    command.upgrade(config, '0054')
    with sessionmaker(bind=engine)() as session:
        request = OrderStatusCommandRequest(orders[1], 'CANCELLED', 1, 'old-compatible')
        method = stage_bound_order_status_command if bound else stage_unbound_order_status_command
        result = method(session, portfolio, request, actor_ref='test')
        assert result.content.outcome == 'APPLIED'
        session.commit()


def test_application_role_needs_only_controlled_entry_and_cannot_spoof_audit(operation_db):
    _, engine, portfolio, _, _ = _setup(operation_db)
    role = f'lc_portfolio_command_test_{uuid.uuid4().hex[:12]}'
    created = False
    try:
        with engine.begin() as connection:
            assert connection.scalar(text('SELECT current_database()')).startswith('liveprofit_lc_history_0051_test_')
            connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN'))
            created = True
            connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            connection.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))
        with sessionmaker(bind=engine)() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            with pytest.raises(DBAPIError, match='permission denied'):
                _stage(session, portfolio)
            session.rollback()
        with engine.begin() as connection:
            connection.execute(text(f'GRANT EXECUTE ON FUNCTION lc_record_portfolio_unbound_drawdown(bytea,text) TO "{role}"'))
            connection.execute(text(f'GRANT INSERT ON quant_execution_operation_sources TO "{role}"'))
        with sessionmaker(bind=engine)() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            staged = _stage(session, portfolio)
            session.commit()
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:id,true)"), {'id': str(staged.command_id)})
            with pytest.raises(DBAPIError, match='controlled owner function'):
                session.execute(text('INSERT INTO quant_execution_operation_sources SELECT * FROM quant_execution_operation_sources WHERE business_command_id=:id'),
                                {'id': staged.command_id})
            session.rollback()
    finally:
        if created:
            with engine.begin() as connection:
                connection.execute(text(f'DROP OWNED BY "{role}"'))
                connection.execute(text(f'DROP ROLE "{role}"'))


@pytest.mark.parametrize('write', ['flush', 'sql'])
def test_previous_business_write_refuses_new_command(operation_db, write):
    _, engine, portfolio, orders, _ = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        if write == 'flush':
            order = session.get(SuggestedOrder, orders[0])
            order.reason_code = 'PRIOR_WRITE'
            session.flush()
        else:
            session.execute(text('UPDATE portfolios SET version=version+1 WHERE id=:id'), {'id': portfolio})
        with pytest.raises(DBAPIError, match='untouched READ COMMITTED'):
            _stage(session, portfolio)
        session.rollback()
        assert session.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE command_kind='PORTFOLIO_DRAWDOWN'")) == 0


@pytest.mark.parametrize('pause', ['missing', 'resumed', 'future'])
def test_pause_evidence_must_exist_be_active_and_not_future(operation_db, pause):
    _, engine, portfolio, orders, _ = _setup(operation_db)
    with engine.begin() as connection:
        if pause == 'missing':
            # Different owner portfolio has orders but no historical risk event.
            other = uuid.uuid4()
            connection.execute(text("INSERT INTO portfolios(id,name,total_assets,available_cash) VALUES(:id,'no-pause',100000,75000)"), {'id': other})
            connection.execute(text('UPDATE suggested_orders SET portfolio_id=:id WHERE id=ANY(:ids)'), {'id': other, 'ids': orders})
            portfolio = other
        elif pause == 'resumed':
            connection.execute(text("""INSERT INTO quant_portfolio_risk_events
                (id,portfolio_id,revision,kind,facts_as_of,risk_profile,net_asset_value,peak_net_asset_value,
                 drawdown_limit,facts_sha256,reason,reviewed_by,review_signature)
                SELECT gen_random_uuid(),portfolio_id,revision+1,'RESUME',facts_as_of,risk_profile,
                  net_asset_value,peak_net_asset_value,drawdown_limit,facts_sha256,'TEST','isolated-user',repeat('a',64)
                FROM quant_portfolio_risk_events WHERE portfolio_id=:id"""), {'id': portfolio})
    with sessionmaker(bind=engine)() as session, pytest.raises(DBAPIError, match='no rows|active pause required'):
        _stage(session, portfolio, day=date(2026, 9, 23) if pause == 'future' else DAY)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE command_kind='PORTFOLIO_DRAWDOWN'")) == 0
        assert connection.scalar(text('SELECT count(*) FROM suggested_orders WHERE id=ANY(:ids) AND revision<>1'), {'ids': orders}) == 0


def test_full_and_terminal_buys_are_locked_but_excluded(operation_db):
    _, engine, portfolio, orders, _ = _setup(operation_db)
    with engine.begin() as connection:
        connection.execute(text('UPDATE suggested_orders SET filled_quantity=quantity WHERE id=:id'), {'id': orders[1]})
        connection.execute(text("UPDATE suggested_orders SET status='CANCELLED' WHERE id=:id"), {'id': orders[2]})
    with sessionmaker(bind=engine)() as session:
        staged = _stage(session, portfolio)
        assert {r['before_row']['id'] for r in staged.results} == {str(orders[0]), str(orders[3])}
        session.commit()


@pytest.mark.parametrize('case', ['roundtrip', 'role'])
def test_previous_unbound_capture_and_permissions_under_0054(operation_db, monkeypatch, case):
    from tests.backend.quant_strategy.support import (
        lifecycle_unbound_command as previous,
    )

    original_setup = previous._setup

    def setup_at_0054(*args, **kwargs):
        result = original_setup(*args, **kwargs)
        command.upgrade(result[0], '0054')
        return result

    monkeypatch.setattr(previous, '_setup', setup_at_0054)
    check = (previous.assert_every_real_a_b_a_update_is_captured_without_disabling_guards if case == 'roundtrip'
             else previous.assert_application_role_cannot_spoof_context_or_write_audit_facts)
    check(operation_db)


@pytest.mark.parametrize('defect', ['null_count', 'foreign_result', 'bad_row_image'])
def test_result_tampering_with_recomputed_hash_cannot_seal(operation_db, defect):
    _, engine, portfolio, orders, sell = _setup(operation_db)
    expression = {
        'null_count': "NEW.source_snapshot:=jsonb_set(NEW.source_snapshot,'{change_count}','null');",
        'foreign_result': f"NEW.order_id:='{sell}'::uuid;",
        'bad_row_image': "NEW.source_snapshot:=jsonb_set(NEW.source_snapshot,'{after_row,revision}','999');",
    }[defect]
    with engine.begin() as connection:
        connection.execute(text(f"""CREATE FUNCTION test_portfolio_tamper() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN IF NEW.source_type='UNBOUND_ORDER_RESULT' AND NEW.source_ordinal=1 THEN
            {expression}
            NEW.source_content_hash:=public.lc_json_sha(NEW.source_snapshot);
          END IF; RETURN NEW; END $$;
          CREATE TRIGGER zz_portfolio_tamper BEFORE INSERT ON quant_execution_operation_sources
            FOR EACH ROW EXECUTE FUNCTION test_portfolio_tamper();"""))
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(DBAPIError):
            _stage(session, portfolio)
        session.rollback()
        assert session.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE command_kind='PORTFOLIO_DRAWDOWN'")) == 0
        assert session.scalar(text('SELECT count(*) FROM suggested_orders WHERE id=ANY(:ids) AND revision<>1'), {'ids': orders}) == 0


def test_no_lifecycle_portfolio_still_refuses_intent_bound_order(operation_db):
    _, engine, portfolio, orders, _ = _setup(operation_db)
    _, lifecycles, _ = schema_support._seed(engine)
    with sessionmaker(bind=engine)() as session:
        intent = PositionIntent(lifecycle_id=lifecycles[0], trade_date=DAY, target_shares=Decimal(100),
                                reason_code='TEST', state_version=4, status='ACTIVE', revision=1)
        session.add(intent)
        session.flush()
        session.execute(text('UPDATE suggested_orders SET intent_id=:intent WHERE id=:id'), {'intent': intent.id, 'id': orders[0]})
        session.commit()
    with sessionmaker(bind=engine)() as session, pytest.raises(DBAPIError, match='bound orders are not connected'):
        _stage(session, portfolio)


def test_new_key_creates_complete_successor_without_changing_old_replay(operation_db):
    _, engine, portfolio, _, _ = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        first = _stage(session, portfolio)
        session.commit()
        second = _stage(session, portfolio, key='successor')
        assert len(second.results) == 3 and all(r['outcome'] == 'NO_STATE_CHANGE' for r in second.results)
        assert session.scalar(text('SELECT previous_command_id FROM lifecycle_business_commands WHERE id=:id'), {'id': second.command_id}) == first.command_id
        session.commit()
        assert _stage(session, portfolio).results == first.results


def test_saved_context_in_later_transaction_cannot_append_changes(operation_db):
    _, engine, portfolio, orders, _ = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        staged = _stage(session, portfolio)
        session.commit()
        session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:id,true)"), {'id': str(staged.command_id)})
        with pytest.raises(DBAPIError, match='transaction|sealed'):
            session.execute(text('UPDATE suggested_orders SET revision=revision+1 WHERE id=:id'), {'id': orders[0]})
        session.rollback()


def test_entry_freezes_utc_images_even_with_non_utc_session(operation_db):
    _, engine, portfolio, _, _ = _setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        session.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
        staged = _stage(session, portfolio)
        assert all(r['before_row']['created_at'].endswith('+00:00') for r in staged.results)
        session.commit()
        session.execute(text("SET LOCAL TIME ZONE 'Asia/Shanghai'"))
        assert _stage(session, portfolio).results == staged.results
