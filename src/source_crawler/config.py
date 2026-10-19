"""爬取参数的统一配置。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


USER_AGENT = "OfficialSourceCrawler/1.0 (+contact: replace-with-your-email@example.com)"
DEFAULT_EXCLUDED_DOMAINS = ("fao.org",)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = PROJECT_ROOT / "config" / "site_config.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data"


@dataclass(frozen=True)
class CrawlConfig:
    output_dir: Path
    excluded_domains: tuple[str, ...] = DEFAULT_EXCLUDED_DOMAINS
    max_depth: int = 1
    max_pages: int = 500
    max_pages_per_site: int = 80
    delay: float = 1.0
    timeout: float = 30.0
    max_file_mb: int = 200
    min_link_score: int = 2
    respect_robots: bool = True
    verify_dns: bool = True
    user_agent: str = USER_AGENT
    resume_download_dir: Path | None = None
