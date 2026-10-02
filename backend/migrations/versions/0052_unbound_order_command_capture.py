"""Controlled unbound status command capture; production entry stays ungranted.

No tables or past facts are added. Bound operations and creation inputs remain
closed. Functions are frozen here; they never import application code.
"""
from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


SQL = r'''
CREATE FUNCTION lc_json_canonical(v jsonb) RETURNS text
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, public AS $$
DECLARE result text;
BEGIN
  CASE jsonb_typeof(v)
  WHEN 'object' THEN
    SELECT '{' || coalesce(string_agg(to_jsonb(key)::text || ':' || public.lc_json_canonical(value),
      ',' ORDER BY key COLLATE "C"), '') || '}' INTO result FROM jsonb_each(v);
  WHEN 'array' THEN
    SELECT '[' || coalesce(string_agg(public.lc_json_canonical(value), ',' ORDER BY ordinal), '') || ']'
      INTO result FROM jsonb_array_elements(v) WITH ORDINALITY AS a(value, ordinal);
  ELSE result := v::text;
  END CASE;
  RETURN result;
END $$;

CREATE FUNCTION lc_json_sha(v jsonb) RETURNS text
LANGUAGE sql IMMUTABLE STRICT SET search_path = pg_catalog, public AS $$
  SELECT encode(sha256(convert_to(public.lc_json_canonical(v), 'UTF8')), 'hex')
$$;

CREATE FUNCTION lc_order_image(raw text) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path = pg_catalog, public AS $$
DECLARE image jsonb := raw::jsonb; name text;
BEGIN
  FOREACH name IN ARRAY ARRAY['quantity','filled_quantity','limit_price','stop_price','reserved_cash','reserved_risk'] LOOP
    IF jsonb_typeof(image->name) = 'number' THEN
      image := jsonb_set(image, ARRAY[name], to_jsonb(image->>name));
    ELSIF (image->name) IS DISTINCT FROM 'null'::jsonb THEN
      RAISE EXCEPTION 'order original numeric field is invalid: %', name;
    END IF;
  END LOOP;
  RETURN image || jsonb_build_object('__raw_row_json__', raw);
END $$;

CREATE FUNCTION lc_check_order_image(image jsonb, portfolio uuid, order_id uuid) RETURNS void
LANGUAGE plpgsql IMMUTABLE SET search_path = pg_catalog, public AS $$
BEGIN
  IF image IS NULL OR jsonb_typeof(image) <> 'object' OR
    (SELECT count(*) FROM jsonb_object_keys(image)) <> 26 OR NOT image ?& ARRAY[
      'id','portfolio_id','position_id','lifecycle_id','intent_id','source_signal_id','rule_certificate_id',
      'rule_authorized_at','decision_at','market','symbol','industry_code','side','quantity','filled_quantity',
      'limit_price','stop_price','reserved_cash','reserved_risk','reason_code','status','revision',
      'earliest_execution_trade_date','created_at','updated_at','__raw_row_json__'] OR
    jsonb_typeof(image->'__raw_row_json__') <> 'string' OR
    image IS DISTINCT FROM public.lc_order_image(image->>'__raw_row_json__') OR
    image->>'id' IS DISTINCT FROM order_id::text OR image->>'portfolio_id' IS DISTINCT FROM portfolio::text OR
    image->'lifecycle_id' IS DISTINCT FROM 'null'::jsonb OR image->'intent_id' IS DISTINCT FROM 'null'::jsonb OR
    jsonb_typeof(image->'revision') IS DISTINCT FROM 'number' OR (image->>'revision') !~ '^[1-9][0-9]*$' THEN
    RAISE EXCEPTION 'unbound physical order image identity/schema differs';
  END IF;
END $$;

CREATE FUNCTION lc_changes_payload(cmd uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, public AS $$
  SELECT coalesce(jsonb_agg((to_jsonb(c) - 'captured_at') || jsonb_build_object('captured_at',
    to_char(c.captured_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '+00:00')
    ORDER BY c.change_seq), '[]'::jsonb)
  FROM public.quant_execution_operation_changes c WHERE c.business_command_id = cmd
$$;

CREATE FUNCTION lc_validate_unbound_command(cmd uuid, check_projection boolean) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, public SET TimeZone = 'UTC' SET DateStyle = 'ISO, YMD' AS $$
DECLARE h public.lifecycle_business_commands%ROWTYPE; s public.quant_execution_operation_sources%ROWTYPE;
  req jsonb; expected jsonb; result jsonb; cursor_image jsonb; ch record; seq bigint := 0;
  prior public.lifecycle_business_commands%ROWTYPE; raw text; order_id uuid; n bigint;
BEGIN
  SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
  req := convert_from(h.canonical_request, 'UTF8')::jsonb;
  order_id := (req->>'order_id')::uuid;
  expected := jsonb_build_array(jsonb_build_object('order_id', order_id::text,
    'original_revision', (req->>'expected_revision')::integer,
    'allowed_results', jsonb_build_array('APPLIED','NO_STATE_CHANGE','BLOCKED')));
  IF req IS DISTINCT FROM jsonb_build_object('schema_version',1,'command_kind','ORDER_STATUS_CHANGED',
      'order_id',order_id::text,'status',req->>'status','expected_revision',(req->>'expected_revision')::integer,
      'request_key',h.request_key) OR h.command_kind <> 'ORDER_STATUS_CHANGED' OR h.selector_version <> 'order-state:v1' OR
    h.request_schema_version <> 1 OR req->>'request_key' IS DISTINCT FROM h.request_key OR
    h.canonical_request <> convert_to(public.lc_json_canonical(req), 'UTF8') OR
    h.request_hash <> encode(sha256(h.canonical_request),'hex') OR
    h.expected_steps <> '[]'::jsonb OR h.expected_step_count <> 0 OR
    h.expected_unbound_orders <> expected OR h.expected_unbound_order_count <> 1 OR
    h.manifest_hash <> public.lc_json_sha(jsonb_build_object('steps','[]'::jsonb,'unbound_orders',expected)) OR
    (check_projection AND h.captured_transaction_id <> pg_current_xact_id()::text) THEN
    RAISE EXCEPTION 'unbound command request/manifest/transaction differs';
  END IF;
  IF h.command_seq > 1 THEN
    SELECT * INTO STRICT prior FROM public.lifecycle_business_commands WHERE id=h.previous_command_id;
    IF prior.portfolio_id <> h.portfolio_id OR prior.command_seq+1 <> h.command_seq OR
      prior.manifest_hash <> h.previous_manifest_hash OR prior.manifest_hash <>
      public.lc_json_sha(jsonb_build_object('steps',prior.expected_steps,'unbound_orders',prior.expected_unbound_orders)) THEN
      RAISE EXCEPTION 'unbound command predecessor differs';
    END IF;
  END IF;
  IF EXISTS (SELECT 1 FROM public.position_lifecycle_operations WHERE business_command_id=cmd) OR
    EXISTS (SELECT 1 FROM public.suggested_order_initial_inputs WHERE business_command_id=cmd) OR
    EXISTS (SELECT 1 FROM public.position_lifecycle_initial_inputs WHERE business_command_id=cmd) THEN
    RAISE EXCEPTION 'unbound command has unexpected lifecycle/input participants';
  END IF;
  SELECT count(*) INTO n FROM public.quant_execution_operation_sources WHERE business_command_id=cmd;
  IF n <> 1 THEN RAISE EXCEPTION 'unbound command requires exactly one sealed result'; END IF;
  SELECT * INTO STRICT s FROM public.quant_execution_operation_sources WHERE business_command_id=cmd;
  result := s.source_snapshot;
  IF s.operation_id IS NOT NULL OR s.scope <> 'COMMAND' OR s.source_type <> 'UNBOUND_ORDER_RESULT' OR
    s.source_role <> 'ORDER_STATUS_RESULT' OR s.source_ordinal <> 1 OR s.order_id <> order_id OR
    s.result_schema_version <> 1 OR (SELECT count(*) FROM jsonb_object_keys(result)) <> 8 OR
    NOT result ?& ARRAY['schema_version','outcome','reason_code','before_row','after_row','missing_sources',
                       'change_count','changes_sha256'] OR
    result->>'schema_version' IS DISTINCT FROM '1' OR result->>'outcome' IS DISTINCT FROM s.result_outcome OR
    result->>'reason_code' IS DISTINCT FROM s.result_reason_code OR s.source_content_hash <> public.lc_json_sha(result) THEN
    RAISE EXCEPTION 'unbound result identity/schema/digest differs';
  END IF;
  PERFORM public.lc_check_order_image(result->'before_row', h.portfolio_id, order_id);
  PERFORM public.lc_check_order_image(result->'after_row', h.portfolio_id, order_id);
  IF (result->'before_row'->>'revision')::integer <> (req->>'expected_revision')::integer OR
    (result->'after_row'->>'revision')::integer <> s.source_revision OR
    jsonb_typeof(result->'missing_sources') <> 'array' OR
    EXISTS (SELECT 1 FROM jsonb_array_elements(result->'missing_sources') v
            WHERE jsonb_typeof(v) <> 'string' OR length(trim(v#>>'{}'))=0) OR
    (SELECT count(*) FROM jsonb_array_elements(result->'missing_sources')) <>
    (SELECT count(DISTINCT v) FROM jsonb_array_elements(result->'missing_sources') v) THEN
    RAISE EXCEPTION 'unbound result revisions/missing sources differ';
  END IF;
  cursor_image := result->'before_row';
  FOR ch IN SELECT * FROM public.quant_execution_operation_changes WHERE business_command_id=cmd ORDER BY change_seq LOOP
    seq := seq+1;
    IF ch.change_seq <> seq OR ch.operation_id IS NOT NULL OR ch.scope <> 'COMMAND' OR
      ch.entity_type <> 'ORDER' OR ch.row_id <> order_id OR ch.change_kind <> 'UPDATE' OR ch.binding_side <> 'NONE' THEN
      RAISE EXCEPTION 'unbound change identity/sequence/scope differs';
    END IF;
    PERFORM public.lc_check_order_image(ch.before_row, h.portfolio_id, order_id);
    PERFORM public.lc_check_order_image(ch.after_row, h.portfolio_id, order_id);
    IF cursor_image IS DISTINCT FROM ch.before_row OR
      (ch.after_row->>'revision')::integer <> (ch.before_row->>'revision')::integer+1 OR
      (ch.before_row - ARRAY['__raw_row_json__','status','revision','updated_at']) IS DISTINCT FROM
      (ch.after_row - ARRAY['__raw_row_json__','status','revision','updated_at']) THEN
      RAISE EXCEPTION 'unbound row chain or permitted change differs';
    END IF;
    cursor_image := ch.after_row;
  END LOOP;
  IF cursor_image IS DISTINCT FROM result->'after_row' OR jsonb_typeof(result->'change_count') IS DISTINCT FROM 'number' OR
    (result->>'change_count') !~ '^[0-9]+$' OR result->'change_count' IS DISTINCT FROM to_jsonb(seq) OR
    result->>'changes_sha256' IS DISTINCT FROM public.lc_json_sha(public.lc_changes_payload(cmd)) THEN
    RAISE EXCEPTION 'unbound final row/change digest differs';
  END IF;
  IF s.result_outcome='NO_STATE_CHANGE' AND (seq <> 0 OR result->'before_row' <> result->'after_row' OR
    result->'after_row'->>'status' IS DISTINCT FROM req->>'status') THEN
    RAISE EXCEPTION 'unchanged result has an effect';
  ELSIF s.result_outcome='APPLIED' AND (seq=0 OR
    (result->'after_row'->>'revision')::integer <= (result->'before_row'->>'revision')::integer OR
    result->'after_row'->>'status' IS DISTINCT FROM req->>'status' OR result->'missing_sources' <> '[]'::jsonb) THEN
    RAISE EXCEPTION 'applied result lacks requested effect/evidence';
  END IF;
  IF check_projection THEN
    SELECT row_to_json(o)::text INTO STRICT raw FROM public.suggested_orders o WHERE id=order_id;
    IF public.lc_order_image(raw) IS DISTINCT FROM result->'after_row' THEN
      RAISE EXCEPTION 'sealed command differs from actual order projection';
    END IF;
  END IF;
END $$;

CREATE FUNCTION lc_unbound_audit_insert_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE cmd uuid; h public.lifecycle_business_commands%ROWTYPE;
BEGIN
  IF current_user <> pg_get_userbyid((SELECT relowner FROM pg_class WHERE oid=TG_RELID)) THEN
    RAISE EXCEPTION 'lifecycle audit writes require controlled owner function';
  END IF;
  cmd := nullif(current_setting('liveprofit.lifecycle_command',true),'')::uuid;
  IF cmd IS NULL THEN RAISE EXCEPTION 'lifecycle capture context is absent'; END IF;
  IF TG_TABLE_NAME='lifecycle_business_commands' THEN
    IF NEW.id <> cmd OR NEW.command_kind <> 'ORDER_STATUS_CHANGED' OR NEW.selector_version <> 'order-state:v1' OR
      NEW.expected_step_count <> 0 OR NEW.expected_unbound_order_count <> 1 THEN
      RAISE EXCEPTION 'only controlled single-unbound-order manifests are writable';
    END IF;
    NEW.captured_transaction_id := pg_current_xact_id()::text;
  ELSE
    SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
    IF NEW.business_command_id <> cmd OR h.command_kind <> 'ORDER_STATUS_CHANGED' OR
      h.captured_transaction_id <> pg_current_xact_id()::text OR NEW.operation_id IS NOT NULL OR NEW.scope <> 'COMMAND' OR
      EXISTS (SELECT 1 FROM public.quant_execution_operation_sources WHERE business_command_id=cmd) THEN
      RAISE EXCEPTION 'command transaction/scope differs or result is already sealed';
    END IF;
    IF TG_TABLE_NAME='quant_execution_operation_changes' AND pg_trigger_depth() < 2 THEN
      RAISE EXCEPTION 'changes must be captured by the managed-order trigger';
    END IF;
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION lc_capture_unbound_order_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public SET TimeZone = 'UTC' SET DateStyle = 'ISO, YMD' AS $$
DECLARE cmd uuid; h public.lifecycle_business_commands%ROWTYPE; seq bigint;
BEGIN
  cmd := nullif(current_setting('liveprofit.lifecycle_command',true),'')::uuid;
  IF cmd IS NULL THEN
    -- A queued command may already have passed SET CONSTRAINTS IMMEDIATE.
    -- Refuse later same-transaction writes even after the entry cleared its GUC.
    IF EXISTS (SELECT 1 FROM public.lifecycle_business_commands c
      WHERE c.command_kind='ORDER_STATUS_CHANGED' AND c.captured_transaction_id=pg_current_xact_id()::text) THEN
      RAISE EXCEPTION 'same-transaction order command is sealed; additional order writes are outside its scope';
    END IF;
    RETURN NULL; -- Transitional writers in later transactions remain uncaptured.
  END IF;
  SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
  IF TG_OP <> 'UPDATE' THEN RAISE EXCEPTION 'status command cannot insert/delete an order'; END IF;
  IF h.captured_transaction_id <> pg_current_xact_id()::text OR h.command_kind <> 'ORDER_STATUS_CHANGED' OR
    OLD.id::text IS DISTINCT FROM h.expected_unbound_orders->0->>'order_id' OR
    OLD.portfolio_id <> h.portfolio_id OR NEW.portfolio_id <> h.portfolio_id OR OLD.id <> NEW.id OR
    OLD.lifecycle_id IS NOT NULL OR NEW.lifecycle_id IS NOT NULL OR OLD.intent_id IS NOT NULL OR NEW.intent_id IS NOT NULL OR
    EXISTS (SELECT 1 FROM public.quant_execution_operation_sources WHERE business_command_id=cmd) THEN
    RAISE EXCEPTION 'unbound capture scope/transaction differs or command is sealed';
  END IF;
  SELECT coalesce(max(change_seq),0)+1 INTO seq FROM public.quant_execution_operation_changes WHERE business_command_id=cmd;
  INSERT INTO public.quant_execution_operation_changes
    (id,business_command_id,change_seq,scope,entity_type,row_id,change_kind,binding_side,before_row,after_row)
  VALUES (gen_random_uuid(),cmd,seq,'COMMAND','ORDER',OLD.id,'UPDATE','NONE',
    public.lc_order_image(row_to_json(OLD)::text),public.lc_order_image(row_to_json(NEW)::text));
  RETURN NULL;
END $$;

CREATE FUNCTION lc_unbound_command_deferred_check() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public AS $$
BEGIN
  PERFORM public.lc_validate_unbound_command(NEW.id,true);
  RETURN NULL;
END $$;

CREATE FUNCTION lc_record_unbound_order_status(portfolio uuid, canonical_request bytea, actor_ref text)
RETURNS TABLE(command_id uuid, replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public SET TimeZone = 'UTC' SET DateStyle = 'ISO, YMD' AS $$
DECLARE req jsonb; expected_req jsonb; o public.suggested_orders%ROWTYPE;
  h public.lifecycle_business_commands%ROWTYPE; prior public.lifecycle_business_commands%ROWTYPE;
  cmd uuid; manifest jsonb; before_image jsonb; after_image jsonb; result jsonb;
  outcome text; reason text; missing jsonb := '[]'::jsonb; order_id uuid; expected_revision integer; requested text;
BEGIN
  IF pg_current_xact_id_if_assigned() IS NOT NULL THEN
    RAISE EXCEPTION 'command requires a transaction without previous writes or row locks';
  END IF;
  req := convert_from(canonical_request,'UTF8')::jsonb;
  IF jsonb_typeof(req->'expected_revision') IS DISTINCT FROM 'number' OR (req->>'expected_revision') !~ '^[1-9][0-9]*$' OR
    jsonb_typeof(req->'order_id') IS DISTINCT FROM 'string' OR jsonb_typeof(req->'status') IS DISTINCT FROM 'string' OR
    jsonb_typeof(req->'request_key') IS DISTINCT FROM 'string' OR
    length(trim(req->>'request_key'))=0 OR length(req->>'request_key')>128 OR
    actor_ref IS NULL OR length(trim(actor_ref))=0 OR length(actor_ref)>128 THEN
    RAISE EXCEPTION 'invalid original command request/actor shape';
  END IF;
  order_id := (req->>'order_id')::uuid;
  expected_revision := (req->>'expected_revision')::integer;
  requested := req->>'status';
  IF requested NOT IN ('EXECUTING','REJECTED','CANCELLED','RECONCILIATION_REQUIRED','SUPERSEDED') THEN
    RAISE EXCEPTION 'unsupported original requested status';
  END IF;
  expected_req := jsonb_build_object('schema_version',1,'command_kind','ORDER_STATUS_CHANGED','order_id',order_id::text,
    'status',requested,'expected_revision',expected_revision,'request_key',req->>'request_key');
  IF canonical_request IS DISTINCT FROM convert_to(public.lc_json_canonical(expected_req),'UTF8') THEN
    RAISE EXCEPTION 'original command fields/canonical bytes differ';
  END IF;
  PERFORM 1 FROM public.portfolios WHERE id=portfolio FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'command portfolio does not exist'; END IF;
  SELECT * INTO h FROM public.lifecycle_business_commands c WHERE c.portfolio_id=portfolio AND c.request_key=req->>'request_key';
  IF FOUND THEN
    IF h.canonical_request <> canonical_request THEN RAISE EXCEPTION 'request key conflicts with another original request'; END IF;
    PERFORM public.lc_validate_unbound_command(h.id,false);
    RETURN QUERY SELECT h.id,true;
    RETURN;
  END IF;
  SELECT * INTO STRICT o FROM public.suggested_orders WHERE id=order_id FOR UPDATE;
  IF o.portfolio_id <> portfolio OR o.lifecycle_id IS NOT NULL OR o.intent_id IS NOT NULL THEN
    RAISE EXCEPTION 'only the selected portfolio unbound order is supported';
  END IF;
  IF o.revision <> expected_revision THEN RAISE EXCEPTION 'order revision conflict'; END IF;
  before_image := public.lc_order_image(row_to_json(o)::text);
  SELECT * INTO prior FROM public.lifecycle_business_commands c WHERE c.portfolio_id=portfolio ORDER BY c.command_seq DESC LIMIT 1;
  cmd := gen_random_uuid();
  manifest := jsonb_build_object('steps','[]'::jsonb,'unbound_orders',jsonb_build_array(jsonb_build_object(
    'order_id',order_id::text,'original_revision',expected_revision,
    'allowed_results',jsonb_build_array('APPLIED','NO_STATE_CHANGE','BLOCKED'))));
  PERFORM set_config('liveprofit.lifecycle_command',cmd::text,true);
  INSERT INTO public.lifecycle_business_commands
    (id,portfolio_id,request_key,command_seq,previous_command_id,previous_manifest_hash,command_kind,request_schema_version,
     canonical_request,request_hash,selector_version,expected_steps,expected_unbound_orders,manifest_hash,
     expected_step_count,expected_unbound_order_count,actor_type,actor_ref)
  VALUES (cmd,portfolio,req->>'request_key',coalesce(prior.command_seq,0)+1,prior.id,prior.manifest_hash,
    'ORDER_STATUS_CHANGED',1,canonical_request,encode(sha256(canonical_request),'hex'),'order-state:v1',
    manifest->'steps',manifest->'unbound_orders',public.lc_json_sha(manifest),0,1,'USER',actor_ref);
  IF o.side='BUY' AND requested='EXECUTING' THEN
    outcome := 'BLOCKED'; reason := 'BUY_EXECUTION_REQUIRES_CERTIFIED_ENTRY';
    missing := jsonb_build_array('EXECUTION_MARKET','ADMISSION','INSTRUMENT_RULE_CERTIFICATE');
  ELSIF requested IN ('CANCELLED','REJECTED','SUPERSEDED') AND (o.status <> 'PROPOSED' OR o.filled_quantity <> 0) THEN
    outcome := 'BLOCKED'; reason := 'BROKER_TERMINAL_RECEIPT_REQUIRED'; missing := jsonb_build_array('BROKER_TERMINAL_RECEIPT');
  ELSIF requested='EXECUTING' AND (o.status <> 'PROPOSED' OR o.filled_quantity <> 0) THEN
    outcome := 'BLOCKED'; reason := 'ORDER_NOT_UNFILLED_PROPOSAL'; missing := jsonb_build_array('BROKER_TERMINAL_RECEIPT');
  ELSIF requested='RECONCILIATION_REQUIRED' AND o.status NOT IN ('PROPOSED','EXECUTING','PARTIALLY_FILLED','RECONCILIATION_REQUIRED') THEN
    outcome := 'BLOCKED'; reason := 'TERMINAL_ORDER_CANNOT_REOPEN';
  ELSIF o.status=requested THEN
    outcome := 'NO_STATE_CHANGE'; reason := 'ALREADY_IN_REQUESTED_STATE';
  ELSE
    outcome := 'APPLIED'; reason := 'ORDER_STATUS_APPLIED';
    UPDATE public.suggested_orders SET status=requested,revision=revision+1,updated_at=clock_timestamp() WHERE id=order_id;
  END IF;
  SELECT * INTO STRICT o FROM public.suggested_orders WHERE id=order_id;
  after_image := public.lc_order_image(row_to_json(o)::text);
  result := jsonb_build_object('schema_version',1,'outcome',outcome,'reason_code',reason,'before_row',before_image,
    'after_row',after_image,'missing_sources',missing,'change_count',jsonb_array_length(public.lc_changes_payload(cmd)),
    'changes_sha256',public.lc_json_sha(public.lc_changes_payload(cmd)));
  INSERT INTO public.quant_execution_operation_sources
    (id,business_command_id,source_role,source_ordinal,source_type,scope,order_id,source_revision,source_snapshot,
     source_content_hash,result_outcome,result_reason_code,result_schema_version)
  VALUES (gen_random_uuid(),cmd,'ORDER_STATUS_RESULT',1,'UNBOUND_ORDER_RESULT','COMMAND',order_id,o.revision,result,
    public.lc_json_sha(result),outcome,reason,1);
  PERFORM public.lc_validate_unbound_command(cmd,true);
  PERFORM set_config('liveprofit.lifecycle_command','',true);
  RETURN QUERY SELECT cmd,false;
END $$;
'''

