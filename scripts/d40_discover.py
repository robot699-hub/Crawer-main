"""Discover policy-relevant empirical datasets from the public CIMMYT Dataverse UI.

The Dataverse Search API requires authentication on this installation, so this
uses only public search-result HTML and emits a static crawler configuration.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit, parse_qs

import requests
from bs4 import BeautifulSoup


BASE = "https://data.cimmyt.org/dataverse/cimmytdatadvn"
OUT = Path(__file__).resolve().parents[1] / "config" / "d40_cimmyt_dataverse.json"
QUERIES = (
    "policy",
    "adoption",
    "technology adoption",
    "socioeconomic",
    "welfare",
    "impact",
    "household",
    "farmer survey",
    "market",
    "gender",
    "poverty",
    "Pakistan",
)
TERMS = (
    "policy", "policies", "impact", "welfare", "economic", "economics",
    "socioeconomic", "socio-economic", "household", "adoption", "farmer",
    "technology", "scaling", "profitability", "market", "seed system",
    "climate adaptation", "resilience", "nutrition", "gender", "survey",
    "poverty", "evaluation", "extension", "incentive", "decision-making",
)
COUNTRIES = (
    "Pakistan", "Bangladesh", "India", "Nepal", "Afghanistan", "Myanmar",
    "Ethiopia", "Kenya", "Tanzania", "Uganda", "Zambia", "Zimbabwe",
    "Malawi", "Mozambique", "Nigeria", "Ghana", "Mexico", "China",
    "Kyrgyzstan", "Uzbekistan", "Tajikistan", "Turkey", "Ecuador",
)


def tidy(value: str) -> str:
    return " ".join(value.split())


def dataset_id(url: str) -> str:
    query = parse_qs(urlsplit(url).query)
    persistent = query.get("persistentId", [url])[-1]
    match = re.search(r"(?:hdl:|/)(\d{4,}/\d+)$", persistent)
    if match:
        return match.group(1).replace("/", "-")
    return re.sub(r"[^A-Za-z0-9]+", "-", persistent).strip("-")[-40:]


def country_hint(text: str) -> str:
    hits = [country for country in COUNTRIES if re.search(rf"\b{re.escape(country)}\b", text, re.I)]
    return "; ".join(hits) if hits else "未明确标注（跨国/需核验）"


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "OfficialSourceCrawler/1.0 (CIMMYT public Dataverse research archive)"})
    found: dict[str, dict[str, str]] = {}
    for query in QUERIES:
        seen_pages: set[str] = set()
        for page in range(1, 80):
            params = {"q": query, "types": "datasets", "sort": "score", "order": "desc"}
            if page > 1:
                params["page"] = str(page)
            response = session.get(BASE, params=params, timeout=40)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            cards = soup.select(".datasetResult")
            if not cards:
                break
            card_urls: list[str] = []
            for card in cards:
                anchor = card.select_one('a[href*="dataset.xhtml"][href*="persistentId="]')
                if not anchor:
                    continue
                detail_url = urljoin(BASE, anchor.get("href", ""))
                persistent = parse_qs(urlsplit(detail_url).query).get("persistentId", [""])[0]
                if not persistent or persistent in found:
                    card_urls.append(persistent)
                    continue
                title = tidy(anchor.get_text(" ", strip=True))
                body = tidy(card.get_text(" ", strip=True))
                matched = [term for term in TERMS if term.casefold() in body.casefold()]
                if not matched:
                    continue
                found[persistent] = {
                    "url": detail_url,
                    "title": title,
                    "summary": body[:2200],
                    "query": query,
                    "country": country_hint(body),
                    "signals": ", ".join(matched),
                }
                card_urls.append(persistent)
            page_key = "|".join(card_urls)
            if not card_urls or page_key in seen_pages:
                break
            seen_pages.add(page_key)
            next_page = soup.select_one('a[href*="page=' + str(page + 1) + '"]')
            if not next_page:
                break
            time.sleep(1.0)
        print(f"query={query}: unique records so far={len(found)}")
        time.sleep(1.0)

    candidates = []
    for index, (persistent, item) in enumerate(sorted(found.items(), key=lambda pair: pair[1]["title"].casefold()), 1):
        candidates.append({
            "id": f"D40-{dataset_id(persistent)}-{index:03d}",
            "source_id": "D40-CIMMYT",
            "publisher": "CIMMYT Research Data Dataverse",
            "name": item["title"],
            "country_or_region": item["country"],
            "url": item["url"],
            "publication_date": "",
            "usage_license": "Dataverse 记录级许可；以该数据集页面及文件元数据为准，未声明时需人工核验",
            "discovery_query": item["query"],
            "relevance_signals": item["signals"],
            "search_result_summary": item["summary"],
        })

    config = {
        "schema_version": 4,
        "job": {
            "source_url": BASE,
            "target_region": "全球；按数据集元数据记录国家/地区，重点可筛 Pakistan",
            "material_or_data": "CIMMYT Research Data Dataverse 中与农业政策分析有关的公开经验研究数据、数据论文与附件",
            "first_batch_scope": "收录公开检索结果中明确涉及农业政策分析证据的记录：农户/家庭调查、农业技术与品种采用及影响、福利/收入/市场、性别、贫困、气候韧性、农业扩展与评估等。对每个记录保存数据集详情页 HTML，下载详情页直接链接的公开数据、数据论文、报告和附件；保留 DOI、版本、日期、许可、国家/地区与文件路径。仅当数据集标题或摘要出现明确农业政策/采用/影响/社会经济信号时纳入。该 Dataverse 是科研数据仓，不是法规库；不把数据或论文冒称为条例正文。",
            "license_and_boundaries": "只使用 CIMMYT Dataverse 公开网页与其明确链接的公开文件，遵守 robots.txt、记录级许可和署名/再利用要求。API 需要认证，因此仅通过公开检索页发现记录；任何未声明许可的页面或文件标记需人工核验。",
            "latest_items": len(candidates),
            "content_fields": ["数据集名称", "DOI/永久标识符", "国家/地区", "版本与发布日期", "农业政策相关性信号", "记录级许可", "附件类型与本地路径", "原始链接"],
        },
        "run": {
            "output_dir": "D:\\爬取数据\\D40\\完整归档",
            "output_format": "xlsx",
            "include_fao": False,
            "exclude_domains": [],
            "max_candidates": max(len(candidates), 1),
            "max_pages": max(1000, len(candidates) * 60),
            "max_pages_per_site": max(1000, len(candidates) * 60),
            "max_depth": 1,
            "min_link_score": 1,
            "delay": 1.0,
            "timeout": 60,
            "max_file_mb": 500,
            "respect_robots": True,
            "verify_dns": True,
            "user_agent": "OfficialSourceCrawler/1.0 (D40 CIMMYT Dataverse public research archive)",
        },
        "candidate_defaults": {
            "source_id": "D40-CIMMYT",
            "enabled": True,
            "priority": "P0",
            "usage_license": "Dataverse 记录级许可；以该数据集页面及文件元数据为准，未声明时需人工核验",
            "crawl": {
                "scope_ready": True,
                "max_items": 500,
                "max_pages": 60,
                "max_depth": 1,
                "min_link_score": 1,
                "include_terms": [],
                "exclude_terms": [],
                "region_terms": [],
                "dataset_detail_url_patterns": ["/dataset.xhtml?persistentId="],
                "allowed_domains": ["data.cimmyt.org"],
                "file_types": ["pdf", "doc", "docx", "xls", "xlsx", "csv", "tsv", "ods", "json", "xml", "zip", "txt", "sav", "dta", "rds", "rdata"],
                "download": True,
                "save_html": True,
            },
        },
        "candidates": candidates,
    }
    OUT.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}; selected {len(candidates)} unique dataset records")


if __name__ == "__main__":
    main()
