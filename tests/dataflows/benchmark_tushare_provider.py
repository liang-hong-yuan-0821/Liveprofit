#!/usr/bin/env python
"""
Tushare Provider 全量方法耗时基准测试

测试所有 Agent 实际调用的 TushareProvider 方法，
测量每个方法的耗时，与 LLM timeout (180s/300s) 对比，识别风险方法。
"""

import os
import sys
import time
import json
import logging
import traceback
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

# ===== 加载 .env =====
PROJECT_ROOT = Path(__file__).resolve().parent.parent
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())
    print(f"[SETUP] 已加载 .env: {env_file}")
else:
    print(f"[SETUP] 未找到 .env: {env_file}")

sys.path.insert(0, str(PROJECT_ROOT))

# ===== 日志 =====
logging.basicConfig(
    level=logging.WARNING,  # 压制 provider 内部的 INFO 日志，只看测试输出
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ===== 常量 =====
PER_METHOD_TIMEOUT = 120   # 单个方法超时上限（秒）
GLOBAL_TIMEOUT = 600        # 全局超时上限（秒）
LLM_QUICK_TIMEOUT = 180     # quick_thinking LLM 超时
LLM_DEEP_TIMEOUT = 300      # deep_thinking LLM 超时

# 测试用参数
TEST_CODE = "000001.SZ"
TEST_DATE = "2026-08-01"
TEST_START = "2026-07-01"
TEST_END = "2026-08-01"
TEST_INDEX_CODES = "000001.SH,399001.SZ,399006.SZ,000688.SH,000300.SH,000905.SH,000852.SH"

NOW = datetime.now()
NOW_STR = NOW.strftime("%Y-%m-%d")


def run_with_timeout(fn, timeout):
    """在独立线程中执行 fn，超时返回 (None, timeout=True)

    注意：不能使用 with ThreadPoolExecutor，因为 __exit__ 会调用
    shutdown(wait=True)，导致超时后主线程仍被 hang 住的工作线程阻塞。
    """
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(fn)
        return future.result(timeout=timeout), False
    except FutureTimeout:
        return None, True
    finally:
        pool.shutdown(wait=False)


def format_duration(seconds):
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    elif seconds < 60:
        return f"{seconds:.1f}s"
    else:
        return f"{int(seconds//60)}m{seconds%60:.0f}s"


def main():
    print("=" * 80)
    print("Tushare Provider 基准测试")
    print(f"启动时间: {NOW_STR} {NOW.strftime('%H:%M:%S')}")
    print(f"单方法超时: {PER_METHOD_TIMEOUT}s | 全局超时: {GLOBAL_TIMEOUT}s")
    print(f"LLM quick: {LLM_QUICK_TIMEOUT}s | LLM deep: {LLM_DEEP_TIMEOUT}s")
    print("=" * 80)

    # ===== 创建 Provider =====
    print("\n>>> 初始化 TushareProvider...")
    t0 = time.perf_counter()
    from AI.dataflows.providers.tushare_provider import TushareProvider
    provider = TushareProvider()
    elapsed = time.perf_counter() - t0
    print(f"    连接状态: {'已连接' if provider.connected else '未连接'} | 耗时: {format_duration(elapsed)}")

    if not provider.connected:
        print("\n[ERROR] Tushare 未连接，请检查 TUSHARE_TOKEN 环境变量。")
        sys.exit(1)

    # ===== 定义测试用例 =====
    test_cases = [
        {
            "name": "get_stock_data",
            "fn": lambda: provider.get_stock_data(TEST_CODE, TEST_START, TEST_END),
            "api_calls": 1,
            "risk": "低",
        },
        {
            "name": "get_stock_info",
            "fn": lambda: json.dumps(provider.get_stock_info(TEST_CODE), ensure_ascii=False),
            "api_calls": 1,
            "risk": "低",
        },
        {
            "name": "get_fundamentals",
            "fn": lambda: provider.get_fundamentals(TEST_CODE, TEST_DATE),
            "api_calls": 4,
            "risk": "低",
        },
        {
            "name": "get_news",
            "fn": lambda: provider.get_news(TEST_CODE, TEST_START, TEST_END),
            "api_calls": 2,
            "risk": "低",
        },
        {
            "name": "get_index_data",
            "fn": lambda: provider.get_index_data(TEST_INDEX_CODES, TEST_START, TEST_END),
            "api_calls": 7,
            "risk": "低-中",
        },
        {
            "name": "get_daily_basic",
            "fn": lambda: provider.get_daily_basic(TEST_CODE, "20260731"),
            "api_calls": 1,
            "risk": "低",
        },
        {
            "name": "get_ipo_calendar",
            "fn": lambda: provider.get_ipo_calendar(NOW_STR),
            "api_calls": 1,
            "risk": "低",
        },
        {
            "name": "get_market_breadth",
            "fn": lambda: provider.get_market_breadth("20260731"),
            "api_calls": 1,
            "risk": "中",
        },
        {
            "name": "get_industry_sector_performance",
            "fn": lambda: provider.get_industry_sector_performance(days=10),
            "api_calls": "1+31",
            "risk": "高",
        },
        {
            "name": "get_concept_board_heat_rank",
            "fn": lambda: provider.get_concept_board_heat_rank(days=10),
            "api_calls": "1+777",
            "risk": "极高",
        },
        {
            "name": "get_sector_technical_screening",
            "fn": lambda: provider.get_sector_technical_screening(days=60),
            "api_calls": "1+31",
            "risk": "高",
        },
    ]

    results = []
    global_start = time.perf_counter()
    aborted = False

    for i, tc in enumerate(test_cases, 1):
        name = tc["name"]
        risk = tc["risk"]
        api_calls = tc["api_calls"]

        # 全局超时检查
        global_elapsed = time.perf_counter() - global_start
        if global_elapsed > GLOBAL_TIMEOUT:
            print(f"\n[ABORT] 全局超时 ({format_duration(global_elapsed)} > {GLOBAL_TIMEOUT}s)，跳过剩余 {len(test_cases) - i + 1} 个方法")
            aborted = True
            break

        print(f"\n{'─' * 60}")
        print(f"[{i}/{len(test_cases)}] {name} (预估API调用: {api_calls}, 风险: {risk})")

        t_start = time.perf_counter()
        result, timed_out = run_with_timeout(tc["fn"], PER_METHOD_TIMEOUT)
        t_elapsed = time.perf_counter() - t_start

        if timed_out:
            print(f"    >>> TIMEOUT ({format_duration(PER_METHOD_TIMEOUT)})")
            results.append({
                "name": name,
                "duration_s": round(t_elapsed, 2),
                "status": "TIMEOUT",
                "content_len": 0,
                "preview": f"超过 {PER_METHOD_TIMEOUT}s 单方法超时上限",
                "risk": risk,
                "verdict": "TIMEOUT (>120s)",
            })
        elif isinstance(result, Exception) or (isinstance(result, str) and result.startswith("Traceback")):
            print(f"    >>> ERROR")
            results.append({
                "name": name,
                "duration_s": round(t_elapsed, 2),
                "status": "ERROR",
                "content_len": 0,
                "preview": str(result)[:200],
                "risk": risk,
                "verdict": "ERROR",
            })
        else:
            content = result if isinstance(result, str) else str(result)
            content_len = len(content)
            preview = content[:120].replace("\n", "\\n")

            # 风险评估
            if t_elapsed > LLM_DEEP_TIMEOUT:
                verdict = f"CRITICAL ({format_duration(t_elapsed)} > deep LLM {LLM_DEEP_TIMEOUT}s)"
            elif t_elapsed > LLM_QUICK_TIMEOUT:
                verdict = f"HIGH ({format_duration(t_elapsed)} > quick LLM {LLM_QUICK_TIMEOUT}s)"
            elif t_elapsed > 60:
                verdict = f"WARN ({format_duration(t_elapsed)} > 60s)"
            else:
                verdict = "OK"

            print(f"    耗时: {format_duration(t_elapsed)} | 返回: {content_len} 字符 | {verdict}")
            if t_elapsed > 5:
                print(f"    预览: {preview}")

            results.append({
                "name": name,
                "duration_s": round(t_elapsed, 2),
                "status": "OK",
                "content_len": content_len,
                "preview": preview,
                "risk": risk,
                "verdict": verdict,
            })

    # ===== 汇总 =====
    global_elapsed = time.perf_counter() - global_start
    print(f"\n{'=' * 80}")
    print(f"测试完成 {'(提前终止)' if aborted else ''}")
    print(f"总耗时: {format_duration(global_elapsed)}")
    print(f"{'=' * 80}")

    print(f"\n{'方法':<40} {'耗时':>10} {'状态':>8} {'风险':>6} {'评估'}")
    print("-" * 90)
    for r in results:
        print(f"{r['name']:<40} {format_duration(r['duration_s']):>10} {r['status']:>8} {r['risk']:>6} {r['verdict']}")

    # 风险方法汇总
    risky = [r for r in results if "CRITICAL" in r["verdict"] or "HIGH" in r["verdict"] or "TIMEOUT" in r["verdict"]]
    if risky:
        print(f"\n[WARN] 需要优化的方法 ({len(risky)} 个):")
        for r in risky:
            print(f"    - {r['name']}: {r['verdict']}")

    # 写入 JSON
    output_path = PROJECT_ROOT / "tests" / f"benchmark_result_{NOW.strftime('%Y%m%d_%H%M%S')}.json"
    output_data = {
        "run_time": NOW_STR,
        "global_duration_s": round(global_elapsed, 2),
        "aborted": aborted,
        "llm_quick_timeout_s": LLM_QUICK_TIMEOUT,
        "llm_deep_timeout_s": LLM_DEEP_TIMEOUT,
        "per_method_timeout_s": PER_METHOD_TIMEOUT,
        "results": results,
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    print(f"\n结果已保存: {output_path}")


if __name__ == "__main__":
    main()