FUNCTIONS = (
    "lc_record_unbound_order_status(uuid,bytea,text)", "lc_unbound_command_deferred_check()",
    "lc_capture_unbound_order_change()", "lc_unbound_audit_insert_guard()",
    "lc_validate_unbound_command(uuid,boolean)", "lc_changes_payload(uuid)",
    "lc_check_order_image(jsonb,uuid,uuid)", "lc_order_image(text)", "lc_json_sha(jsonb)", "lc_json_canonical(jsonb)",
)
WRITABLE_TABLES = ("lifecycle_business_commands", "quant_execution_operation_sources", "quant_execution_operation_changes")


def upgrade():
    op.execute("LOCK TABLE portfolios IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE suggested_orders IN ACCESS EXCLUSIVE MODE")
    for table in WRITABLE_TABLES:
        op.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
    op.execute("""DO $$ BEGIN IF EXISTS (
      SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname IN
        ('lifecycle_business_commands','quant_execution_operation_sources','quant_execution_operation_changes')
        AND pg_get_userbyid(c.relowner) <> current_user)
      THEN RAISE EXCEPTION '0052 functions must be installed by the audit table owner'; END IF; END $$""")
    op.execute(SQL)
    op.execute("CREATE INDEX ix_lc_unbound_command_transaction ON lifecycle_business_commands(captured_transaction_id) "
               "WHERE command_kind='ORDER_STATUS_CHANGED'")
    for signature in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for table in WRITABLE_TABLES:
        op.execute(f"DROP TRIGGER lc_history_capture_closed ON {table}")
        op.execute(f"CREATE TRIGGER lc_history_capture_closed BEFORE INSERT ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION lc_unbound_audit_insert_guard()")
    # One pending commit validation per command. All legal children precede the
    # sealing result; every later write is rejected, including after early SET.
    op.execute("CREATE CONSTRAINT TRIGGER lc_unbound_command_complete AFTER INSERT ON lifecycle_business_commands "
               "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lc_unbound_command_deferred_check()")
    op.execute("CREATE TRIGGER lc_unbound_order_capture AFTER INSERT OR UPDATE OR DELETE ON suggested_orders "
               "FOR EACH ROW EXECUTE FUNCTION lc_capture_unbound_order_change()")


def downgrade():
    # Removing capability never removes already recorded command facts.
    op.execute("LOCK TABLE portfolios IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE suggested_orders IN ACCESS EXCLUSIVE MODE")
    op.execute("DROP TRIGGER lc_unbound_order_capture ON suggested_orders")
    op.execute("DROP INDEX ix_lc_unbound_command_transaction")
    for table in WRITABLE_TABLES:
        op.execute(f"DROP TRIGGER lc_history_capture_closed ON {table}")
        op.execute(f"CREATE TRIGGER lc_history_capture_closed BEFORE INSERT ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION lifecycle_history_capture_not_ready()")
    op.execute("DROP TRIGGER lc_unbound_command_complete ON lifecycle_business_commands")
    for signature in FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
