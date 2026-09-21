"""受限子进程执行器（plan 4.1.1）。

- `sys.executable -I` 一次性子进程：空 cwd、最小环境、close_fds=True、
  JSON stdin/stdout、stdout 上限 4KiB、300ms wall-clock（可调）。
- 子进程只注入空 builtins 和五个安全函数（abs/min/max/round/isfinite）；
  异常/超时/协议或业务输出错误 fail-closed 转 HOLD（错误码
  EXECUTION_TIMEOUT/INVALID_OUTPUT），不终止其他标的。
- Unix 额外 RLIMIT_CPU=1s / RLIMIT_AS=128MiB / RLIMIT_FSIZE=0 / 低 NOFILE；
  Windows 记录 RESOURCE_LIMIT_DEGRADED 且仍 kill 超时进程。
- AST 白名单由 validator 在上游（草稿保存/发布/执行装配）强制，
  本层提供进程隔离与超时；空 builtins 不能替代上游 AST 校验或强沙箱。
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile

from AI.strategy_sandbox.protocol import MAX_STDOUT_BYTES, StrategyRunResult, validate_strategy_output

DEFAULT_TIMEOUT_SECONDS = 0.3

# 子进程脚本（宿主信任代码）：读 JSON → 空 builtins 环境 exec 用户源码 → 调用 strategy → 写回 JSON
_CHILD_SCRIPT = r"""
import json
import math
import os
import sys


def _apply_posix_limits():
    # POSIX 资源限额自举（RLIMIT_CPU/AS/FSIZE/NOFILE）；Windows 跳过（宿主记 DEGRADED）
    if os.name != "posix":
        return
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (1, 1))
        resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except Exception:
        pass


def _isfinite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    return True


def _run():
    # 走 buffer + 显式 UTF-8：Windows 下 sys.stdin/stdout 默认编码可能是 GBK，
    # 与宿主的 UTF-8 载荷不兼容
    _apply_posix_limits()
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    context = payload["context"]
    source = payload["source"]
    safe_globals = {
        "__builtins__": {},
        "abs": abs,
        "min": min,
        "max": max,
        "round": round,
        "isfinite": _isfinite,
    }
    exec(compile(source, "<strategy>", "exec"), safe_globals)
    result = safe_globals["strategy"](context)
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))


try:
    _run()
except Exception:
    sys.stdout.buffer.write(json.dumps({"__sandbox_error__": True}).encode("utf-8"))
"""


def _child_env() -> dict[str, str]:
    """最小环境：仅保留解释器启动所必需的系统变量。"""
    env = {}
    for key in ("SYSTEMROOT", "TEMP", "TMP", "COMSPEC", "PATH"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    return env


def run_strategy(
    source_code: str,
    context: dict,
    *,
    has_position: bool = False,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    on_process=None,
) -> StrategyRunResult:
    """执行一次策略并校验输出；异常/超时/非法输出 fail-closed（error_code 非 None）。

    on_process(popen) 在子进程创建后回调（执行控制登记/注销用）。
    """
    degraded = sys.platform == "win32"
    # 序列化失败时尚未启动进程，避免子进程一直等待 stdin。
    payload = json.dumps({"source": source_code, "context": context}, ensure_ascii=False).encode("utf-8")
    with tempfile.TemporaryDirectory(prefix="quant-sandbox-") as empty_cwd:
        try:
            proc = subprocess.Popen(
                [sys.executable, "-I", "-c", _CHILD_SCRIPT],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=empty_cwd,
                env=_child_env(),
                close_fds=True,
                start_new_session=os.name == "posix",
                # 资源限额由子进程脚本自举（见 _CHILD_SCRIPT 开头）——
                # 不用 preexec_fn（ThreadPoolExecutor 多线程下 fork 有死锁风险）
            )
        except OSError as exc:
            return StrategyRunResult(
                ok=False, error_code="INVALID_OUTPUT",
                error_message=f"子进程启动失败: {exc}", resource_limit_degraded=degraded,
            )
        try:
            if on_process is not None:
                on_process(proc)
            stdout, _ = proc.communicate(payload, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            return StrategyRunResult(
                ok=False, error_code="EXECUTION_TIMEOUT",
                error_message=f"策略执行超过 {timeout}s", resource_limit_degraded=degraded,
            )
        except BaseException:
            # 包括登记时取消/失租；先回收，再让任务级异常正常传播。
            if proc.poll() is None:
                proc.kill()
            proc.communicate()
            raise
    if len(stdout) > MAX_STDOUT_BYTES:
        return StrategyRunResult(
            ok=False, error_code="INVALID_OUTPUT",
            error_message=f"输出超过 {MAX_STDOUT_BYTES} 字节", resource_limit_degraded=degraded,
        )
    try:
        output = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return StrategyRunResult(
            ok=False, error_code="INVALID_OUTPUT",
            error_message="输出不是合法 JSON", resource_limit_degraded=degraded,
        )
    if isinstance(output, dict) and output.get("__sandbox_error__"):
        return StrategyRunResult(
            ok=False, error_code="INVALID_OUTPUT",
            error_message="策略执行抛出异常", resource_limit_degraded=degraded,
        )
    try:
        validation = validate_strategy_output(output, has_position=has_position)
    except Exception:  # noqa: BLE001
        # 非法输出永不外溢：任何校验路径异常都 fail-closed 为该票 INVALID_OUTPUT
        return StrategyRunResult(
            ok=False, error_code="INVALID_OUTPUT",
            error_message="输出校验异常", resource_limit_degraded=degraded,
        )
    if not validation.ok:
        return StrategyRunResult(
            ok=False, error_code=validation.code or "INVALID_OUTPUT",
            error_message=validation.message, resource_limit_degraded=degraded,
        )
    return StrategyRunResult(ok=True, output=output, resource_limit_degraded=degraded)
