from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from pathlib import Path
from typing import Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DOWNLOAD_EXTENSIONS = {
    ".pdf",
    ".doc",
    ".docx",
    ".txt",
    ".md",
    ".xls",
    ".xlsx",
    ".csv",
    ".tsv",
    ".ods",
    ".json",
    ".xml",
    ".zip",
    ".gz",
    ".7z",
    ".sav",
    ".dta",
    ".rds",
    ".rdata",
}
CONTENT_TYPE_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
    "application/markdown": ".md",
    "application/x-markdown": ".md",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "application/json": ".json",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/zip": ".zip",
    "application/gzip": ".gz",
    "application/x-stata": ".dta",
    "application/x-r-data": ".rdata",
}
TRACKING_QUERY_PREFIXES = ("utm_", "pk_")
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}

def normalize_url(url: str) -> str:
    url = url.strip().strip("<>\"'")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        key_lower = key.lower()
        if key_lower in TRACKING_QUERY_KEYS or key_lower.startswith(TRACKING_QUERY_PREFIXES):
            continue
        query.append((key, value))
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def extract_urls(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    found = re.findall(r"https?://[^\s<>\"]+", value)
    result: list[str] = []
    for raw in found:
        normalized = normalize_url(raw.rstrip(".,;，；。)）]】"))
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def host_matches(host: str, domain: str) -> bool:
    host = host.lower().rstrip(".")
    domain = domain.lower().lstrip(".").rstrip(".")
    return host == domain or host.endswith("." + domain)


def is_excluded(url: str, excluded_domains: Sequence[str]) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return any(host_matches(host, domain) for domain in excluded_domains)


def same_site(url_a: str, url_b: str) -> bool:
    a = (urlsplit(url_a).hostname or "").lower().removeprefix("www.")
    b = (urlsplit(url_b).hostname or "").lower().removeprefix("www.")
    return bool(a and b and (a == b or b.endswith("." + a)))


def within_site_scope(url: str, seed_url: str, allowed_domains: Sequence[str]) -> bool:
    if same_site(seed_url, url):
        return True
    host = (urlsplit(url).hostname or "").lower()
    return any(host_matches(host, domain) for domain in allowed_domains)


def ensure_public_url(url: str, resolve_dns: bool = True) -> None:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("只允许 http/https 公网地址")
    host = parts.hostname.lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("拒绝本机或局域网地址")
    try:
        ip = ipaddress.ip_address(host.strip("[]"))
        addresses = [ip]
    except ValueError:
        if not resolve_dns:
            return
        try:
            default_port = 443 if parts.scheme == "https" else 80
            addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, parts.port or default_port)]
        except socket.gaierror as exc:
            raise ValueError(f"域名解析失败: {exc}") from exc
    if any(not ip.is_global for ip in addresses):
        raise ValueError("拒绝非公网地址")


def detect_extension(url: str, content_type: str = "", disposition: str = "") -> str:
    filename_match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
    candidates = [filename_match.group(1) if filename_match else "", urlsplit(url).path]
    for candidate in candidates:
        suffix = Path(candidate).suffix.lower()
        if suffix in DOWNLOAD_EXTENSIONS:
            return suffix
    mime = content_type.split(";", 1)[0].strip().lower()
    return CONTENT_TYPE_EXTENSIONS.get(mime, "")


def safe_filename(name: str, url: str, extension: str) -> str:
    stem = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", name).strip(" ._")
    stem = re.sub(r"\s+", " ", stem)[:100] or "download"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
    return f"{stem}_{digest}{extension}"
