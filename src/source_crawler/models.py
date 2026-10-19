from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

OUTPUT_COLUMNS = [
    "candidate_id",
    "source_id",
    "priority",
    "record_name",
    "first_batch_scope",
    "license_and_boundaries",
    "publisher",
    "country_or_region",
    "publication_date",
    "coverage_years",
    "original_url",
    "download_date",
    "file_type",
    "usage_license",
    "source_category",
    "policy_status",
    "policy_version",
    "implementation_period",
    "access_conditions",
    "failure_reason",
    "download_status",
    "item_type",
    "scope_status",
    "local_path",
    "sha256",
    "http_status",
    "seed_url",
    "discovered_from",
]


@dataclass(frozen=True)
class CandidateRules:
    discovery_mode: str = "web"
    max_depth: int | None = None
    max_pages: int | None = None
    max_items: int | None = None
    min_link_score: int | None = None
    include_terms: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    region_terms: tuple[str, ...] = ()
    year_start: int | None = None
    year_end: int | None = None
    dataset_detail_url_patterns: tuple[str, ...] = ()
    scope_ready: bool = False
    allowed_domains: tuple[str, ...] = ()
    file_types: tuple[str, ...] = ()
    download: bool = True
    save_html: bool = False
    eurlex_topics: tuple[str, ...] = ()
    eurlex_language: str = "ENG"
    eurlex_page_size: int = 250
    eurlex_result_limit: int | None = None


@dataclass(frozen=True)
class Seed:
    url: str
    candidate_id: str = ""
    source_id: str = ""
    priority: str = ""
    name: str = ""
    country_or_region: str = ""
    publisher: str = ""
    publication_date: str = ""
    usage_license: str = ""
    first_batch_scope: str = ""
    license_and_boundaries: str = ""
    rules: CandidateRules = CandidateRules()


@dataclass(frozen=True)
class Task:
    url: str
    seed: Seed
    depth: int = 0
    discovered_from: str = ""
    link_text: str = ""


@dataclass
class Record:
    candidate_id: str = ""
    source_id: str = ""
    priority: str = ""
    record_name: str = ""
    first_batch_scope: str = ""
    license_and_boundaries: str = ""
    publisher: str = ""
    country_or_region: str = ""
    publication_date: str = ""
    coverage_years: str = ""
    original_url: str = ""
    download_date: str = ""
    file_type: str = ""
    usage_license: str = "未识别，需人工核验"
    source_category: str = "其他网页"
    policy_status: str = ""
    policy_version: str = ""
    implementation_period: str = ""
    access_conditions: str = "公开访问"
    failure_reason: str = ""
    download_status: str = "仅记录网页"
    item_type: str = ""
    scope_status: str = ""
    local_path: str = ""
    sha256: str = ""
    http_status: str = ""
    seed_url: str = ""
    discovered_from: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
