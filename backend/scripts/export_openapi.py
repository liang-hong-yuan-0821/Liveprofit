"""OpenAPI v1 导出（§2.6.4：只装配 create_app()，不连接数据库/不调用 LLM）。

本地与 CI 固定执行：
    python -m backend.scripts.export_openapi --output backend/openapi/openapi.v1.json

所有 router/schema 改动必须在同一变更中更新该文件（git diff 门禁）。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = PROJECT_ROOT / "backend" / "openapi" / "openapi.v1.json"


def export_schema() -> dict:
    from backend.main import create_app

    app = create_app()  # Settings 校验（api）：缺 DATABASE_URL/REDIS_URL 时 fail-fast
    return app.openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 Liveprofit OpenAPI v1 schema")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    schema = export_schema()
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OpenAPI v1 已导出：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
