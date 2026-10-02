"""DB-selected multi-order protective quarantine; lifecycle steps remain closed."""
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None

SQL = r'''
CREATE FUNCTION lc_portfolio_manifest(portfolio uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog,public AS $$
 SELECT coalesce(jsonb_agg(jsonb_build_object('order_id',o.id::text,'original_revision',o.revision,
   'allowed_results',jsonb_build_array('APPLIED','NO_STATE_CHANGE','BLOCKED')) ORDER BY o.id),'[]'::jsonb)
 FROM public.suggested_orders o WHERE o.portfolio_id=portfolio AND o.side='BUY'
 AND o.status IN ('PROPOSED','EXECUTING','PARTIALLY_FILLED','RECONCILIATION_REQUIRED') AND o.quantity>o.filled_quantity
$$;

CREATE FUNCTION lc_portfolio_check_head(h public.lifecycle_business_commands) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE req jsonb; expected jsonb;
BEGIN
 req:=convert_from(h.canonical_request,'UTF8')::jsonb;
 expected:=jsonb_build_object('schema_version',1,'command_kind','PORTFOLIO_DRAWDOWN','portfolio_id',h.portfolio_id::text,
   'valuation_date',(req->>'valuation_date')::date::text,'request_key',h.request_key);
 IF jsonb_typeof(req->'valuation_date') IS DISTINCT FROM 'string' OR
   h.selector_version<>'portfolio-unbound-drawdown:v1' OR h.request_schema_version<>1 OR
   h.expected_steps<>'[]'::jsonb OR h.expected_step_count<>0 OR h.expected_unbound_order_count<1 OR
   h.expected_unbound_order_count<>jsonb_array_length(h.expected_unbound_orders) OR
   h.canonical_request IS DISTINCT FROM convert_to(public.lc_json_canonical(expected),'UTF8') OR
   h.request_hash IS DISTINCT FROM encode(sha256(h.canonical_request),'hex') OR
   h.manifest_hash IS DISTINCT FROM public.lc_json_sha(jsonb_build_object('steps','[]'::jsonb,'unbound_orders',h.expected_unbound_orders)) THEN
   RAISE EXCEPTION 'portfolio request/manifest shape differs'; END IF;
END $$;

CREATE FUNCTION lc_portfolio_order_changes(cmd uuid,order_id uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog,public AS $$
 SELECT coalesce(jsonb_agg(value ORDER BY (value->>'change_seq')::bigint),'[]'::jsonb)
 FROM jsonb_array_elements(public.lc_changes_payload(cmd)) a(value) WHERE value->>'row_id'=order_id::text
$$;

CREATE FUNCTION lc_portfolio_result_validate(cmd uuid,member jsonb,ordinal bigint,check_projection boolean) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,public SET TimeZone='UTC' SET DateStyle='ISO,YMD' AS $$
DECLARE h public.lifecycle_business_commands%ROWTYPE; s public.quant_execution_operation_sources%ROWTYPE;
 result jsonb; current_image jsonb; c record; seq bigint:=0; requested text; expected_outcome text; expected_reason text; raw text; identity uuid;
BEGIN
 SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
 identity:=(member->>'order_id')::uuid;
 SELECT * INTO STRICT s FROM public.quant_execution_operation_sources WHERE business_command_id=cmd AND order_id=identity AND source_type='UNBOUND_ORDER_RESULT';
 result:=s.source_snapshot;
 IF s.operation_id IS NOT NULL OR s.scope<>'COMMAND' OR s.source_role<>'ORDER_STATUS_RESULT' OR s.source_ordinal<>ordinal OR
   s.result_schema_version<>1 OR jsonb_typeof(result) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(result))<>8 OR
   NOT result ?& ARRAY['schema_version','outcome','reason_code','before_row','after_row','missing_sources','change_count','changes_sha256'] OR
   result->'schema_version' IS DISTINCT FROM '1'::jsonb OR result->>'outcome' IS DISTINCT FROM s.result_outcome OR
   result->>'reason_code' IS DISTINCT FROM s.result_reason_code OR result->'missing_sources' IS DISTINCT FROM '[]'::jsonb OR
   s.source_content_hash IS DISTINCT FROM public.lc_json_sha(result) THEN RAISE EXCEPTION 'portfolio result schema/digest differs'; END IF;
 PERFORM public.lc_check_order_image(result->'before_row',h.portfolio_id,identity);
 PERFORM public.lc_check_order_image(result->'after_row',h.portfolio_id,identity);
 IF member IS DISTINCT FROM jsonb_build_object('order_id',identity::text,'original_revision',(result#>>'{before_row,revision}')::integer,
   'allowed_results',jsonb_build_array('APPLIED','NO_STATE_CHANGE','BLOCKED')) OR result#>>'{before_row,side}'<>'BUY' OR
   result#>>'{before_row,status}' NOT IN ('PROPOSED','EXECUTING','PARTIALLY_FILLED','RECONCILIATION_REQUIRED') OR
   (result#>>'{before_row,quantity}')::numeric <= (result#>>'{before_row,filled_quantity}')::numeric THEN
   RAISE EXCEPTION 'portfolio before/result participant differs'; END IF;
 requested:=CASE WHEN result#>>'{before_row,status}'='PROPOSED' AND (result#>>'{before_row,filled_quantity}')::numeric=0
   THEN 'SUPERSEDED' ELSE 'RECONCILIATION_REQUIRED' END;
 expected_outcome:=CASE WHEN result#>>'{before_row,status}'=requested THEN 'NO_STATE_CHANGE' ELSE 'APPLIED' END;
 expected_reason:=CASE WHEN expected_outcome='APPLIED' THEN 'ORDER_STATUS_APPLIED' ELSE 'ALREADY_IN_REQUESTED_STATE' END;
 current_image:=result->'before_row';
 FOR c IN SELECT * FROM public.quant_execution_operation_changes WHERE business_command_id=cmd AND row_id=identity ORDER BY change_seq LOOP
   seq:=seq+1;
   IF c.operation_id IS NOT NULL OR c.scope<>'COMMAND' OR c.entity_type<>'ORDER' OR c.change_kind<>'UPDATE' OR c.binding_side<>'NONE' THEN
     RAISE EXCEPTION 'portfolio change scope differs'; END IF;
   PERFORM public.lc_check_order_image(c.before_row,h.portfolio_id,identity);
   PERFORM public.lc_check_order_image(c.after_row,h.portfolio_id,identity);
   IF c.before_row IS DISTINCT FROM current_image OR (c.after_row->>'revision')::integer<>(c.before_row->>'revision')::integer+1 OR
     (c.before_row-ARRAY['__raw_row_json__','status','revision','updated_at']) IS DISTINCT FROM
     (c.after_row-ARRAY['__raw_row_json__','status','revision','updated_at']) THEN RAISE EXCEPTION 'portfolio row chain/permitted fields differs'; END IF;
   current_image:=c.after_row;
 END LOOP;
 IF current_image IS DISTINCT FROM result->'after_row' OR s.source_revision<>(result#>>'{after_row,revision}')::integer OR
   result->'change_count' IS DISTINCT FROM to_jsonb(seq) OR
   result->>'changes_sha256' IS DISTINCT FROM public.lc_json_sha(public.lc_portfolio_order_changes(cmd,identity)) OR
   s.result_outcome<>expected_outcome OR s.result_reason_code<>expected_reason OR result#>>'{after_row,status}'<>requested OR
   (expected_outcome='NO_STATE_CHANGE' AND (seq<>0 OR result->'before_row' IS DISTINCT FROM result->'after_row')) OR
   (expected_outcome='APPLIED' AND seq=0) THEN RAISE EXCEPTION 'portfolio result effect differs'; END IF;
 IF check_projection THEN
   SELECT row_to_json(o)::text INTO STRICT raw FROM public.suggested_orders o WHERE id=identity;
   IF public.lc_order_image(raw) IS DISTINCT FROM result->'after_row' THEN RAISE EXCEPTION 'portfolio sealed projection differs'; END IF;
 END IF;
END $$;

CREATE FUNCTION lc_portfolio_validate(cmd uuid,check_projection boolean) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,public SET TimeZone='UTC' SET DateStyle='ISO,YMD' AS $$
DECLARE h public.lifecycle_business_commands%ROWTYPE; prior public.lifecycle_business_commands%ROWTYPE;
 pause public.quant_execution_operation_sources%ROWTYPE; m record; seq bigint:=0; c record; raw text;
BEGIN
 SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
 PERFORM public.lc_portfolio_check_head(h);
 IF h.command_kind<>'PORTFOLIO_DRAWDOWN' OR (check_projection AND h.captured_transaction_id<>pg_current_xact_id()::text) OR
   EXISTS(SELECT 1 FROM public.position_lifecycle_operations WHERE business_command_id=cmd) OR
   EXISTS(SELECT 1 FROM public.suggested_order_initial_inputs WHERE business_command_id=cmd) OR
   EXISTS(SELECT 1 FROM public.position_lifecycle_initial_inputs WHERE business_command_id=cmd) THEN RAISE EXCEPTION 'portfolio command transaction/steps differs'; END IF;
 IF h.command_seq>1 THEN
   SELECT * INTO STRICT prior FROM public.lifecycle_business_commands WHERE id=h.previous_command_id;
   IF prior.portfolio_id<>h.portfolio_id OR prior.command_seq+1<>h.command_seq OR h.previous_manifest_hash<>prior.manifest_hash OR
     prior.manifest_hash<>public.lc_json_sha(jsonb_build_object('steps',prior.expected_steps,'unbound_orders',prior.expected_unbound_orders)) THEN
     RAISE EXCEPTION 'portfolio predecessor differs'; END IF;
 END IF;
 SELECT * INTO STRICT pause FROM public.quant_execution_operation_sources WHERE business_command_id=cmd AND source_role='RISK_PAUSE_INPUT';
 SELECT row_to_json(r)::text INTO STRICT raw FROM public.quant_portfolio_risk_events r WHERE id=pause.risk_event_id;
 IF pause.source_type<>'RISK_EVENT' OR pause.source_ordinal<>1 OR pause.operation_id IS NOT NULL OR pause.scope<>'COMMAND' OR
   pause.source_snapshot IS DISTINCT FROM public.lc_bound_image(raw) OR pause.source_content_hash<>public.lc_json_sha(pause.source_snapshot) OR
   pause.source_snapshot->>'kind'<>'PAUSE' OR pause.source_snapshot->>'portfolio_id'<>h.portfolio_id::text OR
   (pause.source_snapshot->>'facts_as_of')::date>(convert_from(h.canonical_request,'UTF8')::jsonb->>'valuation_date')::date THEN
   RAISE EXCEPTION 'portfolio pause evidence differs'; END IF;
 IF check_projection AND pause.risk_event_id IS DISTINCT FROM (SELECT id FROM public.quant_portfolio_risk_events WHERE portfolio_id=h.portfolio_id ORDER BY revision DESC LIMIT 1) THEN
   RAISE EXCEPTION 'portfolio active pause changed'; END IF;
 IF (SELECT count(*) FROM public.quant_execution_operation_sources WHERE business_command_id=cmd)<>h.expected_unbound_order_count+1 OR
   (SELECT count(DISTINCT value->>'order_id') FROM jsonb_array_elements(h.expected_unbound_orders))<>h.expected_unbound_order_count THEN
   RAISE EXCEPTION 'portfolio result set incomplete'; END IF;
 FOR m IN SELECT value,n FROM jsonb_array_elements(h.expected_unbound_orders) WITH ORDINALITY a(value,n) LOOP
   PERFORM public.lc_portfolio_result_validate(cmd,m.value,m.n,check_projection);
 END LOOP;
 FOR c IN SELECT change_seq,row_id FROM public.quant_execution_operation_changes WHERE business_command_id=cmd ORDER BY change_seq LOOP
   seq:=seq+1;
   IF c.change_seq<>seq OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(h.expected_unbound_orders) a WHERE a->>'order_id'=c.row_id::text) THEN
     RAISE EXCEPTION 'portfolio global change sequence/scope differs'; END IF;
 END LOOP;
END $$;

CREATE FUNCTION lc_record_portfolio_unbound_drawdown(canonical_request bytea,actor_ref text)
RETURNS TABLE(command_id uuid,replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public SET TimeZone='UTC' SET DateStyle='ISO,YMD' AS $$
DECLARE req jsonb; portfolio uuid; valuation date; cmd uuid; h public.lifecycle_business_commands%ROWTYPE;
 prior public.lifecycle_business_commands%ROWTYPE; pause public.quant_portfolio_risk_events%ROWTYPE;
 members jsonb; before_image jsonb; result jsonb; requested text; outcome text; reason text; o public.suggested_orders%ROWTYPE; ordinal integer:=0;
BEGIN
 IF current_setting('transaction_isolation')<>'read committed' OR pg_current_xact_id_if_assigned() IS NOT NULL THEN
   RAISE EXCEPTION 'portfolio command requires untouched READ COMMITTED transaction'; END IF;
 req:=convert_from(canonical_request,'UTF8')::jsonb;
 portfolio:=(req->>'portfolio_id')::uuid; valuation:=(req->>'valuation_date')::date;
 IF valuation IS NULL OR actor_ref IS NULL OR length(trim(actor_ref))=0 OR length(actor_ref)>128 OR req->>'request_key' IS NULL OR
   length(trim(req->>'request_key'))=0 OR length(req->>'request_key')>128 OR canonical_request IS DISTINCT FROM convert_to(public.lc_json_canonical(
   jsonb_build_object('schema_version',1,'command_kind','PORTFOLIO_DRAWDOWN','portfolio_id',portfolio::text,'valuation_date',valuation::text,'request_key',req->>'request_key')),'UTF8') THEN
   RAISE EXCEPTION 'portfolio original request/actor differs'; END IF;
 PERFORM 1 FROM public.portfolios WHERE id=portfolio FOR UPDATE;
 IF NOT FOUND OR pg_current_xact_id_if_assigned() IS NULL THEN RAISE EXCEPTION 'portfolio missing or transaction locks absent'; END IF;
 SELECT * INTO h FROM public.lifecycle_business_commands c WHERE c.portfolio_id=portfolio AND c.request_key=req->>'request_key';
 IF FOUND THEN
   IF h.canonical_request<>canonical_request THEN RAISE EXCEPTION 'request key conflicts with another original request'; END IF;
   PERFORM public.lc_portfolio_validate(h.id,false); RETURN QUERY SELECT h.id,true; RETURN;
 END IF;
 IF EXISTS(SELECT 1 FROM public.position_lifecycle_states WHERE portfolio_id=portfolio) THEN
   RAISE EXCEPTION 'portfolio lifecycle steps are not connected'; END IF;
 PERFORM 1 FROM public.suggested_orders WHERE portfolio_id=portfolio ORDER BY id FOR UPDATE;
 IF EXISTS(SELECT 1 FROM public.suggested_orders WHERE portfolio_id=portfolio AND (lifecycle_id IS NOT NULL OR intent_id IS NOT NULL)) THEN
   RAISE EXCEPTION 'portfolio bound orders are not connected'; END IF;
 SELECT * INTO STRICT pause FROM public.quant_portfolio_risk_events WHERE portfolio_id=portfolio ORDER BY revision DESC LIMIT 1 FOR UPDATE;
 IF pause.kind<>'PAUSE' OR pause.facts_as_of>valuation THEN RAISE EXCEPTION 'existing active pause required'; END IF;
 members:=public.lc_portfolio_manifest(portfolio);
 IF jsonb_array_length(members)=0 THEN RAISE EXCEPTION 'empty portfolio command is not supported'; END IF;
 SELECT * INTO prior FROM public.lifecycle_business_commands c WHERE c.portfolio_id=portfolio ORDER BY command_seq DESC LIMIT 1;
 cmd:=gen_random_uuid(); PERFORM set_config('liveprofit.lifecycle_command',cmd::text,true);
 INSERT INTO public.lifecycle_business_commands(id,portfolio_id,request_key,command_seq,previous_command_id,previous_manifest_hash,
   command_kind,request_schema_version,canonical_request,request_hash,selector_version,expected_steps,expected_unbound_orders,manifest_hash,
   expected_step_count,expected_unbound_order_count,actor_type,actor_ref)
 VALUES(cmd,portfolio,req->>'request_key',coalesce(prior.command_seq,0)+1,prior.id,prior.manifest_hash,'PORTFOLIO_DRAWDOWN',1,canonical_request,
   encode(sha256(canonical_request),'hex'),'portfolio-unbound-drawdown:v1','[]'::jsonb,members,
   public.lc_json_sha(jsonb_build_object('steps','[]'::jsonb,'unbound_orders',members)),0,jsonb_array_length(members),'USER',actor_ref);
 INSERT INTO public.quant_execution_operation_sources(id,business_command_id,source_role,source_ordinal,source_type,scope,risk_event_id,source_snapshot,source_content_hash)
 VALUES(gen_random_uuid(),cmd,'RISK_PAUSE_INPUT',1,'RISK_EVENT','COMMAND',pause.id,public.lc_bound_image(row_to_json(pause)::text),public.lc_json_sha(public.lc_bound_image(row_to_json(pause)::text)));
 FOR o IN SELECT * FROM public.suggested_orders WHERE portfolio_id=portfolio AND id IN
   (SELECT (a->>'order_id')::uuid FROM jsonb_array_elements(members) a) ORDER BY id LOOP
   ordinal:=ordinal+1; before_image:=public.lc_order_image(row_to_json(o)::text);
   requested:=CASE WHEN o.status='PROPOSED' AND o.filled_quantity=0 THEN 'SUPERSEDED' ELSE 'RECONCILIATION_REQUIRED' END;
   outcome:=CASE WHEN o.status=requested THEN 'NO_STATE_CHANGE' ELSE 'APPLIED' END;
   reason:=CASE WHEN outcome='APPLIED' THEN 'ORDER_STATUS_APPLIED' ELSE 'ALREADY_IN_REQUESTED_STATE' END;
   IF outcome='APPLIED' THEN UPDATE public.suggested_orders SET status=requested,revision=revision+1,updated_at=clock_timestamp() WHERE id=o.id; END IF;
   SELECT * INTO STRICT o FROM public.suggested_orders WHERE id=o.id;
   result:=jsonb_build_object('schema_version',1,'outcome',outcome,'reason_code',reason,'before_row',before_image,
     'after_row',public.lc_order_image(row_to_json(o)::text),'missing_sources','[]'::jsonb,
     'change_count',jsonb_array_length(public.lc_portfolio_order_changes(cmd,o.id)),
     'changes_sha256',public.lc_json_sha(public.lc_portfolio_order_changes(cmd,o.id)));
   INSERT INTO public.quant_execution_operation_sources(id,business_command_id,source_role,source_ordinal,source_type,scope,order_id,
     source_revision,source_snapshot,source_content_hash,result_outcome,result_reason_code,result_schema_version)
   VALUES(gen_random_uuid(),cmd,'ORDER_STATUS_RESULT',ordinal,'UNBOUND_ORDER_RESULT','COMMAND',o.id,o.revision,result,public.lc_json_sha(result),outcome,reason,1);
 END LOOP;
 PERFORM public.lc_portfolio_validate(cmd,true);
 PERFORM set_config('liveprofit.lifecycle_command','',true); RETURN QUERY SELECT cmd,false;
END $$;

CREATE FUNCTION lc_portfolio_aux_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
 IF EXISTS(SELECT 1 FROM public.lifecycle_business_commands WHERE command_kind='PORTFOLIO_DRAWDOWN' AND captured_transaction_id=pg_current_xact_id()::text) THEN
   RAISE EXCEPTION 'portfolio command cannot change account or risk state'; END IF;
 RETURN NULL;
END $$;
'''


