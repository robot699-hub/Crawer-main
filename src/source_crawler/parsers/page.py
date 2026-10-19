from __future__ import annotations

import re
from typing import Sequence
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from ..models import Record
from ..validators.scope import is_pagination_link
from ..validators.urls import detect_extension, normalize_url

RELEVANT_TERMS = (
    "download",
    "dataset",
    "database",
    "data",
    "statistics",
    "indicator",
    "publication",
    "report",
    "evaluation",
    "project",
    "completion",
    "impact",
    "policy",
    "regulation",
    "legislation",
    "law",
    "official journal",
    "open data",
    "eco-regeling",
    "eco-activiteiten",
    "conditionaliteiten",
    "landbouwer",
    "landbouw",
    "social accounting matrix",
    "nexus sam",
    "computable general equilibrium",
    "cge model",
    "下载",
    "数据",
    "统计",
    "指标",
    "数据库",
    "报告",
    "政策",
    "法规",
    "条例",
    "公报",
    "文献",
)
LOW_VALUE_TERMS = (
    "login",
    "sign in",
    "register",
    "newsletter",
    "contact",
    "about us",
    "privacy",
    "cookie",
    "登录",
    "注册",
    "联系我们",
    "隐私",
)
POLICY_TERMS = (
    "regulation",
    "directive",
    "decision",
    "legislation",
    "law",
    " act ",
    "decree",
    "policy",
    "regeling",
    "subsidie",
    "voorwaarde",
    "voorwaarden",
    "besluit",
    "法规",
    "法律",
    "条例",
    "法令",
    "政策",
    "决定",
)
BLOG_TERMS = ("blog", "opinion", "commentary", "观点", "博客", "评论")
ACCESS_TERMS = {
    "需要登录": ("sign in", "log in", "login required", "登录后", "需要登录"),
    "可能需要订阅或付费": ("subscribe", "subscription", "paywall", "purchase access", "订阅", "付费"),
    "可能存在验证码": ("captcha", "recaptcha", "验证码"),
    "页面依赖 JavaScript": ("enable javascript", "javascript is required", "请启用javascript", "请启用 javascript"),
}

def classify_source(title: str, url: str, text: str = "") -> str:
    label = f" {title} {url} ".lower()
    haystack = f"{label} {text[:3000]}".lower()
    if any(term in label for term in BLOG_TERMS):
        return "研究博客/观点文章"
    if any(term in haystack for term in POLICY_TERMS):
        return "政策相关页面（待核验）"
    if any(term in haystack for term in ("dataset", "database", "statistics", "indicator", "open data", "数据集", "数据库", "统计", "指标")):
        return "数据库/专题数据页面"
    if any(term in haystack for term in ("journal", "doi.org", "working paper", "research paper", "论文", "文献")):
        return "相关文献"
    if any(term in haystack for term in ("report", "publication", "报告", "出版物")):
        return "报告/出版物"
    return "其他网页"


