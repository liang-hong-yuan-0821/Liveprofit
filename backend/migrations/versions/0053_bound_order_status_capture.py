"""Seal a single bound order-status operation; production entry is ungranted.

No past history is repaired. Only a predecessor-consistent, fixed member set is
supported; general lifecycle writers and creation inputs remain unconnected.
"""
from alembic import op

revision = "0053"
down_revision = "0052"
branch_labels = None
depends_on = None


SQL = r'''
CREATE FUNCTION lc_bound_normalize(v jsonb) RETURNS jsonb
LANGUAGE plpgsql IMMUTABLE STRICT SET search_path=pg_catalog,public AS $$
DECLARE result jsonb;
BEGIN
  CASE jsonb_typeof(v)
  WHEN 'object' THEN SELECT coalesce(jsonb_object_agg(key,public.lc_bound_normalize(value)),'{}'::jsonb)
    INTO result FROM jsonb_each(v);
  WHEN 'array' THEN SELECT coalesce(jsonb_agg(public.lc_bound_normalize(value) ORDER BY ordinal),'[]'::jsonb)
    INTO result FROM jsonb_array_elements(v) WITH ORDINALITY a(value,ordinal);
  WHEN 'number' THEN result := CASE WHEN v::text ~ '[.eE]' THEN to_jsonb(v::text) ELSE v END;
  ELSE result := v;
  END CASE;
  RETURN result;
END $$;

CREATE FUNCTION lc_bound_image(raw text) RETURNS jsonb
LANGUAGE sql IMMUTABLE STRICT SET search_path=pg_catalog,public AS $$
  SELECT public.lc_bound_normalize(raw::jsonb) || jsonb_build_object('__raw_row_json__',raw)
$$;

CREATE FUNCTION lc_bound_managed(image jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE STRICT SET search_path=pg_catalog,public AS $$
DECLARE state jsonb; name text;
BEGIN
  state:=image-ARRAY['created_at','updated_at','source_signal_id','strategy_version_id','lifecycle_policy_version_id','__raw_row_json__'];
  FOREACH name IN ARRAY ARRAY['closed_at','rule_authorized_at','decision_at'] LOOP
    IF state ? name AND state->name<>'null'::jsonb THEN
      state:=jsonb_set(state,ARRAY[name],to_jsonb(to_char((state->>name)::timestamptz AT TIME ZONE 'UTC',
        'YYYY-MM-DD"T"HH24:MI:SS.US')||'+00:00'));
    END IF;
  END LOOP;
  RETURN state;
END
$$;

CREATE FUNCTION lc_bound_state(state jsonb) RETURNS jsonb
LANGUAGE plpgsql STABLE STRICT SET search_path=pg_catalog,public AS $$
DECLARE result jsonb; name text; members jsonb;
BEGIN
  result:=jsonb_build_object('lifecycle',public.lc_bound_managed(state->'lifecycle'));
  FOREACH name IN ARRAY ARRAY['trailing_stops','expectations','active_intents','active_orders'] LOOP
    SELECT coalesce(jsonb_agg(public.lc_bound_managed(value) ORDER BY ordinal),'[]'::jsonb) INTO members
      FROM jsonb_array_elements(state->name) WITH ORDINALITY a(value,ordinal);
    result:=result||jsonb_build_object(name,members);
  END LOOP;
  RETURN result;
END $$;

CREATE FUNCTION lc_bound_check_image(image jsonb,kind text) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE expected text[];
BEGIN
  CASE kind
    WHEN 'lifecycle' THEN expected:=ARRAY['__raw_row_json__','arc_neckline_price','closed_at','confirmation_completed','created_at','id','initial_fill_id','initial_fill_price','initial_stop_price','last_processed_trade_date','lifecycle_policy_version_id','market','phase','portfolio_id','position_id','profit_take_price','profit_target_reached','profit_trim_completed','risk_capacity_shares','state_version','strategy_version_id','symbol','target_exposure_pct','target_shares','updated_at'];
    WHEN 'trailing_stops' THEN expected:=ARRAY['__raw_row_json__','active_stop_price','config_snapshot','created_at','exit_intent_id','high_water_mark','id','initial_stop_price','last_processed_trade_date','lifecycle_id','phase','updated_at'];
    WHEN 'expectations' THEN expected:=ARRAY['__raw_row_json__','created_at','fill_trade_date','fulfilled_trade_date','id','last_processed_trade_date','lifecycle_id','observed_trading_days','status','updated_at','window_trading_days'];
    WHEN 'active_intents' THEN expected:=ARRAY['__raw_row_json__','created_at','id','lifecycle_id','reason_code','revision','source_signal_id','state_version','status','target_shares','trade_date','updated_at'];
    WHEN 'active_orders' THEN expected:=ARRAY['__raw_row_json__','created_at','decision_at','earliest_execution_trade_date','filled_quantity','id','industry_code','intent_id','lifecycle_id','limit_price','market','portfolio_id','position_id','quantity','reason_code','reserved_cash','reserved_risk','revision','rule_authorized_at','rule_certificate_id','side','source_signal_id','status','stop_price','symbol','updated_at'];
    WHEN 'positions' THEN expected:=ARRAY['__raw_row_json__','active_stop_price','average_cost','created_at','id','market','portfolio_id','quantity','symbol','updated_at'];
    WHEN 'daily_facts' THEN expected:=ARRAY['__raw_row_json__','created_at','data_as_of','final_target_shares','id','input_hash','input_payload','lifecycle_id','planning_result','price_basis','rule_version','state_version_after','state_version_before','trade_date','updated_at'];
    ELSE RAISE EXCEPTION 'unsupported frozen physical kind';
  END CASE;
  IF jsonb_typeof(image) IS DISTINCT FROM 'object' OR NOT image ?& expected OR
    (SELECT count(*) FROM jsonb_object_keys(image))<>cardinality(expected) OR
    image IS DISTINCT FROM public.lc_bound_image(image->>'__raw_row_json__') OR
    jsonb_typeof(image->'id') IS DISTINCT FROM 'string' THEN RAISE EXCEPTION 'frozen physical schema/original differs'; END IF;
  PERFORM (image->>'id')::uuid;
END $$;

CREATE FUNCTION lc_bound_verify_previous(operation_id uuid) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE p public.position_lifecycle_operations%ROWTYPE; snapshot jsonb; evidence jsonb; rows jsonb; state jsonb;
  row_image jsonb; members jsonb; name text; original text; keys text[]; baseline boolean; l jsonb;
BEGIN
  SELECT * INTO STRICT p FROM public.position_lifecycle_operations WHERE id=operation_id;
  snapshot:=p.after_snapshot; evidence:=snapshot->'evidence';
  IF jsonb_typeof(snapshot) IS DISTINCT FROM 'object' OR
    snapshot-ARRAY['managed_state','account_observations','evidence']<>'{}'::jsonb OR
    NOT snapshot ?& ARRAY['managed_state','account_observations','evidence'] OR
    jsonb_typeof(snapshot->'managed_state') IS DISTINCT FROM 'object' OR
    (SELECT count(*) FROM jsonb_object_keys(snapshot->'managed_state'))<>5 OR
    NOT (snapshot->'managed_state') ?& ARRAY['lifecycle','trailing_stops','expectations','active_intents','active_orders'] THEN
    RAISE EXCEPTION 'predecessor snapshot schema differs'; END IF;
  baseline:=p.operation_kind='MIGRATED_BASELINE';
  IF baseline THEN
    keys:=ARRAY['origin','missing_sources','migration_rows'];
    IF p.operation_seq<>1 OR p.formula_version<>'unknown:prior' OR p.before_snapshot IS NOT NULL OR
      evidence->>'origin' IS DISTINCT FROM 'UNKNOWN_PRIOR' OR
      evidence->'missing_sources' IS DISTINCT FROM '["PRIOR_OPERATIONS","ORDER_CREATION_INPUTS"]'::jsonb THEN
      RAISE EXCEPTION 'migration predecessor evidence differs'; END IF;
    rows:=evidence->'migration_rows';
    IF jsonb_typeof(rows) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(rows))<>6 OR
      NOT rows ?& ARRAY['lifecycle','position_trailing_stops','position_expectations','position_intents','suggested_orders','position_daily_facts'] THEN
      RAISE EXCEPTION 'migration predecessor row groups differ'; END IF;
  ELSE
    keys:=ARRAY['schema_version','origin','rows','command_result'];
    IF p.operation_kind<>'INTENT_OR_ORDER_CHANGED' OR p.formula_version<>'bound-order-state:v1' OR
      evidence->'schema_version' IS DISTINCT FROM '1'::jsonb OR evidence->>'origin' IS DISTINCT FROM 'LOCAL_STATUS_CAPTURE' OR
      evidence#>>'{command_result,outcome}' IS DISTINCT FROM p.outcome OR
      evidence#>>'{command_result,reason_code}' IS DISTINCT FROM p.reason_code OR
      (SELECT count(*) FROM jsonb_object_keys(evidence->'command_result'))<>4 OR
      NOT (evidence->'command_result') ?& ARRAY['outcome','reason_code','requested_status','missing_sources'] THEN
      RAISE EXCEPTION 'bound predecessor evidence differs'; END IF;
    rows:=evidence->'rows';
    IF jsonb_typeof(rows) IS DISTINCT FROM 'object' OR (SELECT count(*) FROM jsonb_object_keys(rows))<>5 OR
      NOT rows ?& ARRAY['lifecycle','trailing_stops','expectations','active_intents','active_orders'] THEN
      RAISE EXCEPTION 'bound predecessor row groups differ'; END IF;
  END IF;
  IF jsonb_typeof(evidence) IS DISTINCT FROM 'object' OR NOT evidence ?& keys OR
    (SELECT count(*) FROM jsonb_object_keys(evidence))<>cardinality(keys) THEN RAISE EXCEPTION 'predecessor evidence fields differ'; END IF;
  l:=rows->'lifecycle'; PERFORM public.lc_bound_check_image(l,'lifecycle');
  IF l->>'id' IS DISTINCT FROM p.lifecycle_id::text OR l->>'portfolio_id' IS DISTINCT FROM p.portfolio_id::text OR
    l->'state_version' IS DISTINCT FROM to_jsonb(p.state_version_after) OR
    l->>'lifecycle_policy_version_id' IS DISTINCT FROM p.policy_version_id::text THEN
    RAISE EXCEPTION 'predecessor lifecycle identity/version differs'; END IF;
  state:=jsonb_build_object('lifecycle',public.lc_bound_managed(l));
  FOR original,name IN VALUES ('position_trailing_stops','trailing_stops'),('position_expectations','expectations'),
    ('position_intents','active_intents'),('suggested_orders','active_orders'),('position_daily_facts','daily_facts') LOOP
    IF NOT baseline THEN
      IF name='daily_facts' THEN CONTINUE; END IF;
      original:=name;
    END IF;
    IF jsonb_typeof(rows->original) IS DISTINCT FROM 'array' OR
      (SELECT count(*) FROM jsonb_array_elements(rows->original))<>
      (SELECT count(DISTINCT a->>'id') FROM jsonb_array_elements(rows->original) a) THEN
      RAISE EXCEPTION 'predecessor member shape/identity differs'; END IF;
    members:='[]'::jsonb;
    FOR row_image IN SELECT value FROM jsonb_array_elements(rows->original) LOOP
      PERFORM public.lc_bound_check_image(row_image,name);
      IF row_image->>'lifecycle_id' IS DISTINCT FROM p.lifecycle_id::text THEN
        RAISE EXCEPTION 'predecessor child attribution differs'; END IF;
      IF baseline AND name='active_orders' AND row_image->>'status' NOT IN ('PROPOSED','EXECUTING','PARTIALLY_FILLED','RECONCILIATION_REQUIRED') THEN CONTINUE; END IF;
      IF baseline AND name='active_intents' AND row_image->>'status' NOT IN ('ACTIVE','EXECUTING','RECONCILIATION_REQUIRED') THEN CONTINUE; END IF;
      members:=members||jsonb_build_array(public.lc_bound_managed(row_image));
    END LOOP;
    IF name<>'daily_facts' THEN state:=state||jsonb_build_object(name,members); END IF;
  END LOOP;
  IF state IS DISTINCT FROM public.lc_bound_state(snapshot->'managed_state') THEN
    RAISE EXCEPTION 'predecessor physical/managed state differs'; END IF;
  IF jsonb_typeof(snapshot->'account_observations') IS DISTINCT FROM 'object' OR
    (SELECT count(*) FROM jsonb_object_keys(snapshot->'account_observations'))<>2 OR
    snapshot#>>'{account_observations,origin}' IS DISTINCT FROM 'ACCOUNT_OBSERVED' OR
    jsonb_typeof(snapshot#>'{account_observations,positions}') IS DISTINCT FROM 'array' THEN
    RAISE EXCEPTION 'predecessor account observation schema differs'; END IF;
  FOR row_image IN SELECT value FROM jsonb_array_elements(snapshot#>'{account_observations,positions}') LOOP
    PERFORM public.lc_bound_check_image(row_image,'positions');
    IF row_image->>'portfolio_id' IS DISTINCT FROM p.portfolio_id::text OR row_image->>'market' IS DISTINCT FROM l->>'market' OR
      row_image->>'symbol' IS DISTINCT FROM l->>'symbol' THEN RAISE EXCEPTION 'predecessor account attribution differs'; END IF;
  END LOOP;
END $$;

CREATE FUNCTION lc_bound_snapshot(lifecycle uuid, template jsonb) RETURNS jsonb
LANGUAGE plpgsql SET search_path=pg_catalog,public SET TimeZone='UTC' SET DateStyle='ISO,YMD' AS $$
DECLARE l public.position_lifecycle_states%ROWTYPE; name text; tab text; images jsonb; members jsonb;
  rows jsonb; state jsonb; positions jsonb; raw text; member jsonb;
BEGIN
  SELECT * INTO STRICT l FROM public.position_lifecycle_states WHERE id=lifecycle;
  rows := jsonb_build_object('lifecycle',public.lc_bound_image(row_to_json(l)::text));
  state := jsonb_build_object('lifecycle',public.lc_bound_managed(rows->'lifecycle'));
  FOR name,tab IN VALUES ('trailing_stops','position_trailing_stops'),('expectations','position_expectations'),
    ('active_intents','position_intents'),('active_orders','suggested_orders') LOOP
    members := template->name;
    IF jsonb_typeof(members) IS DISTINCT FROM 'array' THEN RAISE EXCEPTION 'fixed member array absent'; END IF;
    images := '[]'::jsonb;
    FOR member IN SELECT value FROM jsonb_array_elements(members) LOOP
      EXECUTE format('SELECT row_to_json(r)::text FROM public.%I r WHERE id=$1 AND lifecycle_id=$2',tab)
        INTO STRICT raw USING (member->>'id')::uuid,lifecycle;
      images := images || jsonb_build_array(public.lc_bound_image(raw));
    END LOOP;
    -- Preserve predecessor members after they become terminal. Never shrink a
    -- set by today's active filter, and refuse newly active unrecorded members.
    IF name='active_orders' THEN
      IF EXISTS (SELECT 1 FROM public.suggested_orders o WHERE o.lifecycle_id=lifecycle
        AND o.status IN ('PROPOSED','EXECUTING','PARTIALLY_FILLED','RECONCILIATION_REQUIRED')
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(members) m WHERE m->>'id'=o.id::text)) THEN
        RAISE EXCEPTION 'new active order is outside predecessor fixed members'; END IF;
    ELSIF name='active_intents' THEN
      IF EXISTS (SELECT 1 FROM public.position_intents i WHERE i.lifecycle_id=lifecycle
        AND i.status IN ('ACTIVE','EXECUTING','RECONCILIATION_REQUIRED')
        AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(members) m WHERE m->>'id'=i.id::text)) THEN
        RAISE EXCEPTION 'new active intent is outside predecessor fixed members'; END IF;
    ELSE
      EXECUTE format('SELECT count(*) FROM public.%I WHERE lifecycle_id=$1',tab) INTO raw USING lifecycle;
      IF raw::integer <> jsonb_array_length(members) THEN RAISE EXCEPTION 'fixed child member set differs'; END IF;
    END IF;
    rows := rows || jsonb_build_object(name,images);
    SELECT coalesce(jsonb_agg(public.lc_bound_managed(value) ORDER BY ordinal),'[]'::jsonb) INTO images
      FROM jsonb_array_elements(images) WITH ORDINALITY a(value,ordinal);
    state := state || jsonb_build_object(name,images);
  END LOOP;
  SELECT coalesce(jsonb_agg(public.lc_bound_image(row_to_json(p)::text) ORDER BY p.id),'[]'::jsonb) INTO positions
    FROM public.portfolio_positions p WHERE p.portfolio_id=l.portfolio_id AND p.market=l.market AND p.symbol=l.symbol;
  RETURN jsonb_build_object('managed_state',state,'account_observations',
    jsonb_build_object('origin','ACCOUNT_OBSERVED','positions',positions),'evidence',
    jsonb_build_object('schema_version',1,'origin','LOCAL_STATUS_CAPTURE','rows',rows));
END $$;

CREATE FUNCTION lc_bound_sources_payload(cmd uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path=pg_catalog,public AS $$
  SELECT coalesce(jsonb_agg(to_jsonb(s) ORDER BY s.source_role,s.source_ordinal),'[]'::jsonb)
  FROM public.quant_execution_operation_sources s WHERE s.business_command_id=cmd
$$;

CREATE FUNCTION lc_bound_validate(cmd uuid, check_projection boolean) RETURNS void
LANGUAGE plpgsql SET search_path=pg_catalog,public SET TimeZone='UTC' SET DateStyle='ISO,YMD' AS $$
DECLARE h public.lifecycle_business_commands%ROWTYPE; o public.position_lifecycle_operations%ROWTYPE;
  prev public.position_lifecycle_operations%ROWTYPE; prior public.lifecycle_business_commands%ROWTYPE;
  req jsonb; expected jsonb; payload jsonb; s public.quant_execution_operation_sources%ROWTYPE;
  before_image jsonb; after_image jsonb; cursor_image jsonb; c record; seq integer:=0;
  member jsonb; row_image jsonb; state jsonb; result jsonb; owner_before jsonb; owner_after jsonb;
BEGIN
  SELECT * INTO STRICT h FROM public.lifecycle_business_commands WHERE id=cmd;
  req := convert_from(h.canonical_request,'UTF8')::jsonb;
  IF h.command_kind <> 'ORDER_STATUS_CHANGED' OR h.selector_version <> 'bound-order-state:v1' OR
    h.expected_step_count<>1 OR h.expected_unbound_order_count<>0 OR h.expected_unbound_orders<>'[]'::jsonb OR
    h.canonical_request<>convert_to(public.lc_json_canonical(req),'UTF8') OR
    h.request_hash<>encode(sha256(h.canonical_request),'hex') OR
    req IS DISTINCT FROM jsonb_build_object('schema_version',1,'command_kind','ORDER_STATUS_CHANGED',
      'order_id',(req->>'order_id')::uuid::text,'status',req->>'status',
      'expected_revision',(req->>'expected_revision')::integer,'request_key',h.request_key) OR
    (check_projection AND h.captured_transaction_id<>pg_current_xact_id()::text) THEN
    RAISE EXCEPTION 'bound request/transaction differs'; END IF;
  IF h.command_seq>1 THEN
    SELECT * INTO STRICT prior FROM public.lifecycle_business_commands WHERE id=h.previous_command_id;
    IF prior.portfolio_id<>h.portfolio_id OR prior.command_seq+1<>h.command_seq OR
      h.previous_manifest_hash<>prior.manifest_hash OR prior.manifest_hash<>
      public.lc_json_sha(jsonb_build_object('steps',prior.expected_steps,'unbound_orders',prior.expected_unbound_orders)) THEN
      RAISE EXCEPTION 'bound command predecessor differs'; END IF;
  END IF;
  IF (SELECT count(*) FROM public.position_lifecycle_operations WHERE business_command_id=cmd)<>1 OR
    EXISTS(SELECT 1 FROM public.suggested_order_initial_inputs WHERE business_command_id=cmd) OR
    EXISTS(SELECT 1 FROM public.position_lifecycle_initial_inputs WHERE business_command_id=cmd) THEN
    RAISE EXCEPTION 'bound command operation/input set differs'; END IF;
  SELECT * INTO STRICT o FROM public.position_lifecycle_operations WHERE business_command_id=cmd;
  expected:=jsonb_build_array(jsonb_build_object('operation_id',o.id::text,'lifecycle_id',o.lifecycle_id::text,
    'command_step_no',1,'operation_kind','INTENT_OR_ORDER_CHANGED'));
  IF h.expected_steps<>expected OR h.manifest_hash<>
    public.lc_json_sha(jsonb_build_object('steps',expected,'unbound_orders','[]'::jsonb)) OR
    o.command_step_no<>1 OR o.operation_kind<>'INTENT_OR_ORDER_CHANGED' OR o.portfolio_id<>h.portfolio_id OR
    o.actor_type<>h.actor_type OR o.actor_ref<>h.actor_ref OR o.formula_version<>'bound-order-state:v1' OR
    o.effective_at IS NOT NULL OR o.effective_trade_date IS NOT NULL OR o.initial_input_hash IS NOT NULL THEN
    RAISE EXCEPTION 'bound operation identity differs'; END IF;
  payload:=to_jsonb(o)-ARRAY['recorded_at','canonical_payload','operation_hash'];
  IF o.canonical_payload<>convert_to(public.lc_json_canonical(payload),'UTF8') OR
    o.operation_hash<>public.lc_json_sha(payload) OR o.before_hash<>public.lc_json_sha(o.before_snapshot) OR
    o.after_hash<>public.lc_json_sha(o.after_snapshot) THEN RAISE EXCEPTION 'bound operation digest differs'; END IF;
  SELECT * INTO STRICT prev FROM public.position_lifecycle_operations WHERE id=o.previous_operation_id;
  PERFORM public.lc_bound_verify_previous(prev.id);
  payload:=to_jsonb(prev)-ARRAY['recorded_at','canonical_payload','operation_hash'];
  IF prev.lifecycle_id<>o.lifecycle_id OR prev.portfolio_id<>o.portfolio_id OR prev.operation_seq+1<>o.operation_seq OR
    prev.canonical_payload<>convert_to(public.lc_json_canonical(payload),'UTF8') OR
    prev.operation_hash<>public.lc_json_sha(payload) OR o.previous_hash<>prev.operation_hash OR
    prev.after_hash<>public.lc_json_sha(prev.after_snapshot) OR
    public.lc_bound_state(prev.after_snapshot->'managed_state') IS DISTINCT FROM o.before_snapshot->'managed_state' OR
    prev.policy_version_id<>o.policy_version_id OR prev.policy_content_hash<>o.policy_content_hash OR
    o.state_version_before<>prev.state_version_after OR o.state_version_before<>o.state_version_after THEN
    RAISE EXCEPTION 'bound predecessor/state continuity differs'; END IF;
  IF o.source_count<>1 OR (SELECT count(*) FROM public.quant_execution_operation_sources WHERE business_command_id=cmd)<>1 OR
    o.sources_sha256<>public.lc_json_sha(public.lc_bound_sources_payload(cmd)) OR
    o.changes_sha256<>public.lc_json_sha(public.lc_changes_payload(cmd)) THEN RAISE EXCEPTION 'bound evidence set differs'; END IF;
  SELECT * INTO STRICT s FROM public.quant_execution_operation_sources WHERE business_command_id=cmd;
  IF s.operation_id IS DISTINCT FROM o.id OR s.scope<>'LIFECYCLE' OR s.source_type<>'ORDER' OR
    s.source_role<>'ORDER_STATUS_INPUT' OR s.source_ordinal<>1 OR s.order_id::text<>req->>'order_id' OR
    s.source_revision<>(req->>'expected_revision')::integer OR s.source_content_hash<>public.lc_json_sha(s.source_snapshot) THEN
    RAISE EXCEPTION 'bound source identity differs'; END IF;
  SELECT value INTO STRICT before_image FROM jsonb_array_elements(o.before_snapshot#>'{evidence,rows,active_orders}')
    WHERE value->>'id'=req->>'order_id';
  SELECT value INTO STRICT after_image FROM jsonb_array_elements(o.after_snapshot#>'{evidence,rows,active_orders}')
    WHERE value->>'id'=req->>'order_id';
  IF before_image IS DISTINCT FROM s.source_snapshot OR
    before_image->>'lifecycle_id' IS DISTINCT FROM o.lifecycle_id::text OR
    before_image->>'portfolio_id' IS DISTINCT FROM o.portfolio_id::text OR
    before_image->>'revision' IS DISTINCT FROM req->>'expected_revision' THEN
    RAISE EXCEPTION 'bound order before image differs'; END IF;
  -- Rebuild every managed row from its exact original, then verify that only
  -- the target order's permitted fields changed. Account observations are not
  -- lifecycle-owned balances and do not determine continuity.
  FOR member IN SELECT value FROM jsonb_array_elements(jsonb_build_array(o.before_snapshot,o.after_snapshot)) LOOP
    PERFORM public.lc_bound_check_image(member#>'{evidence,rows,lifecycle}','lifecycle');
    state:=jsonb_build_object('lifecycle',public.lc_bound_managed(member#>'{evidence,rows,lifecycle}'));
    IF member#>'{evidence,rows,lifecycle}' IS DISTINCT FROM
      public.lc_bound_image(member#>>'{evidence,rows,lifecycle,__raw_row_json__}') THEN RAISE EXCEPTION 'bound lifecycle raw differs'; END IF;
    FOR c IN SELECT * FROM jsonb_each(member#>'{evidence,rows}') WHERE key<>'lifecycle' LOOP
      FOR row_image IN SELECT value FROM jsonb_array_elements(c.value) LOOP
        PERFORM public.lc_bound_check_image(row_image,c.key);
      END LOOP;
      IF EXISTS(SELECT 1 FROM jsonb_array_elements(c.value) a WHERE a IS DISTINCT FROM public.lc_bound_image(a->>'__raw_row_json__')) THEN
        RAISE EXCEPTION 'bound child raw differs'; END IF;
      state:=state||jsonb_build_object(c.key,(SELECT coalesce(jsonb_agg(public.lc_bound_managed(value) ORDER BY ordinal),'[]'::jsonb)
        FROM jsonb_array_elements(c.value) WITH ORDINALITY a(value,ordinal)));
    END LOOP;
    IF state IS DISTINCT FROM member->'managed_state' OR member#>>'{managed_state,lifecycle,state_version}' IS DISTINCT FROM o.state_version_after::text THEN
      RAISE EXCEPTION 'bound managed interpretation differs'; END IF;
  END LOOP;
  owner_before:=o.before_snapshot#>'{evidence,rows,lifecycle}'; owner_after:=o.after_snapshot#>'{evidence,rows,lifecycle}';
  IF owner_before->>'strategy_version_id' IS DISTINCT FROM coalesce(prev.after_snapshot#>>'{evidence,rows,lifecycle,strategy_version_id}',
      prev.after_snapshot#>>'{evidence,migration_rows,lifecycle,strategy_version_id}') OR
    owner_before->>'lifecycle_policy_version_id' IS DISTINCT FROM o.policy_version_id::text THEN
    RAISE EXCEPTION 'bound frozen owner differs'; END IF;
  IF owner_before IS DISTINCT FROM owner_after OR ((o.before_snapshot#>'{evidence,rows}') - 'active_orders') IS DISTINCT FROM
    ((o.after_snapshot#>'{evidence,rows}') - 'active_orders') THEN RAISE EXCEPTION 'bound command changes non-order state'; END IF;
  state:=o.before_snapshot->'managed_state';
  SELECT jsonb_agg(CASE WHEN value->>'id'=req->>'order_id' THEN public.lc_bound_managed(after_image) ELSE value END ORDER BY ordinal)
    INTO expected FROM jsonb_array_elements(state->'active_orders') WITH ORDINALITY a(value,ordinal);
  IF jsonb_set(state,'{active_orders}',expected) IS DISTINCT FROM o.after_snapshot->'managed_state' THEN
    RAISE EXCEPTION 'bound fixed member set or unrelated order differs'; END IF;
  cursor_image:=before_image;
  FOR c IN SELECT * FROM public.quant_execution_operation_changes WHERE business_command_id=cmd ORDER BY change_seq LOOP
    seq:=seq+1;
    IF c.change_seq<>seq OR c.operation_id IS DISTINCT FROM o.id OR c.scope<>'LIFECYCLE' OR c.entity_type<>'ORDER' OR
      c.row_id::text<>req->>'order_id' OR c.change_kind<>'UPDATE' OR c.binding_side<>'BOTH' OR
      c.before_row IS DISTINCT FROM cursor_image OR c.after_row IS DISTINCT FROM public.lc_bound_image(c.after_row->>'__raw_row_json__') OR
      (c.before_row-ARRAY['__raw_row_json__','status','revision','updated_at']) IS DISTINCT FROM
      (c.after_row-ARRAY['__raw_row_json__','status','revision','updated_at']) OR
      (c.after_row->>'revision')::integer<>(c.before_row->>'revision')::integer+1 THEN
      RAISE EXCEPTION 'bound change chain/permitted fields differs'; END IF;
    cursor_image:=c.after_row;
  END LOOP;
  result:=o.after_snapshot#>'{evidence,command_result}';
  IF cursor_image IS DISTINCT FROM after_image OR o.change_count<>seq OR result IS DISTINCT FROM
    jsonb_build_object('outcome',o.outcome,'reason_code',o.reason_code,'requested_status',req->>'status','missing_sources',result->'missing_sources') OR
    jsonb_typeof(result->'missing_sources') IS DISTINCT FROM 'array' OR
    EXISTS(SELECT 1 FROM jsonb_array_elements(result->'missing_sources') m WHERE jsonb_typeof(m)<>'string' OR length(trim(m#>>'{}'))=0) OR
    (SELECT count(*) FROM jsonb_array_elements(result->'missing_sources'))<>
    (SELECT count(DISTINCT m) FROM jsonb_array_elements(result->'missing_sources') m) OR
    (o.outcome='APPLIED' AND (seq=0 OR after_image->>'status' IS DISTINCT FROM req->>'status' OR result->'missing_sources'<>'[]'::jsonb)) OR
    (o.outcome IN ('BLOCKED','NO_STATE_CHANGE') AND (seq<>0 OR before_image IS DISTINCT FROM after_image)) OR
    (o.outcome='NO_STATE_CHANGE' AND after_image->>'status' IS DISTINCT FROM req->>'status') THEN
    RAISE EXCEPTION 'bound result effect differs'; END IF;
  IF check_projection AND (public.lc_bound_snapshot(o.lifecycle_id,o.after_snapshot->'managed_state')->'managed_state'
    IS DISTINCT FROM o.after_snapshot->'managed_state' OR
    public.lc_bound_snapshot(o.lifecycle_id,o.after_snapshot->'managed_state')#>'{evidence,rows}' IS DISTINCT FROM o.after_snapshot#>'{evidence,rows}') THEN
    RAISE EXCEPTION 'bound sealed state differs from actual projection'; END IF;
END $$;
 
CREATE FUNCTION lc_status_audit_guard() RETURNS trigger
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

CREATE FUNCTION lc_status_order_capture() RETURNS trigger
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

CREATE FUNCTION lc_status_managed_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  IF nullif(current_setting('liveprofit.lifecycle_command',true),'') IS NOT NULL OR EXISTS(
    SELECT 1 FROM public.lifecycle_business_commands WHERE command_kind='ORDER_STATUS_CHANGED'
      AND captured_transaction_id=pg_current_xact_id()::text) THEN
    RAISE EXCEPTION 'status command cannot modify other managed state'; END IF;
  RETURN NULL;
END $$;

CREATE FUNCTION lc_status_command_check() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  IF NEW.selector_version='bound-order-state:v1' THEN PERFORM public.lc_bound_validate(NEW.id,true);
  ELSE PERFORM public.lc_validate_unbound_command(NEW.id,true); END IF;
  RETURN NULL;
END $$;

CREATE FUNCTION lc_status_operation_check() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$
BEGIN
  PERFORM public.lc_bound_validate(NEW.business_command_id,true);
  RETURN NULL;
END $$;
'''


