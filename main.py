#!/usr/bin/env python3
"""一键启动入口；无参数运行时读取 config/site_config.json。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
try:
    from source_crawler.cli import main
except ModuleNotFoundError as exc:
    if exc.name not in {"requests", "bs4", "openpyxl"}:
        raise
    raise SystemExit("缺少运行依赖。请先执行：python -m pip install -r requirements.txt") from exc


if __name__ == "__main__":
    raise SystemExit(main())
