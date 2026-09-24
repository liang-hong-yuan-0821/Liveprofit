"""运行：python -m backend.scripts.quant_data_quality_poc --date YYYY-MM-DD"""

from __future__ import annotations

import argparse
import json
from datetime import date

from backend.modules.quant_strategy.application.data_quality_poc import QuantDataQualityPoc
from db.instrument.db import get_connection


def main() -> None:
    parser = argparse.ArgumentParser(description="量化全市场数据质量 POC")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--runner-samples", type=int, default=3)
    args = parser.parse_args()
    with get_connection() as conn:
        report = QuantDataQualityPoc(
            batch_size=args.batch_size, runner_sample_size=args.runner_samples,
        ).run(conn, date.fromisoformat(args.date))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
