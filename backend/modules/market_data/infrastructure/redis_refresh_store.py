"""Rebuildable refresh state. Every resource mutation uses one Redis Lua CAS.

The resource revision fences job, counter and budget changes together; PG remains
the source of truth and the session advisory lock protects actual collection.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

ACTIVE = {"QUEUED", "RUNNING", "RETRY_WAIT"}
TERMINAL = {"SUCCEEDED", "PARTIAL", "FAILED", "CANCELLED"}
AUTO_BLOCKING_ERRORS = {"SOURCE_NETWORK_ACCESS_DENIED"}

_CAS = """
local current = redis.call('GET', KEYS[1]) or ''
if current ~= ARGV[1] then return 0 end
local ops = cjson.decode(ARGV[2])
for _,op in ipairs(ops) do
  local cmd = op[1]
  local key = KEYS[op[2]]
  if cmd == 'set' then
    redis.call('SET', key, op[3])
    if op[4] and op[4] > 0 then redis.call('EXPIRE', key, op[4]) end
  elseif cmd == 'del' then redis.call('DEL', key)
  elseif cmd == 'expire' then redis.call('EXPIRE', key, op[3])
  elseif cmd == 'persist' then redis.call('PERSIST', key)
  elseif cmd == 'zadd' then redis.call('ZADD', key, op[3], op[4])
  elseif cmd == 'zrem' then redis.call('ZREM', key, op[3])
  elseif cmd == 'trim' then redis.call('ZREMRANGEBYSCORE', key, '-inf', op[3])
  end
