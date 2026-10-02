# test-catalog-begin
# {
#   "purpose": "量化策略 / lifecycle_bound_command（持仓生命周期、绑定命令、命令）：0053 real bound status capture in per-test random isolated databases.",
#   "keywords": [
#     "量化策略",
#     "绑定",
#     "并发",
#     "历史审计",
#     "持仓生命周期",
#     "来源观测",
#     "重放",
#     "权限角色",
#     "事务回滚",
#     "lifecycle_bound_command",
#     "bound",
#     "concurrent",
#     "history",
#     "lifecycle",
#     "observation",
#     "replay",
#     "role",
#     "rollback"
#   ],
#   "covers": [
#     "backend/modules/investment_workspace/infrastructure/models.py",
#     "backend/modules/quant_strategy/application/lifecycle_bound_command.py",
#     "backend/modules/quant_strategy/application/lifecycle_command_selection.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_bound_replay.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_command_replay.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_history_repository.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_models.py",
#     "backend/modules/quant_strategy/infrastructure/lifecycle_operation_models.py"
#   ],
#   "environment": [
#     "db"
#   ]
# }
# test-catalog-end

"""0053 real bound status capture in per-test random isolated databases."""
import json
import uuid
from copy import deepcopy
from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from backend.modules.investment_workspace.infrastructure.models import PortfolioPosition
from backend.modules.quant_strategy.application.lifecycle_bound_command import (
    stage_bound_order_status_command,
)
from backend.modules.quant_strategy.application.lifecycle_command_selection import (
    OrderStatusCommandRequest,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_bound_replay import (
    _verify_bound,
    load_bound_order_status_replay,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_command_replay import (
    _change_payload,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_history_repository import (
    LifecycleHistoryIntegrityError,
    _canonical,
    _hash,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_models import (
    PositionIntent,
)
from backend.modules.quant_strategy.infrastructure.lifecycle_operation_models import (
    LifecycleBusinessCommand,
    PositionLifecycleOperation,
    QuantExecutionOperationChange,
    QuantExecutionOperationSource,
)
import tests.backend.quant_strategy.support.lifecycle_operation_schema as schema_tests
import tests.backend.quant_strategy.support.lifecycle_unbound_command as unbound_tests

operation_db = schema_tests.operation_db


def _setup(operation_db, *, status="PROPOSED", side="SELL", children=False):
    config, engine = operation_db
    portfolio, lifecycles, orders = schema_tests._seed(engine)
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
        connection.execute(text("UPDATE suggested_orders SET status=:status,side=:side WHERE id=:id"),
                           {"status":status,"side":side,"id":orders[1]})
    if children:
        with sessionmaker(bind=engine)() as session:
            session.add_all((PortfolioPosition(portfolio_id=portfolio,market="CN",symbol="000001.SZ",
                quantity=Decimal(200),average_cost=Decimal(10)),PositionIntent(lifecycle_id=lifecycles[0],
                trade_date=date(2026,9,21),target_shares=Decimal(100),reason_code="TEST",state_version=4,status="ACTIVE",revision=1)))
            session.commit()
    command.upgrade(config,"0053")
    return config,engine,portfolio,lifecycles[0],orders[1]


def test_application_role_has_only_entry_capability_and_cannot_spoof_audit(operation_db):
    _,engine,portfolio,_,order=_setup(operation_db)
    role=f"lc_bound_test_{uuid.uuid4().hex[:12]}"
    created=False
    factory=sessionmaker(bind=engine)
    try:
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
            connection.execute(text(f'CREATE ROLE "{role}" NOLOGIN')); created=True
            connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{role}"'))
            connection.execute(text(f'GRANT SELECT ON ALL TABLES IN SCHEMA public TO "{role}"'))
        with factory() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            with pytest.raises(DBAPIError,match="permission denied"):_stage(session,portfolio,order)
            session.rollback()
        with engine.begin() as connection:
            connection.execute(text(f'GRANT EXECUTE ON FUNCTION lc_record_bound_order_status(uuid,bytea,text) TO "{role}"'))
            connection.execute(text(f'GRANT INSERT ON position_lifecycle_operations,quant_execution_operation_sources TO "{role}"'))
            connection.execute(text(f'GRANT UPDATE ON suggested_orders TO "{role}"'))
        with factory() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            staged=_stage(session,portfolio,order); session.commit()
        for table in ("position_lifecycle_operations","quant_execution_operation_sources"):
            with factory() as session:
                session.execute(text(f'SET LOCAL ROLE "{role}"'))
                session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:id,true)"),{"id":str(staged.command_id)})
                with pytest.raises(DBAPIError,match="controlled owner function"):
                    session.execute(text(f"INSERT INTO {table} SELECT * FROM {table} WHERE business_command_id=:id"),{"id":staged.command_id})
                session.rollback()
        with factory() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            with pytest.raises(DBAPIError,match="permission denied"):
                session.execute(text("SELECT lc_bound_validate(:id,false)"),{"id":staged.command_id})
            session.rollback()
        with factory() as session:
            session.execute(text(f'SET LOCAL ROLE "{role}"'))
            session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:id,true)"),{"id":str(staged.command_id)})
            with pytest.raises(DBAPIError,match="scope/transaction"):
                session.execute(text("UPDATE suggested_orders SET revision=revision+1 WHERE id=:id"),{"id":order})
            session.rollback()
    finally:
        if created:
            with engine.begin() as connection:
                connection.execute(text(f'DROP OWNED BY "{role}"')); connection.execute(text(f'DROP ROLE "{role}"'))


@pytest.mark.parametrize("kind",["source","operation","change"])
def test_owner_cannot_append_to_sealed_operation_in_same_or_later_transaction(operation_db,kind):
    _,engine,portfolio,_,order=_setup(operation_db)
    factory=sessionmaker(bind=engine)
    tables={"source":"quant_execution_operation_sources","operation":"position_lifecycle_operations","change":"quant_execution_operation_changes"}
    with factory() as session:
        staged=_stage(session,portfolio,order)
        for iteration in (0,1):
            savepoint=session.begin_nested()
            session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:id,true)"),{"id":str(staged.command_id)})
            with pytest.raises(DBAPIError,match="sealed|transaction differs"):
                table=tables[kind]
                session.execute(text(f"INSERT INTO {table} SELECT * FROM {table} WHERE business_command_id=:id"),{"id":staged.command_id})
            savepoint.rollback()
            if iteration==0: session.commit()
        session.rollback()


def test_missing_operation_head_fails_at_commit(operation_db):
    _,engine,portfolio,_,order=_setup(operation_db)
    factory=sessionmaker(bind=engine)
    with factory() as session:
        staged=_stage(session,portfolio,order); session.commit()
    table=LifecycleBusinessCommand.__table__
    with pytest.raises(DBAPIError,match="operation/input set"), factory.begin() as session:
        original=dict(session.execute(select(table).where(table.c.id==staged.command_id)).mappings().one())
        previous_id=original["id"]
        original.update(id=uuid.uuid4(),request_key="unsealed",command_seq=original["command_seq"]+1,
                        previous_command_id=previous_id,previous_manifest_hash=original["manifest_hash"])
        request=OrderStatusCommandRequest(order,"RECONCILIATION_REQUIRED",2,"unsealed")
        original.update(canonical_request=request.canonical_request(),request_hash=_hash(json.loads(request.canonical_request())))
        original["expected_steps"]=deepcopy(original["expected_steps"])
        original["expected_steps"][0]["operation_id"]=str(uuid.uuid4())
        original["manifest_hash"]=_hash({"steps":original["expected_steps"],"unbound_orders":[]})
        session.execute(text("SELECT set_config('liveprofit.lifecycle_command',:id,true)"),{"id":str(original["id"])})
        session.execute(insert(table).values(original))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='unsealed'"))==0


@pytest.mark.parametrize("mutation",["truncated","manifest_bool","result","missing_change","unrelated_field","owner","before_state",
    "previous_orders","previous_trailing","previous_expectations","previous_managed","bound_previous_orders","bound_previous_managed"])
def test_content_reader_refuses_rehashed_broken_facts_without_modifying_database(operation_db,mutation):
    _,engine,portfolio,_,order=_setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        request=OrderStatusCommandRequest(order,"CANCELLED",1,"bound-test")
        if mutation.startswith("bound_previous"):
            _stage(session,portfolio,order); session.commit()
            request=OrderStatusCommandRequest(order,"RECONCILIATION_REQUIRED",2,"bound-next")
        staged=stage_bound_order_status_command(session,portfolio,request,actor_ref="test")
        def records(model):
            table=model.__table__
            identity=table.c.id if model is LifecycleBusinessCommand else table.c.business_command_id
            return [dict(row) for row in session.execute(select(table).where(identity==staged.command_id)).mappings()]
        head=records(LifecycleBusinessCommand)[0]; operation=records(PositionLifecycleOperation)[0]
        sources=records(QuantExecutionOperationSource); changes=records(QuantExecutionOperationChange)
        table=PositionLifecycleOperation.__table__
        previous=dict(session.execute(select(table).where(table.c.id==operation["previous_operation_id"])).mappings().one())
        if "previous" in mutation:
            if mutation.endswith("managed"):
                previous["after_snapshot"]["managed_state"]["lifecycle"]["target_shares"]="999.0000"
            elif mutation.startswith("bound_previous"):
                previous["after_snapshot"]["evidence"]["rows"]["active_orders"]=[]
            else:
                name={"previous_orders":"suggested_orders","previous_trailing":"position_trailing_stops",
                      "previous_expectations":"position_expectations"}[mutation]
                previous["after_snapshot"]["evidence"]["migration_rows"][name]=[]
            previous["after_hash"]=_hash(previous["after_snapshot"])
            payload={k:v for k,v in previous.items() if k not in {"recorded_at","canonical_payload","operation_hash"}}
            previous.update(canonical_payload=_canonical(payload),operation_hash=_hash(payload))
            operation["previous_hash"]=previous["operation_hash"]
        elif mutation=="truncated":
            operation["after_snapshot"]["evidence"]["rows"]["active_orders"][0].pop("reserved_risk")
        elif mutation=="manifest_bool":
            head["expected_steps"][0]["command_step_no"]=True
            head["manifest_hash"]=_hash({"steps":head["expected_steps"],"unbound_orders":[]})
        elif mutation=="result":operation["after_snapshot"]["evidence"]["command_result"]["reason_code"]="FAKE"
        elif mutation=="missing_change":
            changes=[]; operation["change_count"]=0; operation["changes_sha256"]=_hash([])
        elif mutation=="unrelated_field":
            raw=json.loads(changes[0]["after_row"]["__raw_row_json__"]); raw["quantity"]=201.0
            changes[0]["after_row"]["quantity"]="201.0";changes[0]["after_row"]["__raw_row_json__"]=json.dumps(raw)
            operation["changes_sha256"]=_hash(_change_payload(changes))
        elif mutation=="owner":
            raw=operation["before_snapshot"]["evidence"]["rows"]["lifecycle"]
            raw["strategy_version_id"]=str(uuid.uuid4())
            parsed=json.loads(raw["__raw_row_json__"]); parsed["strategy_version_id"]=raw["strategy_version_id"]
            raw["__raw_row_json__"]=json.dumps(parsed)
        else:operation["before_snapshot"]["managed_state"]["lifecycle"]["target_shares"]="999.0000"
        operation["before_hash"]=_hash(operation["before_snapshot"]);operation["after_hash"]=_hash(operation["after_snapshot"])
        payload={k:v for k,v in operation.items() if k not in {"recorded_at","canonical_payload","operation_hash"}}
        operation.update(canonical_payload=_canonical(payload),operation_hash=_hash(payload))
        with pytest.raises(LifecycleHistoryIntegrityError):
            _verify_bound(head,request,operation,previous,sources,changes)
        session.rollback()


def _stage(session,portfolio,order,status="CANCELLED",revision=1,key="bound-test"):
    return stage_bound_order_status_command(session,portfolio,OrderStatusCommandRequest(order,status,revision,key),actor_ref="test")


@pytest.mark.parametrize("side,before,requested,outcome,count",[
    ("SELL","PROPOSED","CANCELLED","APPLIED",1),
    ("SELL","RECONCILIATION_REQUIRED","RECONCILIATION_REQUIRED","NO_STATE_CHANGE",0),
    ("SELL","EXECUTING","CANCELLED","BLOCKED",0),
    ("BUY","PROPOSED","EXECUTING","BLOCKED",0),
])
def test_bound_results_real_capture_and_caller_commit(operation_db,side,before,requested,outcome,count):
    _,engine,portfolio,lifecycle,order = _setup(operation_db,status=before,side=side)
    request=OrderStatusCommandRequest(order,requested,1,"真实结果-中文")
    with sessionmaker(bind=engine)() as session:
        staged=stage_bound_order_status_command(session,portfolio,request,actor_ref="隔离验收")
        result=staged.content
        assert result.outcome==outcome and result.change_count==count and result.operation_seq==2 and result.state_version==4
        assert not result.database_sealing_verified and not result.continuous_history_known and not result.execution_authorized
        with engine.connect() as observer:
            assert observer.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key=:key"),{"key":request.request_key})==0
            assert observer.scalar(text("SELECT status FROM suggested_orders WHERE id=:id"),{"id":order})==before
        session.commit()
    with sessionmaker(bind=engine)() as session:
        assert load_bound_order_status_replay(session,portfolio,request)==result
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT state_version FROM position_lifecycle_states WHERE id=:id"),{"id":lifecycle})==4


def test_true_a_b_a_preserves_all_intermediate_images(operation_db):
    _,engine,portfolio,_,order=_setup(operation_db)
    with engine.begin() as connection:
        connection.execute(text('''CREATE FUNCTION test_bound_roundtrip() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN IF pg_trigger_depth()=1 AND NEW.status='CANCELLED' THEN
            UPDATE suggested_orders SET status='PROPOSED',revision=revision+1 WHERE id=NEW.id;
            UPDATE suggested_orders SET status='CANCELLED',revision=revision+1 WHERE id=NEW.id;
          END IF; RETURN NULL; END $$;
          CREATE TRIGGER zz_test_roundtrip AFTER UPDATE ON suggested_orders
            FOR EACH ROW EXECUTE FUNCTION test_bound_roundtrip();'''))
    with sessionmaker(bind=engine)() as session:
        staged=_stage(session,portfolio,order)
        assert staged.content.change_count==3 and staged.content.result_revision==4
        session.commit()


def test_terminal_member_survives_next_operation_and_replay_ignores_later_projection(operation_db):
    _,engine,portfolio,_,order=_setup(operation_db)
    factory=sessionmaker(bind=engine)
    with factory() as session:
        first=_stage(session,portfolio,order)
        session.commit()
    with factory() as session:
        second=_stage(session,portfolio,order,"RECONCILIATION_REQUIRED",2,"next")
        assert second.content.outcome=="BLOCKED" and second.content.operation_seq==3
        session.commit()
    with engine.begin() as connection:
        connection.execute(text("UPDATE suggested_orders SET status='RECONCILIATION_REQUIRED',revision=9 WHERE id=:id"),{"id":order})
    with factory() as session:
        assert _stage(session,portfolio,order).command_id==first.command_id
        session.rollback()
    with factory() as session, pytest.raises(DBAPIError,match="request key conflicts"):
        _stage(session,portfolio,order,"EXECUTING")


@pytest.mark.parametrize("mutation",["lifecycle","order","new_member","missing_predecessor"])
def test_unrecorded_history_is_refused_instead_of_repaired(operation_db,mutation):
    _config,engine,portfolio,lifecycle,order=_setup(operation_db)
    with engine.begin() as connection:
        if mutation=="lifecycle":
            connection.execute(text("UPDATE position_lifecycle_states SET target_shares=target_shares+1 WHERE id=:id"),{"id":lifecycle})
        elif mutation=="order":
            connection.execute(text("UPDATE suggested_orders SET reserved_cash=reserved_cash+1 WHERE id=:id"),{"id":order})
        elif mutation=="new_member":
            values=connection.execute(text("SELECT to_jsonb(o) FROM suggested_orders o WHERE id=:id"),{"id":order}).scalar_one()
            values["id"]=str(uuid.uuid4())
            connection.execute(text("INSERT INTO suggested_orders SELECT (jsonb_populate_record(NULL::suggested_orders,CAST(:row AS jsonb))).*"),
                               {"row":json.dumps(values)})
        else:
            # Brand-new lifecycle/order after the migration have no operation
            # predecessor. Use clone columns by JSON record to avoid DDL.
            values=connection.execute(text("SELECT to_jsonb(l) FROM position_lifecycle_states l WHERE id=:id"),{"id":lifecycle}).scalar_one()
            values["id"]=str(uuid.uuid4()); values["symbol"]="000002.SZ"
            connection.execute(text("INSERT INTO position_lifecycle_states SELECT (jsonb_populate_record(NULL::position_lifecycle_states,CAST(:row AS jsonb))).*"),{"row":json.dumps(values)})
            connection.execute(text("UPDATE suggested_orders SET lifecycle_id=:new,symbol='000002.SZ' WHERE id=:id"),{"new":values["id"],"id":order})
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(DBAPIError): _stage(session,portfolio,order)
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='bound-test'"))==0


@pytest.mark.parametrize("mutation",["quantity","revision"])
def test_bad_trigger_rolls_back_all_business_and_history(operation_db,mutation):
    _,engine,portfolio,_,order=_setup(operation_db)
    with engine.begin() as connection:
        connection.execute(text(f'''CREATE FUNCTION test_bound_bad() RETURNS trigger LANGUAGE plpgsql AS $$
          BEGIN NEW.{mutation}:=NEW.{mutation}+1; RETURN NEW; END $$;
          CREATE TRIGGER test_bound_bad BEFORE UPDATE ON suggested_orders FOR EACH ROW EXECUTE FUNCTION test_bound_bad();'''))
    with sessionmaker(bind=engine)() as session:
        with pytest.raises(DBAPIError,match="permitted fields"):_stage(session,portfolio,order)
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT revision FROM suggested_orders WHERE id=:id"),{"id":order})==1
        assert connection.scalar(text("SELECT count(*) FROM quant_execution_operation_changes"))==0
        assert connection.scalar(text("SELECT count(*) FROM position_lifecycle_operations"))==2


def test_rollback_and_concurrent_same_key_replay(operation_db):
    _,engine,portfolio,_,order=_setup(operation_db)
    factory=sessionmaker(bind=engine)
    with factory() as session:
        rolled=_stage(session,portfolio,order)
        session.rollback()
    with factory() as first,factory() as second:
        applied=_stage(first,portfolio,order)
        assert applied.command_id!=rolled.command_id
        second.execute(text("SET LOCAL lock_timeout='100ms'"))
        with pytest.raises(DBAPIError,match="lock timeout"):_stage(second,portfolio,order)
        second.rollback(); first.commit()
        replay=_stage(second,portfolio,order)
        assert replay.replayed and replay.command_id==applied.command_id
        second.commit()


@pytest.mark.parametrize("table",["suggested_orders","position_lifecycle_states","position_trailing_stops",
                                  "position_expectations","position_intents","position_daily_facts","portfolio_positions"])
def test_sealed_transaction_cannot_write_other_managed_state_after_early_check(operation_db,table):
    _,engine,portfolio,lifecycle,order=_setup(operation_db)
    with sessionmaker(bind=engine)() as session:
        staged=_stage(session,portfolio,order)
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        # Empty tables still exercise statement TRUNCATE guards. Existing rows
        # exercise actual UPDATE; savepoint rollback retains the valid command.
        nested=session.begin_nested()
        with pytest.raises(DBAPIError,match="status command|order command"):
            if table in {"position_intents","position_daily_facts","portfolio_positions"}:
                session.execute(text(f"TRUNCATE {table} CASCADE"))
            else:
                where="id=:id" if table=="suggested_orders" else ("id=:lifecycle" if table=="position_lifecycle_states" else "lifecycle_id=:lifecycle")
                session.execute(text(f"UPDATE {table} SET updated_at=clock_timestamp() WHERE {where}"),{"id":order,"lifecycle":lifecycle})
        nested.rollback(); session.commit()
    with sessionmaker(bind=engine)() as session:
        assert load_bound_order_status_replay(session,portfolio,OrderStatusCommandRequest(order,"CANCELLED",1,"bound-test")).command_id==staged.command_id


def test_timezones_and_downgrade_preserve_existing_bound_facts(operation_db):
    config,engine,portfolio,_,order=_setup(operation_db)
    factory=sessionmaker(bind=engine)
    with factory() as session:
        session.execute(text("SET LOCAL TIME ZONE 'America/New_York'"))
        staged=_stage(session,portfolio,order)
        session.execute(text("SET LOCAL TIME ZONE 'Asia/Shanghai'")); session.commit()
    command.downgrade(config,"0052")
    with factory() as session:
        session.execute(text("SET LOCAL TIME ZONE 'Pacific/Auckland'"))
        assert load_bound_order_status_replay(session,portfolio,OrderStatusCommandRequest(order,"CANCELLED",1,"bound-test")).command_id==staged.command_id
    command.upgrade(config,"0053")


@pytest.mark.parametrize("case",["applied","unchanged","blocked","buy","roundtrip","role"])
def test_0052_entry_remains_compatible_under_0053_guards(operation_db,monkeypatch,case):
    original=unbound_tests._setup
    def under_head(fixture,**kwargs):
        result=original(fixture,**kwargs)
        command.upgrade(result[0],"0053")
        return result
    monkeypatch.setattr(unbound_tests,"_setup",under_head)
    if case=="roundtrip":
        unbound_tests.assert_every_real_a_b_a_update_is_captured_without_disabling_guards(operation_db)
    elif case=="role":
        unbound_tests.assert_application_role_cannot_spoof_context_or_write_audit_facts(operation_db)
    else:
        parameters={"applied":("PROPOSED","SELL","CANCELLED","APPLIED",1),
                    "unchanged":("RECONCILIATION_REQUIRED","SELL","RECONCILIATION_REQUIRED","NO_STATE_CHANGE",0),
                    "blocked":("EXECUTING","SELL","CANCELLED","BLOCKED",0),
                    "buy":("PROPOSED","BUY","EXECUTING","BLOCKED",0)}
        unbound_tests.assert_stage_captures_real_result_and_caller_owns_commit(operation_db,*parameters[case])


@pytest.mark.parametrize("kind,damage",[("baseline","orders"),("baseline","trailing"),("baseline","expectations"),
                                      ("baseline","managed"),("bound","orders"),("bound","managed")])
def test_sql_refuses_rehashed_corrupt_immediate_predecessor_before_writes(operation_db,kind,damage):
    _,engine,portfolio,lifecycle,order=_setup(operation_db)
    table=PositionLifecycleOperation.__table__
    factory=sessionmaker(bind=engine)
    revision=1
    if kind=="bound":
        with factory() as session:
            _stage(session,portfolio,order); session.commit()
        revision=2
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT current_database()")).startswith("liveprofit_lc_history_0051_test_")
        row=dict(connection.execute(select(table).where(table.c.lifecycle_id==lifecycle).order_by(table.c.operation_seq.desc()).limit(1)).mappings().one())
        snapshot=deepcopy(row["after_snapshot"])
        if damage=="managed":snapshot["managed_state"]["lifecycle"]["target_shares"]="999.0000"
        elif kind=="bound":snapshot["evidence"]["rows"]["active_orders"]=[]
        else:
            name={"orders":"suggested_orders","trailing":"position_trailing_stops","expectations":"position_expectations"}[damage]
            snapshot["evidence"]["migration_rows"][name]=[]
        row.update(after_snapshot=snapshot,after_hash=_hash(snapshot))
        payload={key:value for key,value in row.items() if key not in {"recorded_at","canonical_payload","operation_hash"}}
        values={"after_snapshot":snapshot,"after_hash":row["after_hash"],"canonical_payload":_canonical(payload),"operation_hash":_hash(payload)}
        # Synthetic corruption only: after rechecking this random DB, its trusted
        # owner temporarily bypasses UPDATE protection. Real positive captures
        # never disable guards. Restore before exercising the production code.
        connection.execute(text("ALTER TABLE position_lifecycle_operations DISABLE TRIGGER lc_history_immutable"))
        connection.execute(table.update().where(table.c.id==row["id"]).values(values))
        connection.execute(text("ALTER TABLE position_lifecycle_operations ENABLE TRIGGER lc_history_immutable"))
    with factory() as session:
        with pytest.raises(DBAPIError,match="predecessor.*state differs"):
            _stage(session,portfolio,order,"RECONCILIATION_REQUIRED",revision,"after-corruption")
        session.rollback()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM lifecycle_business_commands WHERE request_key='after-corruption'"))==0
        assert connection.scalar(text("SELECT revision FROM suggested_orders WHERE id=:id"),{"id":order})==revision


@pytest.mark.parametrize("table",["position_trailing_stops","position_expectations","position_intents","portfolio_positions"])
def test_fixed_child_and_account_observation_locks_last_until_caller_commit(operation_db,table):
    _,engine,portfolio,lifecycle,order=_setup(operation_db,children=True)
    with sessionmaker(bind=engine)() as session:
        staged=_stage(session,portfolio,order)
        with engine.connect() as other:
            other.execute(text("SET LOCAL lock_timeout='100ms'"))
            where="portfolio_id=:portfolio" if table=="portfolio_positions" else "lifecycle_id=:lifecycle"
            with pytest.raises(DBAPIError,match="lock timeout"):
                other.execute(text(f"UPDATE {table} SET updated_at=clock_timestamp() WHERE {where}"),{"portfolio":portfolio,"lifecycle":lifecycle})
            other.rollback()
        session.commit()
    with sessionmaker(bind=engine)() as session:
        assert load_bound_order_status_replay(session,portfolio,OrderStatusCommandRequest(order,"CANCELLED",1,"bound-test")).command_id==staged.command_id