ENTRY_SQL = r'''
CREATE FUNCTION lc_record_bound_order_status(portfolio uuid, canonical_request bytea, actor_ref text)
RETURNS TABLE(command_id uuid, replayed boolean)
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, public SET TimeZone = 'UTC' SET DateStyle = 'ISO, YMD' AS $$
DECLARE req jsonb; expected_req jsonb; o public.suggested_orders%ROWTYPE;
  h public.lifecycle_business_commands%ROWTYPE; prior public.lifecycle_business_commands%ROWTYPE;
  cmd uuid; manifest jsonb; before_image jsonb; after_image jsonb; result jsonb;
  l public.position_lifecycle_states%ROWTYPE; observed public.position_lifecycle_states%ROWTYPE;
  policy public.lifecycle_policy_versions%ROWTYPE; prev public.position_lifecycle_operations%ROWTYPE;
  operation public.position_lifecycle_operations%ROWTYPE; before_snapshot jsonb; after_snapshot jsonb;
  step_id uuid; observed_lifecycle uuid;
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
    PERFORM public.lc_bound_validate(h.id,false);
    RETURN QUERY SELECT h.id,true;
    RETURN;
  END IF;
  SELECT lifecycle_id INTO STRICT observed_lifecycle FROM public.suggested_orders WHERE id=order_id AND portfolio_id=portfolio;
  IF observed_lifecycle IS NULL THEN RAISE EXCEPTION 'bound order required'; END IF;
  SELECT * INTO STRICT observed FROM public.position_lifecycle_states WHERE id=observed_lifecycle AND portfolio_id=portfolio;
  PERFORM 1 FROM public.quant_strategy_versions WHERE id=observed.strategy_version_id FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'bound strategy version absent'; END IF;
  SELECT * INTO STRICT policy FROM public.lifecycle_policy_versions WHERE id=observed.lifecycle_policy_version_id FOR UPDATE;
  SELECT * INTO STRICT l FROM public.position_lifecycle_states WHERE id=observed_lifecycle FOR UPDATE;
  IF l.portfolio_id<>portfolio OR l.strategy_version_id<>observed.strategy_version_id OR
    l.lifecycle_policy_version_id<>observed.lifecycle_policy_version_id THEN RAISE EXCEPTION 'bound owner changed during selection'; END IF;
  PERFORM 1 FROM public.suggested_orders WHERE lifecycle_id=l.id ORDER BY id FOR UPDATE;
  SELECT * INTO STRICT o FROM public.suggested_orders WHERE id=order_id FOR UPDATE;
  IF o.portfolio_id<>portfolio OR o.lifecycle_id IS DISTINCT FROM l.id OR o.market<>l.market OR o.symbol<>l.symbol THEN
    RAISE EXCEPTION 'bound order attribution changed during selection'; END IF;
  SELECT * INTO STRICT prev FROM public.position_lifecycle_operations WHERE lifecycle_id=l.id ORDER BY operation_seq DESC LIMIT 1;
  PERFORM public.lc_bound_verify_previous(prev.id);
  IF prev.policy_version_id<>policy.id OR prev.policy_content_hash<>policy.content_hash OR
    coalesce(prev.after_snapshot#>>'{evidence,rows,lifecycle,strategy_version_id}',
             prev.after_snapshot#>>'{evidence,migration_rows,lifecycle,strategy_version_id}') IS DISTINCT FROM l.strategy_version_id::text OR
    coalesce(prev.after_snapshot#>>'{evidence,rows,lifecycle,lifecycle_policy_version_id}',
             prev.after_snapshot#>>'{evidence,migration_rows,lifecycle,lifecycle_policy_version_id}') IS DISTINCT FROM policy.id::text THEN
    RAISE EXCEPTION 'bound policy/owner drift'; END IF;
  IF NOT EXISTS(SELECT 1 FROM jsonb_array_elements(prev.after_snapshot#>'{managed_state,active_orders}') m WHERE m->>'id'=o.id::text) THEN
    RAISE EXCEPTION 'target order absent from predecessor fixed members'; END IF;
  PERFORM 1 FROM public.position_trailing_stops WHERE lifecycle_id=l.id ORDER BY id FOR UPDATE;
  PERFORM 1 FROM public.position_expectations WHERE lifecycle_id=l.id ORDER BY id FOR UPDATE;
  PERFORM 1 FROM public.position_intents WHERE lifecycle_id=l.id ORDER BY id FOR UPDATE;
  PERFORM 1 FROM public.portfolio_positions WHERE portfolio_id=portfolio AND market=l.market AND symbol=l.symbol ORDER BY id FOR UPDATE;
  before_snapshot:=public.lc_bound_snapshot(l.id,prev.after_snapshot->'managed_state');
  IF before_snapshot->'managed_state' IS DISTINCT FROM public.lc_bound_state(prev.after_snapshot->'managed_state') THEN
    RAISE EXCEPTION 'bound predecessor/state continuity differs'; END IF;
  IF o.revision <> expected_revision THEN RAISE EXCEPTION 'order revision conflict'; END IF;
  before_image := public.lc_bound_image(row_to_json(o)::text);
  SELECT * INTO prior FROM public.lifecycle_business_commands c WHERE c.portfolio_id=portfolio ORDER BY c.command_seq DESC LIMIT 1;
  cmd := gen_random_uuid();
  step_id:=gen_random_uuid();
  manifest:=jsonb_build_object('steps',jsonb_build_array(jsonb_build_object('operation_id',step_id::text,
    'lifecycle_id',l.id::text,'command_step_no',1,'operation_kind','INTENT_OR_ORDER_CHANGED')),'unbound_orders','[]'::jsonb);
  PERFORM set_config('liveprofit.lifecycle_command',cmd::text,true);
  INSERT INTO public.lifecycle_business_commands
    (id,portfolio_id,request_key,command_seq,previous_command_id,previous_manifest_hash,command_kind,request_schema_version,
     canonical_request,request_hash,selector_version,expected_steps,expected_unbound_orders,manifest_hash,
     expected_step_count,expected_unbound_order_count,actor_type,actor_ref)
  VALUES (cmd,portfolio,req->>'request_key',coalesce(prior.command_seq,0)+1,prior.id,prior.manifest_hash,
    'ORDER_STATUS_CHANGED',1,canonical_request,encode(sha256(canonical_request),'hex'),'bound-order-state:v1',
    manifest->'steps',manifest->'unbound_orders',public.lc_json_sha(manifest),1,0,'USER',actor_ref);
  INSERT INTO public.quant_execution_operation_sources
    (id,business_command_id,operation_id,source_role,source_ordinal,source_type,scope,order_id,source_revision,source_snapshot,source_content_hash)
    VALUES(gen_random_uuid(),cmd,step_id,'ORDER_STATUS_INPUT',1,'ORDER','LIFECYCLE',o.id,o.revision,before_image,public.lc_json_sha(before_image));
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
  after_snapshot:=public.lc_bound_snapshot(l.id,prev.after_snapshot->'managed_state');
  after_snapshot:=jsonb_set(after_snapshot,'{evidence,command_result}',jsonb_build_object(
    'outcome',outcome,'reason_code',reason,'requested_status',requested,'missing_sources',missing));
  operation.id:=step_id; operation.lifecycle_id:=l.id; operation.portfolio_id:=portfolio;
  operation.operation_seq:=prev.operation_seq+1; operation.previous_operation_id:=prev.id;
  operation.business_command_id:=cmd; operation.command_step_no:=1;
  operation.operation_kind:='INTENT_OR_ORDER_CHANGED'; operation.outcome:=outcome; operation.reason_code:=reason;
  operation.actor_type:='USER'; operation.actor_ref:=actor_ref; operation.recorded_at:=clock_timestamp();
  operation.policy_version_id:=policy.id; operation.policy_content_hash:=policy.content_hash;
  operation.formula_version:='bound-order-state:v1'; operation.snapshot_schema_version:=1;
  operation.before_snapshot:=before_snapshot; operation.after_snapshot:=after_snapshot;
  operation.state_version_before:=l.state_version; operation.state_version_after:=l.state_version;
  operation.source_count:=1; operation.change_count:=jsonb_array_length(public.lc_changes_payload(cmd));
  operation.sources_sha256:=public.lc_json_sha(public.lc_bound_sources_payload(cmd));
  operation.changes_sha256:=public.lc_json_sha(public.lc_changes_payload(cmd)); operation.previous_hash:=prev.operation_hash;
  operation.before_hash:=public.lc_json_sha(before_snapshot); operation.after_hash:=public.lc_json_sha(after_snapshot);
  result:=to_jsonb(operation)-ARRAY['recorded_at','canonical_payload','operation_hash'];
  operation.canonical_payload:=convert_to(public.lc_json_canonical(result),'UTF8'); operation.operation_hash:=public.lc_json_sha(result);
  INSERT INTO public.position_lifecycle_operations SELECT (operation).*;
  PERFORM set_config('liveprofit.lifecycle_command','',true);
  RETURN QUERY SELECT cmd,false;
END $$;
'''