def extract_years(text: str) -> str:
    years: set[str] = set()
    for start, end in re.findall(r"(?<!\d)((?:19|20)\d{2})\s*[-–—/]\s*((?:19|20)\d{2})(?!\d)", text):
        years.add(f"{start}-{end}")
    if years:
        return "; ".join(sorted(years))
    singles = sorted(set(re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", text)))
    return "; ".join(singles[:8])


def first_meta(soup: BeautifulSoup, keys: Sequence[str]) -> str:
    for key in keys:
        tag = soup.find("meta", attrs={"name": re.compile(f"^{re.escape(key)}$", re.I)})
        if not tag:
            tag = soup.find("meta", attrs={"property": re.compile(f"^{re.escape(key)}$", re.I)})
        if tag and tag.get("content"):
            return str(tag["content"]).strip()
    return ""


def extract_metadata(html: str, url: str, fallback_name: str = "") -> tuple[Record, BeautifulSoup]:
    soup = BeautifulSoup(html, "html.parser")
    title = first_meta(soup, ("og:title", "twitter:title", "citation_title", "dc.title"))
    if not title and soup.title:
        title = soup.title.get_text(" ", strip=True)
    title = title or fallback_name or urlsplit(url).path.rsplit("/", 1)[-1] or url
    publisher = first_meta(soup, ("citation_publisher", "dc.publisher", "publisher", "og:site_name", "author"))
    publication_date = first_meta(
        soup,
        ("citation_publication_date", "article:published_time", "date", "dc.date", "dcterms.date", "datepublished"),
    )
    if not publication_date:
        time_tag = soup.find("time", datetime=True)
        publication_date = str(time_tag.get("datetime", "")).strip() if time_tag else ""

    description = first_meta(soup, ("description", "og:description", "dc.description"))
    visible_text = soup.get_text(" ", strip=True)
    evidence_text = f"{title} {description} {visible_text[:12000]}"

    license_value = ""
    license_link = soup.find("a", rel=lambda value: value and "license" in value)
    if license_link:
        license_value = " ".join(license_link.get_text(" ", strip=True).split()) or str(license_link.get("href", ""))
    if not license_value:
        license_value = first_meta(soup, ("license", "dc.rights", "dcterms.license", "copyright"))
    if not license_value:
        license_match = re.search(
            r"(Creative Commons[^.<]{0,100}|CC BY(?:-NC|-SA|-ND)*[^.<]{0,60}|Open Government Licence[^.<]{0,80}|开放许可[^。]{0,80})",
            evidence_text,
            re.I,
        )
        license_value = license_match.group(1).strip() if license_match else "未识别，需人工核验"

    access = "公开访问"
    lower_text = evidence_text.lower()
    access_hits = [label for label, terms in ACCESS_TERMS.items() if any(term in lower_text for term in terms)]
    if access_hits:
        access = "; ".join(access_hits)

    status_evidence = ""
    for pattern, label in (
        (r"\b(in force|currently effective|effective from)\b|现行有效|生效日期", "页面含有效性信息，需核对原文"),
        (r"\b(repealed|revoked|expired|no longer in force)\b|已废止|已失效|有效期届满", "页面提示可能已废止或失效"),
        (r"\b(amended|consolidated|latest version|revision)\b|修订|修改|最新版本", "页面含修订或版本信息，需核对版本"),
    ):
        if re.search(pattern, evidence_text, re.I):
            status_evidence = label
            break

    version_match = re.search(
        r"(?:version|revision|updated|last updated|版本|修订于|更新于)\s*[:：]?\s*([^|,，;；。]{1,60})",
        evidence_text,
        re.I,
    )
    implementation_match = re.search(
        r"(?:effective|implementation period|applies from|实施时期|实施期|施行日期)\s*[:：]?\s*([^|,，;；。]{1,80})",
        evidence_text,
        re.I,
    )
    record = Record(
        record_name=title[:300],
        publisher=publisher[:200],
        publication_date=publication_date[:80],
        coverage_years=extract_years(f"{title} {description}"),
        original_url=url,
        usage_license=license_value[:300],
        source_category=classify_source(title, url, evidence_text),
        policy_status=status_evidence,
        policy_version=version_match.group(1).strip()[:100] if version_match else "",
        implementation_period=implementation_match.group(1).strip()[:120] if implementation_match else "",
        access_conditions=access,
    )
    return record, soup


def score_link(url: str, text: str) -> int:
    haystack = f"{text} {url}".lower()
    score = sum(3 for term in RELEVANT_TERMS if term in haystack)
    score -= sum(4 for term in LOW_VALUE_TERMS if term in haystack)
    if detect_extension(url):
        score += 20
    if is_pagination_link(url, text):
        score += 6
    if urlsplit(url).query:
        score -= 1
    return score


def discover_links(soup: BeautifulSoup, base_url: str) -> list[tuple[str, str, int]]:
    found: dict[str, tuple[str, int]] = {}
    link_nodes = [(anchor, "href") for anchor in soup.find_all("a", href=True)]
    link_nodes.extend((node, "src") for node in soup.find_all(["iframe", "embed"], src=True))
    link_nodes.extend((node, "data") for node in soup.find_all("object", data=True))
    for node, attribute in link_nodes:
        href = str(node.get(attribute, "")).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#", "data:")):
            continue
        url = normalize_url(urljoin(base_url, href))
        if not url:
            continue
        text = " ".join(node.get_text(" ", strip=True).split())
        if not text:
            text = str(node.get("title") or node.get("aria-label") or "embedded document").strip()
        score = score_link(url, text)
        previous = found.get(url)
        if previous is None or score > previous[1]:
            found[url] = (text, score)
    return sorted(((url, text, score) for url, (text, score) in found.items()), key=lambda item: (-item[2], item[0]))
