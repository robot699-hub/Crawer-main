"""逐条写入采集日志，保留每次运行的结果。"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from ..models import Record, utc_now


class CrawlLogger:
    def __init__(self, output_dir: Path):
        log_dir = output_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        self.path = log_dir / "crawl.jsonl"

    def write(self, record: Record) -> None:
        event = {"observed_at": utc_now(), **asdict(record)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