AUDIT_TABLES = ("lifecycle_business_commands", "quant_execution_operation_sources",
                "quant_execution_operation_changes", "position_lifecycle_operations")
MANAGED_TABLES = ("position_lifecycle_states", "position_trailing_stops", "position_expectations",
                  "position_intents", "position_daily_facts", "portfolio_positions")
FUNCTIONS = (
    "lc_record_bound_order_status(uuid,bytea,text)", "lc_status_operation_check()", "lc_status_command_check()",
    "lc_status_managed_guard()", "lc_status_order_capture()", "lc_status_audit_guard()",
    "lc_bound_validate(uuid,boolean)", "lc_bound_sources_payload(uuid)", "lc_bound_snapshot(uuid,jsonb)",
    "lc_bound_verify_previous(uuid)", "lc_bound_check_image(jsonb,text)", "lc_bound_state(jsonb)", "lc_bound_managed(jsonb)", "lc_bound_image(text)", "lc_bound_normalize(jsonb)",
)


def _lock():
    op.execute("LOCK TABLE portfolios IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE quant_strategy_versions, lifecycle_policy_versions IN ACCESS EXCLUSIVE MODE")
    op.execute("LOCK TABLE position_lifecycle_states, suggested_orders IN ACCESS EXCLUSIVE MODE")
    for table in (*MANAGED_TABLES[1:], *AUDIT_TABLES):
        op.execute(f"LOCK TABLE {table} IN ACCESS EXCLUSIVE MODE")