FUNCTIONS = ("lc_portfolio_manifest(uuid)", "lc_portfolio_check_head(lifecycle_business_commands)",
             "lc_portfolio_order_changes(uuid,uuid)", "lc_portfolio_result_validate(uuid,jsonb,bigint,boolean)",
             "lc_portfolio_validate(uuid,boolean)", "lc_record_portfolio_unbound_drawdown(bytea,text)",
             "lc_portfolio_aux_guard()")


def _lock():
    op.execute("LOCK TABLE portfolios, suggested_orders, quant_portfolio_risk_events IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE lifecycle_business_commands, quant_execution_operation_sources, quant_execution_operation_changes IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS(SELECT 1 FROM pg_class WHERE oid IN
      ('lifecycle_business_commands'::regclass,'quant_execution_operation_sources'::regclass,'quant_execution_operation_changes'::regclass)
      AND pg_get_userbyid(relowner)<>current_user) THEN RAISE EXCEPTION '0054 requires audit owner'; END IF; END $$""")


def upgrade():
    _lock()
    op.execute(SQL)
    op.execute(GUARD_SQL)
    for signature in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    op.execute("CREATE INDEX ix_lc_portfolio_command_transaction ON lifecycle_business_commands(captured_transaction_id) WHERE command_kind='PORTFOLIO_DRAWDOWN'")
    for table in ("portfolios", "quant_portfolio_risk_events", "quant_portfolio_exit_targets"):
        op.execute(f"CREATE TRIGGER lc_portfolio_aux_guard AFTER INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION lc_portfolio_aux_guard()")


def downgrade():
    _lock()
    for table in ("portfolios", "quant_portfolio_risk_events", "quant_portfolio_exit_targets"):
        op.execute(f"DROP TRIGGER lc_portfolio_aux_guard ON {table}")
    op.execute("DROP INDEX ix_lc_portfolio_command_transaction")
    op.execute(PREVIOUS_SQL)
    for signature in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION {signature}")

PREVIOUS_SQL = r'''
CREATE OR REPLACE FUNCTION lc_status_audit_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE cmd uuid; h public.lifecycle_business_commands%ROWTYPE;
BEGIN
  IF current_user<>pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID)) THEN
    RAISE EXCEPTION 'lifecycle audit writes require controlled owner function'; END IF;
  cmd:=nullif(current_setting('liveprofit.lifecycle_command',true),'')::uuid;
  IF cmd IS NULL THEN RAISE EXCEPTION 'lifecycle capture context is absent'; END IF;
  IF TG_TABLE_NAME='lifecycle_business_commands' THEN
    IF NEW.id<>cmd OR NEW.command_kind<>'ORDER_STATUS_CHANGED' OR NOT (
      (NEW.selector_version='order-state:v1' AND NEW.expected_step_count=0 AND NEW.expected_unbound_order_count=1) OR
      (NEW.selector_version='bound-order-state:v1' AND NEW.expected_step_count=1 AND NEW.expected_unbound_order_count=0)) THEN
      RAISE EXCEPTION 'only controlled single-order manifests are writable'; END IF;
    NEW.captured_transaction_id:=pg_current_xact_id()::text;
    RETURN NEW;
  END IF;
  SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
  IF NEW.business_command_id<>cmd OR h.command_kind<>'ORDER_STATUS_CHANGED' OR h.captured_transaction_id<>pg_current_xact_id()::text THEN
    RAISE EXCEPTION 'command transaction differs'; END IF;
  IF h.selector_version='bound-order-state:v1' THEN
    IF EXISTS(SELECT 1 FROM public.position_lifecycle_operations WHERE business_command_id=cmd) THEN
      RAISE EXCEPTION 'bound operation is already sealed'; END IF;
    IF TG_TABLE_NAME='position_lifecycle_operations' THEN
      IF NEW.id::text IS DISTINCT FROM h.expected_steps->0->>'operation_id' OR
        NEW.lifecycle_id::text IS DISTINCT FROM h.expected_steps->0->>'lifecycle_id' THEN
        RAISE EXCEPTION 'bound operation is outside selected step'; END IF;
    ELSIF NEW.operation_id::text IS DISTINCT FROM h.expected_steps->0->>'operation_id' OR NEW.scope<>'LIFECYCLE' THEN
      RAISE EXCEPTION 'bound evidence scope differs'; END IF;
  ELSIF h.selector_version='order-state:v1' THEN
    IF TG_TABLE_NAME='position_lifecycle_operations' THEN RAISE EXCEPTION 'unbound command cannot add operation'; END IF;
    IF NEW.operation_id IS NOT NULL OR NEW.scope<>'COMMAND' OR
      EXISTS(SELECT 1 FROM public.quant_execution_operation_sources WHERE business_command_id=cmd) THEN
      RAISE EXCEPTION 'unbound scope differs or result is already sealed'; END IF;
  ELSE RAISE EXCEPTION 'unsupported command selector'; END IF;
  IF TG_TABLE_NAME='quant_execution_operation_changes' AND pg_trigger_depth()<2 THEN
    RAISE EXCEPTION 'changes must be captured by the managed-order trigger'; END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION lc_status_order_capture() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public SET TimeZone='UTC' SET DateStyle='ISO,YMD' AS $$
DECLARE cmd uuid; h public.lifecycle_business_commands%ROWTYPE; seq bigint; operation uuid; lifecycle uuid;
  old_image jsonb; new_image jsonb; scope_name text; binding text;
BEGIN
  cmd:=nullif(current_setting('liveprofit.lifecycle_command',true),'')::uuid;
  IF cmd IS NULL THEN
    IF EXISTS(SELECT 1 FROM public.lifecycle_business_commands WHERE command_kind='ORDER_STATUS_CHANGED'
      AND captured_transaction_id=pg_current_xact_id()::text) THEN
      RAISE EXCEPTION 'same-transaction order command is sealed; additional order writes are outside its scope'; END IF;
    RETURN NULL;
  END IF;
  SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
  IF TG_OP<>'UPDATE' OR h.command_kind<>'ORDER_STATUS_CHANGED' OR h.captured_transaction_id<>pg_current_xact_id()::text OR
    OLD.id::text IS DISTINCT FROM (convert_from(h.canonical_request,'UTF8')::jsonb)->>'order_id' OR
    OLD.id<>NEW.id OR OLD.portfolio_id<>h.portfolio_id OR NEW.portfolio_id<>h.portfolio_id THEN
    RAISE EXCEPTION 'status capture scope/transaction differs'; END IF;
  IF h.selector_version='bound-order-state:v1' THEN
    operation:=(h.expected_steps->0->>'operation_id')::uuid;
    lifecycle:=(h.expected_steps->0->>'lifecycle_id')::uuid;
    IF OLD.lifecycle_id IS DISTINCT FROM lifecycle OR NEW.lifecycle_id IS DISTINCT FROM lifecycle OR
      EXISTS(SELECT 1 FROM public.position_lifecycle_operations WHERE business_command_id=cmd) THEN
      RAISE EXCEPTION 'bound capture binding differs or operation is sealed'; END IF;
    old_image:=public.lc_bound_image(row_to_json(OLD)::text); new_image:=public.lc_bound_image(row_to_json(NEW)::text);
    scope_name:='LIFECYCLE'; binding:='BOTH';
  ELSIF h.selector_version='order-state:v1' THEN
    IF OLD.lifecycle_id IS NOT NULL OR NEW.lifecycle_id IS NOT NULL OR OLD.intent_id IS NOT NULL OR NEW.intent_id IS NOT NULL OR
      EXISTS(SELECT 1 FROM public.quant_execution_operation_sources WHERE business_command_id=cmd) THEN
      RAISE EXCEPTION 'unbound capture binding differs or result is sealed'; END IF;
    old_image:=public.lc_order_image(row_to_json(OLD)::text); new_image:=public.lc_order_image(row_to_json(NEW)::text);
    scope_name:='COMMAND'; binding:='NONE';
  ELSE RAISE EXCEPTION 'unsupported capture selector'; END IF;
  SELECT coalesce(max(change_seq),0)+1 INTO seq FROM public.quant_execution_operation_changes WHERE business_command_id=cmd;
  INSERT INTO public.quant_execution_operation_changes
    (id,business_command_id,operation_id,change_seq,scope,entity_type,row_id,change_kind,binding_side,before_row,after_row)
    VALUES(gen_random_uuid(),cmd,operation,seq,scope_name,'ORDER',OLD.id,'UPDATE',binding,old_image,new_image);
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION lc_status_managed_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  IF nullif(current_setting('liveprofit.lifecycle_command',true),'') IS NOT NULL OR EXISTS(
    SELECT 1 FROM public.lifecycle_business_commands WHERE command_kind='ORDER_STATUS_CHANGED'
      AND captured_transaction_id=pg_current_xact_id()::text) THEN
    RAISE EXCEPTION 'status command cannot modify other managed state'; END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION lc_status_command_check() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  IF NEW.selector_version='bound-order-state:v1' THEN PERFORM public.lc_bound_validate(NEW.id,true);
  ELSE PERFORM public.lc_validate_unbound_command(NEW.id,true); END IF;
  RETURN NULL;
END $$;
'''

GUARD_SQL = r'''
CREATE OR REPLACE FUNCTION lc_status_audit_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE cmd uuid; h public.lifecycle_business_commands%ROWTYPE;
BEGIN
  IF current_user<>pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID)) THEN
    RAISE EXCEPTION 'lifecycle audit writes require controlled owner function'; END IF;
  cmd:=nullif(current_setting('liveprofit.lifecycle_command',true),'')::uuid;
  IF cmd IS NULL THEN RAISE EXCEPTION 'lifecycle capture context is absent'; END IF;
  IF TG_TABLE_NAME='lifecycle_business_commands' THEN
   IF NEW.command_kind='PORTFOLIO_DRAWDOWN' THEN
    PERFORM public.lc_portfolio_check_head(NEW);
    IF NEW.expected_unbound_orders IS DISTINCT FROM public.lc_portfolio_manifest(NEW.portfolio_id) OR
      EXISTS(SELECT 1 FROM public.position_lifecycle_states WHERE portfolio_id=NEW.portfolio_id) OR
      EXISTS(SELECT 1 FROM public.suggested_orders WHERE portfolio_id=NEW.portfolio_id AND (lifecycle_id IS NOT NULL OR intent_id IS NOT NULL)) THEN
      RAISE EXCEPTION 'portfolio selected participant set differs'; END IF;
    IF NEW.id<>cmd THEN RAISE EXCEPTION 'portfolio command context differs'; END IF;
    NEW.captured_transaction_id:=pg_current_xact_id()::text; RETURN NEW;
   END IF;
  END IF;
  IF TG_TABLE_NAME<>'lifecycle_business_commands' THEN
    SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
    IF h.command_kind='PORTFOLIO_DRAWDOWN' THEN
      IF TG_TABLE_NAME='position_lifecycle_operations' THEN
        RAISE EXCEPTION 'portfolio lifecycle steps are not connected'; END IF;
      IF h.captured_transaction_id<>pg_current_xact_id()::text OR NEW.business_command_id<>cmd OR
        NEW.operation_id IS NOT NULL OR NEW.scope<>'COMMAND' THEN
        RAISE EXCEPTION 'portfolio evidence transaction/scope differs'; END IF;
      IF TG_TABLE_NAME='quant_execution_operation_changes' THEN
        IF pg_trigger_depth()<2 OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(h.expected_unbound_orders) a WHERE a->>'order_id'=NEW.row_id::text) OR
          EXISTS(SELECT 1 FROM public.quant_execution_operation_sources WHERE business_command_id=cmd AND order_id=NEW.row_id AND source_type='UNBOUND_ORDER_RESULT') THEN
          RAISE EXCEPTION 'portfolio change is outside scope or sealed'; END IF;
      ELSIF TG_TABLE_NAME='quant_execution_operation_sources' THEN
        IF NEW.source_role='RISK_PAUSE_INPUT' THEN
          IF NEW.source_type<>'RISK_EVENT' OR NEW.source_ordinal<>1 OR EXISTS(SELECT 1 FROM public.quant_execution_operation_sources WHERE business_command_id=cmd) THEN
            RAISE EXCEPTION 'portfolio pause source differs'; END IF;
        ELSIF NEW.source_role='ORDER_STATUS_RESULT' THEN
          IF NEW.source_type<>'UNBOUND_ORDER_RESULT' OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(h.expected_unbound_orders) WITH ORDINALITY a(v,n)
            WHERE v->>'order_id'=NEW.order_id::text AND n=NEW.source_ordinal) THEN RAISE EXCEPTION 'portfolio result outside scope'; END IF;
        ELSE RAISE EXCEPTION 'portfolio source role differs'; END IF;
      ELSE RAISE EXCEPTION 'unsupported portfolio audit table'; END IF;
      RETURN NEW;
    END IF;
  END IF;
  IF TG_TABLE_NAME='lifecycle_business_commands' THEN
    IF NEW.id<>cmd OR NEW.command_kind<>'ORDER_STATUS_CHANGED' OR NOT (
      (NEW.selector_version='order-state:v1' AND NEW.expected_step_count=0 AND NEW.expected_unbound_order_count=1) OR
      (NEW.selector_version='bound-order-state:v1' AND NEW.expected_step_count=1 AND NEW.expected_unbound_order_count=0)) THEN
      RAISE EXCEPTION 'only controlled single-order manifests are writable'; END IF;
    NEW.captured_transaction_id:=pg_current_xact_id()::text;
    RETURN NEW;
  END IF;
  SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
  IF NEW.business_command_id<>cmd OR h.command_kind<>'ORDER_STATUS_CHANGED' OR h.captured_transaction_id<>pg_current_xact_id()::text THEN
    RAISE EXCEPTION 'command transaction differs'; END IF;
  IF h.selector_version='bound-order-state:v1' THEN
    IF EXISTS(SELECT 1 FROM public.position_lifecycle_operations WHERE business_command_id=cmd) THEN
      RAISE EXCEPTION 'bound operation is already sealed'; END IF;
    IF TG_TABLE_NAME='position_lifecycle_operations' THEN
      IF NEW.id::text IS DISTINCT FROM h.expected_steps->0->>'operation_id' OR
        NEW.lifecycle_id::text IS DISTINCT FROM h.expected_steps->0->>'lifecycle_id' THEN
        RAISE EXCEPTION 'bound operation is outside selected step'; END IF;
    ELSIF NEW.operation_id::text IS DISTINCT FROM h.expected_steps->0->>'operation_id' OR NEW.scope<>'LIFECYCLE' THEN
      RAISE EXCEPTION 'bound evidence scope differs'; END IF;
  ELSIF h.selector_version='order-state:v1' THEN
    IF TG_TABLE_NAME='position_lifecycle_operations' THEN RAISE EXCEPTION 'unbound command cannot add operation'; END IF;
    IF NEW.operation_id IS NOT NULL OR NEW.scope<>'COMMAND' OR
      EXISTS(SELECT 1 FROM public.quant_execution_operation_sources WHERE business_command_id=cmd) THEN
      RAISE EXCEPTION 'unbound scope differs or result is already sealed'; END IF;
  ELSE RAISE EXCEPTION 'unsupported command selector'; END IF;
  IF TG_TABLE_NAME='quant_execution_operation_changes' AND pg_trigger_depth()<2 THEN
    RAISE EXCEPTION 'changes must be captured by the managed-order trigger'; END IF;
  RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION lc_status_order_capture() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public SET TimeZone='UTC' SET DateStyle='ISO,YMD' AS $$
DECLARE cmd uuid; h public.lifecycle_business_commands%ROWTYPE; seq bigint; operation uuid; lifecycle uuid;
  old_image jsonb; new_image jsonb; scope_name text; binding text;
BEGIN
  cmd:=nullif(current_setting('liveprofit.lifecycle_command',true),'')::uuid;
  IF cmd IS NULL THEN
    IF EXISTS(SELECT 1 FROM public.lifecycle_business_commands WHERE command_kind IN ('ORDER_STATUS_CHANGED','PORTFOLIO_DRAWDOWN')
      AND captured_transaction_id=pg_current_xact_id()::text) THEN
      RAISE EXCEPTION 'same-transaction order command is sealed; additional order writes are outside its scope'; END IF;
    RETURN NULL;
  END IF;
  SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
  IF h.command_kind='PORTFOLIO_DRAWDOWN' THEN
    IF TG_OP<>'UPDATE' OR h.captured_transaction_id<>pg_current_xact_id()::text OR OLD.id<>NEW.id OR
      OLD.portfolio_id<>h.portfolio_id OR NEW.portfolio_id<>h.portfolio_id OR OLD.lifecycle_id IS NOT NULL OR NEW.lifecycle_id IS NOT NULL OR
      OLD.intent_id IS NOT NULL OR NEW.intent_id IS NOT NULL OR NOT EXISTS(SELECT 1 FROM jsonb_array_elements(h.expected_unbound_orders) a WHERE a->>'order_id'=OLD.id::text) OR
      EXISTS(SELECT 1 FROM public.quant_execution_operation_sources WHERE business_command_id=cmd AND order_id=OLD.id AND source_type='UNBOUND_ORDER_RESULT') THEN
      RAISE EXCEPTION 'portfolio order capture outside scope/transaction or sealed'; END IF;
    SELECT coalesce(max(change_seq),0)+1 INTO seq FROM public.quant_execution_operation_changes WHERE business_command_id=cmd;
    INSERT INTO public.quant_execution_operation_changes(id,business_command_id,change_seq,scope,entity_type,row_id,change_kind,binding_side,before_row,after_row)
      VALUES(gen_random_uuid(),cmd,seq,'COMMAND','ORDER',OLD.id,'UPDATE','NONE',public.lc_order_image(row_to_json(OLD)::text),public.lc_order_image(row_to_json(NEW)::text));
    RETURN NULL;
  END IF;
  IF TG_OP<>'UPDATE' OR h.command_kind<>'ORDER_STATUS_CHANGED' OR h.captured_transaction_id<>pg_current_xact_id()::text OR
    OLD.id::text IS DISTINCT FROM (convert_from(h.canonical_request,'UTF8')::jsonb)->>'order_id' OR
    OLD.id<>NEW.id OR OLD.portfolio_id<>h.portfolio_id OR NEW.portfolio_id<>h.portfolio_id THEN
    RAISE EXCEPTION 'status capture scope/transaction differs'; END IF;
  IF h.selector_version='bound-order-state:v1' THEN
    operation:=(h.expected_steps->0->>'operation_id')::uuid;
    lifecycle:=(h.expected_steps->0->>'lifecycle_id')::uuid;
    IF OLD.lifecycle_id IS DISTINCT FROM lifecycle OR NEW.lifecycle_id IS DISTINCT FROM lifecycle OR
      EXISTS(SELECT 1 FROM public.position_lifecycle_operations WHERE business_command_id=cmd) THEN
      RAISE EXCEPTION 'bound capture binding differs or operation is sealed'; END IF;
    old_image:=public.lc_bound_image(row_to_json(OLD)::text); new_image:=public.lc_bound_image(row_to_json(NEW)::text);
    scope_name:='LIFECYCLE'; binding:='BOTH';
  ELSIF h.selector_version='order-state:v1' THEN
    IF OLD.lifecycle_id IS NOT NULL OR NEW.lifecycle_id IS NOT NULL OR OLD.intent_id IS NOT NULL OR NEW.intent_id IS NOT NULL OR
      EXISTS(SELECT 1 FROM public.quant_execution_operation_sources WHERE business_command_id=cmd) THEN
      RAISE EXCEPTION 'unbound capture binding differs or result is sealed'; END IF;
    old_image:=public.lc_order_image(row_to_json(OLD)::text); new_image:=public.lc_order_image(row_to_json(NEW)::text);
    scope_name:='COMMAND'; binding:='NONE';
  ELSE RAISE EXCEPTION 'unsupported capture selector'; END IF;
  SELECT coalesce(max(change_seq),0)+1 INTO seq FROM public.quant_execution_operation_changes WHERE business_command_id=cmd;
  INSERT INTO public.quant_execution_operation_changes
    (id,business_command_id,operation_id,change_seq,scope,entity_type,row_id,change_kind,binding_side,before_row,after_row)
    VALUES(gen_random_uuid(),cmd,operation,seq,scope_name,'ORDER',OLD.id,'UPDATE',binding,old_image,new_image);
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION lc_status_managed_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  IF nullif(current_setting('liveprofit.lifecycle_command',true),'') IS NOT NULL OR EXISTS(
    SELECT 1 FROM public.lifecycle_business_commands WHERE command_kind IN ('ORDER_STATUS_CHANGED','PORTFOLIO_DRAWDOWN')
      AND captured_transaction_id=pg_current_xact_id()::text) THEN
    RAISE EXCEPTION 'status command cannot modify other managed state'; END IF;
  RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION lc_status_command_check() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  IF NEW.command_kind='PORTFOLIO_DRAWDOWN' THEN PERFORM public.lc_portfolio_validate(NEW.id,true);
  ELSIF NEW.selector_version='bound-order-state:v1' THEN PERFORM public.lc_bound_validate(NEW.id,true);
  ELSE PERFORM public.lc_validate_unbound_command(NEW.id,true); END IF;
  RETURN NULL;
END $$;
'''
