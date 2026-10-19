"""逐候选入口匹配人工填写的范围条件。"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from ..models import CandidateRules


YEAR_PATTERN = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
PAGINATION_TEXT = {"next", "next page", "more", "load more", "下一页", "下页", "更多", "加载更多"}


def scope_mismatch(rules: CandidateRules, text: str) -> str:
    """返回不匹配原因；空字符串表示符合已填写的规则。"""
    haystack = text.casefold()
    if rules.include_terms and not any(term.casefold() in haystack for term in rules.include_terms):
        return "未匹配范围关键词"
    if any(term.casefold() in haystack for term in rules.exclude_terms):
        return "匹配排除关键词"
    if rules.region_terms and not any(term.casefold() in haystack for term in rules.region_terms):
        return "未匹配地区关键词"
    if rules.year_start is not None or rules.year_end is not None:
        years = [int(year) for year in YEAR_PATTERN.findall(text)]
        if not any((rules.year_start is None or year >= rules.year_start) and
                   (rules.year_end is None or year <= rules.year_end) for year in years):
            return "未找到限定年份内的年份"
    return ""


def is_dataset_detail(url: str, rules: CandidateRules) -> bool:
    return any(pattern.casefold() in url.casefold() for pattern in rules.dataset_detail_url_patterns)


def is_pagination_link(url: str, link_text: str) -> bool:
    text = " ".join(link_text.casefold().split())
    if text in PAGINATION_TEXT:
        return True
    parts = urlsplit(url)
    query_keys = {key.casefold() for key in parse_qs(parts.query)}
    return bool(query_keys & {"page", "pagenumber", "page_no", "pageindex"}) or bool(
        re.search(r"/(?:page|pagina)/\d+(?:/|$)", parts.path, re.I)
    )