def upgrade():
    _lock()
    op.execute("""DO $$ BEGIN IF EXISTS (
      SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relname IN
        ('lifecycle_business_commands','quant_execution_operation_sources','quant_execution_operation_changes','position_lifecycle_operations')
        AND pg_get_userbyid(c.relowner)<>current_user)
      THEN RAISE EXCEPTION '0053 must be installed by the audit table owner'; END IF; END $$""")
    op.execute(SQL)
    op.execute(ENTRY_SQL)
    for signature in FUNCTIONS:
        op.execute(f"REVOKE ALL ON FUNCTION {signature} FROM PUBLIC")
    for table in AUDIT_TABLES:
        op.execute(f"DROP TRIGGER lc_history_capture_closed ON {table}")
        op.execute(f"CREATE TRIGGER lc_history_capture_closed BEFORE INSERT ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION lc_status_audit_guard()")
    op.execute("DROP TRIGGER lc_unbound_order_capture ON suggested_orders")
    op.execute("CREATE TRIGGER lc_unbound_order_capture AFTER INSERT OR UPDATE OR DELETE ON suggested_orders "
               "FOR EACH ROW EXECUTE FUNCTION lc_status_order_capture()")
    op.execute("DROP TRIGGER lc_unbound_command_complete ON lifecycle_business_commands")
    op.execute("CREATE CONSTRAINT TRIGGER lc_unbound_command_complete AFTER INSERT ON lifecycle_business_commands "
               "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lc_status_command_check()")
    op.execute("CREATE TRIGGER lc_bound_operation_seal AFTER INSERT ON position_lifecycle_operations "
               "FOR EACH ROW EXECUTE FUNCTION lc_status_operation_check()")
    for table in MANAGED_TABLES:
        op.execute(f"CREATE TRIGGER lc_status_scope_guard AFTER INSERT OR UPDATE OR DELETE ON {table} "
                   "FOR EACH ROW EXECUTE FUNCTION lc_status_managed_guard()")
        op.execute(f"CREATE TRIGGER lc_status_truncate_guard BEFORE TRUNCATE ON {table} "
                   "FOR EACH STATEMENT EXECUTE FUNCTION lc_status_managed_guard()")
    op.execute("CREATE TRIGGER lc_status_truncate_guard BEFORE TRUNCATE ON suggested_orders "
               "FOR EACH STATEMENT EXECUTE FUNCTION lc_status_managed_guard()")