end
return 1
"""


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value, default=None):
    return json.loads(value) if value else ({} if default is None else default)


def utc_text(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat() if timestamp is not None else None


class RedisRefreshStore:
    def __init__(self, redis, config):
        self.redis, self.config = redis, config
        self.prefix = config.key_prefix
        self._cas = redis.register_script(_CAS)

    def key(self, suffix):
        return self.prefix + suffix

    def ping(self):
        return self.redis.ping()

    def job(self, job_id):
        return _load(self.redis.get(self.key(f"job:{job_id}"))) or None

    def context(self, resource, target_date, now):
        raw = self.redis.get(self.key(f"resource:{resource}"))
        if isinstance(raw, bytes):
            raw = raw.decode()
        state = _load(raw)
        job = self.job(state["current_job_id"]) if state.get("current_job_id") else None
        attempts = _load(self.redis.get(self.key(f"attempts:{resource}:{target_date}")))
        budget = self.redis.zrangebyscore(self.key(f"budget:{resource}"), f"({now - self.config.rolling_window_seconds}", "+inf", withscores=True)
        return {"raw": raw or "", "state": state, "job": job, "attempts": attempts, "budget": budget}

    def _mutate(self, resource, target_date, now, mutate):
        for _ in range(32):
            ctx = self.context(resource, target_date, now)
            operations = []
            def op(command, suffix, *args):
                operations.append((command, self.key(suffix), *args))
            result = mutate(ctx, op)
            if not operations:
                return result
            ctx["state"]["revision"] = uuid4().hex
            operations.append(("set", self.key(f"resource:{resource}"), _json(ctx["state"]), 0))
            keys = [self.key(f"resource:{resource}")]
            encoded = []
            for cmd, key, *args in operations:
                if key not in keys:
                    keys.append(key)
                encoded.append([cmd, keys.index(key) + 1, *args])
            if self._cas(keys=keys, args=[ctx["raw"], _json(encoded)]):
                return result
        raise RuntimeError("refresh state changed too frequently")

    def eligibility(self, ctx, mode, now):
        c, state, attempts = self.config, ctx["state"], ctx["attempts"]
        job = ctx["job"]
        if (job and mode == "retry" and not c.auto_enabled and job["mode"] == "auto"
                and job["status"] in {"QUEUED", "RETRY_WAIT"}):
            job = None  # Explicit retry may replace an automatic task paused by the switch.
        reason, due = None, None
        if not c.enabled:
            reason = "REFRESH_DISABLED"
        elif mode == "auto" and not c.auto_enabled:
            reason = "AUTO_DISABLED"
        elif job and job["status"] in ACTIVE:
            reason = "JOB_ACTIVE"
            if job["status"] == "RETRY_WAIT":
                due = job.get("retry_at")
        elif mode == "auto" and state.get("delivery_blocked"):
            reason = "DELIVERY_UNCONFIRMED"
        elif mode == "auto" and attempts.get("auto_blocked_error"):
            reason = attempts["auto_blocked_error"]
        elif mode == "auto" and attempts.get("auto_count", 0) >= c.auto_max_attempts:
            reason = "AUTO_RETRY_LIMIT"
        elif len(ctx["budget"]) >= c.rolling_max_attempts:
            reason, due = "RETRY_LIMIT", ctx["budget"][0][1] + c.rolling_window_seconds
        else:
            last = attempts.get("last_started_at")
            if mode == "retry":
                last = max(last or 0, state.get("delivery_failed_at", 0)) or None
                due = last + c.manual_cooldown_seconds if last is not None else None
            else:
                due = attempts.get("auto_retry_at")
            if due is not None and now < due:
                reason = "COOLDOWN"
            else:
                due = None
        return {"allowed": reason is None, "reason": reason or "MISSING_DATA", "next_retry_at": utc_text(due)}

    def ensure(self, resource, target_date, spec, mode, now, trigger="PAGE"):
        def mutation(ctx, op):
            state, job = ctx["state"], ctx["job"]
            cancelled_id = None
            if job and job["status"] in ACTIVE:
                manual_takeover = (mode == "retry" and not self.config.auto_enabled
                                   and job["mode"] == "auto" and job["status"] != "RUNNING")
                if not manual_takeover and (job["target_trade_date"] == target_date or job["status"] == "RUNNING"):
                    return {"resource": resource, "decision": "IN_PROGRESS", "job_id": job["id"], "reason": "JOB_ACTIVE", "next_retry_at": utc_text(job.get("retry_at"))}
                job.update(status="CANCELLED", finished_at=utc_text(now))
                op("set", f"job:{job['id']}", _json(job), self.config.terminal_ttl_seconds)
                cancelled_id = job["id"]
                ctx["job"] = None
            decision = self.eligibility(ctx, mode, now)
            if not decision["allowed"]:
                # 取消的 job 不再被准入替代：清掉指向它的状态指针，避免状态
                # 面板继续显示一个已 CANCELLED 的任务（仅在指针未被他方更新时）。
                if cancelled_id and state.get("current_job_id") == cancelled_id:
                    state.pop("current_job_id", None)
                return {"resource": resource, "decision": "COOLDOWN" if decision["reason"] == "COOLDOWN" else "BLOCKED", "job_id": None, **{k: v for k, v in decision.items() if k != "allowed"}}
            old_target = state.get("target_trade_date")
            if old_target and old_target != target_date:
                op("expire", f"attempts:{resource}:{old_target}", self.config.retired_attempts_ttl_seconds)
            job_id = str(uuid4())
            job = dict(id=job_id, resource=resource, target_trade_date=target_date, target_spec=spec, mode=mode, trigger=trigger,
                       status="QUEUED", attempt=0, processed=0, total=len(spec.get("units", [])), created_at=utc_text(now),
                       started_at=None, heartbeat_at=None, finished_at=None, error_code=None, error_summary=None,
                       dispatch_count=0, dispatch_at=None, dispatch_epoch=uuid4().hex)
            state.update(current_job_id=job_id, target_trade_date=target_date)
            if mode == "retry":
                state.update(delivery_blocked=False)
                attempts = ctx["attempts"]
                attempts.pop("auto_blocked_error", None)
                op("set", f"attempts:{resource}:{target_date}", _json(attempts), 0)
            op("set", f"job:{job_id}", _json(job), 0)
            op("persist", f"attempts:{resource}:{target_date}")
            return {"resource": resource, "decision": "QUEUED", "job_id": job_id, "reason": "MISSING_DATA", "next_retry_at": None}
        return self._mutate(resource, target_date, now, mutation)

    def _job_change(self, job_id, now, change):
        initial = self.job(job_id)
        if not initial:
            return None
        def mutation(ctx, op):
            job = ctx["job"]
            if not job or job["id"] != job_id:
                return None
            return change(ctx, job, op)
        return self._mutate(initial["resource"], initial["target_trade_date"], now, mutation)

    def dispatch(self, job_id, now, *, online, idle):
        def change(ctx, job, op):
            if job["status"] != "QUEUED" or not online or (job["dispatch_count"] and not idle):
                return False
            count = job["dispatch_count"]
            if count and now < job["dispatch_at"] + self.config.dispatch_retry_delays_seconds[min(count-1, len(self.config.dispatch_retry_delays_seconds)-1)]:
                return False
            if count >= self.config.max_dispatches:
                job.update(status="FAILED", finished_at=utc_text(now), error_code="DELIVERY_UNCONFIRMED", error_summary="更新任务未被执行，请检查采集服务后重试")
                ctx["state"].update(delivery_blocked=True, delivery_failed_at=now)
                op("set", f"job:{job_id}", _json(job), self.config.terminal_ttl_seconds)
                return False
            job.update(dispatch_count=count+1, dispatch_at=now)
            op("set", f"job:{job_id}", _json(job), 0)
            return True
        return self._job_change(job_id, now, change)

    def claim(self, job_id, now):
        def change(ctx, job, op):
            if job["status"] != "QUEUED" or not self.config.enabled:
                return None
            # Ignore the job itself while rechecking the budget at execution time.
            check = {**ctx, "job": None}
            eligibility = self.eligibility(check, job["mode"], now)
            if not eligibility["allowed"]:
                job.update(status="CANCELLED", error_code=eligibility["reason"], finished_at=utc_text(now))
                op("set", f"job:{job_id}", _json(job), self.config.terminal_ttl_seconds)
                # 与 ensure 取消路径同口径：指针仍指向被取消 job 时清掉，
                # 避免状态面板继续显示该已取消任务。
                if ctx["state"].get("current_job_id") == job_id:
                    ctx["state"].pop("current_job_id", None)
                return None
            token = uuid4().hex
            job.update(status="RUNNING", attempt=job["attempt"]+1, run_token=token, started_at=utc_text(now), heartbeat_at=utc_text(now), processed=0)
            counters = ctx["attempts"]
            job["previous_attempts"] = dict(counters)
            counters["last_started_at"] = now
            if job["mode"] == "auto":
                counters["auto_count"] = counters.get("auto_count", 0)+1
            member = f"{job_id}:{job['attempt']}"
            job["budget_member"] = member
            op("set", f"job:{job_id}", _json(job), 0)
            op("set", f"lock:{job['resource']}", token, self.config.lease_ttl_seconds)
            op("set", f"attempts:{job['resource']}:{job['target_trade_date']}", _json(counters), 0)
            op("trim", f"budget:{job['resource']}", now-self.config.rolling_window_seconds)
            op("zadd", f"budget:{job['resource']}", now, member)
            op("expire", f"budget:{job['resource']}", self.config.budget_ttl_seconds)
            return job
        return self._job_change(job_id, now, change)

    def owns(self, job_id, token):
        job = self.job(job_id)
        if not job or job.get("run_token") != token or job["status"] != "RUNNING":
            return False
        value = self.redis.get(self.key(f"lock:{job['resource']}"))
        return value in (token, token.encode())

    def heartbeat(self, job_id, token, now, progress=None):
        def change(ctx, job, op):
            if job["status"] != "RUNNING" or job.get("run_token") != token or not self.owns(job_id, token):
                return False
            job["heartbeat_at"] = utc_text(now)
            if progress:
                job.update({k: progress[k] for k in ("processed", "total") if k in progress})
            op("set", f"job:{job_id}", _json(job), 0)
            op("set", f"lock:{job['resource']}", token, self.config.lease_ttl_seconds)
            return True
        return self._job_change(job_id, now, change)

    def release_busy(self, job_id, token, now):
        def change(ctx, job, op):
            if job["status"] != "RUNNING" or job.get("run_token") != token:
                return False
            job.update(status="QUEUED", run_token=None)
            previous_attempts = job.pop("previous_attempts", {})
            op("set", f"job:{job_id}", _json(job), 0)
            op("set", f"attempts:{job['resource']}:{job['target_trade_date']}", _json(previous_attempts), 0)
            op("zrem", f"budget:{job['resource']}", job["budget_member"])
            op("del", f"lock:{job['resource']}")
            return True
        return self._job_change(job_id, now, change)

    def finish(self, job_id, token, now, *, complete=False, partial=False, error_code=None, result=None, recovered=False):
        def change(ctx, job, op):
            if job["status"] not in ACTIVE:
                return False
            if job["status"] != "RUNNING" or job.get("run_token") != token:
                return False
            summary = None if complete else "仍有行情缺口，已保存成功入库的数据"
            if error_code == "SOURCE_NETWORK_ACCESS_DENIED":
                summary = "采集进程的出站网络连接被本机策略拒绝；请修复 Worker 网络权限后手动重试。自动重试已暂停。"
            job.update(result=result or {}, error_code=error_code, error_summary=summary, run_token=None)
            attempts = ctx["attempts"]
            if complete:
                job["status"] = "SUCCEEDED"
            elif error_code in AUTO_BLOCKING_ERRORS:
                # A local network ACL denial will not heal through timed retries.
                # Stop automatic redispatch until an operator explicitly retries.
                attempts["auto_blocked_error"] = error_code
                attempts.pop("auto_retry_at", None)
                job["status"] = "PARTIAL" if partial else "FAILED"
            elif job["mode"] == "auto" and attempts.get("auto_count", 0) < self.config.auto_max_attempts and len(ctx["budget"]) < self.config.rolling_max_attempts:
                job["status"] = "RETRY_WAIT"
                delay = self.config.auto_retry_delays_seconds[min(max(attempts.get("auto_count", 1)-1, 0), len(self.config.auto_retry_delays_seconds)-1)]
                job["retry_at"] = now+delay
                attempts["auto_retry_at"] = now+delay
            else:
                job["status"] = "PARTIAL" if partial else "FAILED"
            terminal = job["status"] in TERMINAL
            if terminal:
                job["finished_at"] = utc_text(now)
            op("set", f"job:{job_id}", _json(job), self.config.terminal_ttl_seconds if terminal else 0)
            op("set", f"attempts:{job['resource']}:{job['target_trade_date']}", _json(attempts), 0)
            op("del", f"lock:{job['resource']}")
            return True
        return self._job_change(job_id, now, change)

    def retry_due(self, job_id, now):
        def change(ctx, job, op):
            if job["status"] != "RETRY_WAIT" or now < job.get("retry_at", float("inf")):
                return False
            check = {**ctx, "job": None}
            if not self.eligibility(check, "auto", now)["allowed"]:
                return False
            job.update(status="QUEUED", dispatch_count=0, dispatch_at=None, dispatch_epoch=uuid4().hex)
            op("set", f"job:{job_id}", _json(job), 0)
            return True
        return self._job_change(job_id, now, change)

    def worker(self, worker_id, now, *, busy=False, job_id=None, boot_id=None):
        self.redis.set(self.key(f"worker:{worker_id}"), _json(dict(boot_id=boot_id, state="busy" if busy else "idle", current_job_id=job_id, heartbeat_at=utc_text(now))), ex=self.config.worker_ttl_seconds)

    def workers(self):
        keys = list(self.redis.scan_iter(match=self.key("worker:*"), count=100))
        return [_load(v) for v in self.redis.mget(keys) if v] if keys else []

    def changed(self, resource):
        with self.redis.pipeline() as pipe:
            pipe.set(self.key(f"changed:{resource}"), uuid4().hex, ex=self.config.changed_ttl_seconds)
            pipe.delete(self.key(f"coverage:{resource}"))
            pipe.execute()

    def factor_gate(self, symbol, from_date, to_date):
        return bool(self.redis.set(self.key(f"factor_cooldown:{symbol}:{from_date}:{to_date}"), "1", nx=True, ex=self.config.factor_cooldown_seconds))