def downgrade():
    _lock()
    op.execute("DROP TRIGGER lc_status_truncate_guard ON suggested_orders")
    for table in MANAGED_TABLES:
        op.execute(f"DROP TRIGGER lc_status_scope_guard ON {table}")
        op.execute(f"DROP TRIGGER lc_status_truncate_guard ON {table}")
    op.execute("DROP TRIGGER lc_bound_operation_seal ON position_lifecycle_operations")
    op.execute("DROP TRIGGER lc_unbound_command_complete ON lifecycle_business_commands")
    op.execute("CREATE CONSTRAINT TRIGGER lc_unbound_command_complete AFTER INSERT ON lifecycle_business_commands "
               "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION lc_unbound_command_deferred_check()")
    op.execute("DROP TRIGGER lc_unbound_order_capture ON suggested_orders")
    op.execute("CREATE TRIGGER lc_unbound_order_capture AFTER INSERT OR UPDATE OR DELETE ON suggested_orders "
               "FOR EACH ROW EXECUTE FUNCTION lc_capture_unbound_order_change()")
    for table in AUDIT_TABLES:
        op.execute(f"DROP TRIGGER lc_history_capture_closed ON {table}")
        function = "lifecycle_history_capture_not_ready" if table == "position_lifecycle_operations" else "lc_unbound_audit_insert_guard"
        op.execute(f"CREATE TRIGGER lc_history_capture_closed BEFORE INSERT ON {table} "
                   f"FOR EACH ROW EXECUTE FUNCTION {function}()")
    for signature in FUNCTIONS:
        op.execute(f"DROP FUNCTION {signature}")
