"""限速、重定向和 robots.txt 检查集中在此。"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests

from ..config import CrawlConfig
from ..validators.urls import ensure_public_url, is_excluded, normalize_url, within_site_scope


LOGGER = logging.getLogger(__name__)


class HttpClient:
    def __init__(self, config: CrawlConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": config.user_agent, "Accept-Language": "en,zh-CN;q=0.8,zh;q=0.7"}
        )
        self.last_request_at: dict[str, float] = defaultdict(float)
        self.robot_cache: dict[str, RobotFileParser] = {}

    def close(self) -> None:
        self.session.close()

    def _wait(self, url: str) -> None:
        host = urlsplit(url).netloc.lower()
        remaining = self.config.delay - (time.monotonic() - self.last_request_at[host])
        if remaining > 0:
            time.sleep(remaining)
        self.last_request_at[host] = time.monotonic()

    def get(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        stream: bool = True,
        site_seed_url: str = "",
        allowed_domains: tuple[str, ...] = (),
    ) -> requests.Response:
        current = url
        for _ in range(6):
            if is_excluded(current, self.config.excluded_domains):
                raise ValueError("目标地址属于排除域名")
            if site_seed_url and not within_site_scope(current, site_seed_url, allowed_domains):
                raise ValueError("目标地址超出该入口的允许域名")
            ensure_public_url(current, resolve_dns=self.config.verify_dns)
            response = None
            last_error: requests.RequestException | None = None
            for attempt in range(3):
                self._wait(current)
                try:
                    response = self.session.get(
                        current,
                        headers=headers,
                        timeout=self.config.timeout,
                        stream=stream,
                        allow_redirects=False,
                    )
                    break
                except (requests.ConnectionError, requests.Timeout, requests.exceptions.SSLError) as exc:
                    last_error = exc
                    if attempt == 2:
                        raise
                    time.sleep(min(max(self.config.delay, 0.5), 2.0))
            if response is None:
                raise last_error or requests.RequestException("请求未返回响应")
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise requests.RequestException("重定向缺少 Location")
            current = normalize_url(urljoin(current, location))
            if not current:
                raise requests.RequestException("重定向地址无效")
        raise requests.TooManyRedirects("重定向超过 5 次")

    def _robots(self, url: str, site_seed_url: str = "", allowed_domains: tuple[str, ...] = ()) -> RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self.robot_cache:
            return self.robot_cache[origin]
        robots_url = origin + "/robots.txt"
        # HTTP 429 表示临时限流，而不是 robots 规则允许抓取。遵循服务器给出的
        # Retry-After（若有）并退避重试；持续限流则拒绝继续，避免把不可读的规则
        # 误当成空规则。
        for attempt in range(3):
            parser = RobotFileParser()
            parser.set_url(robots_url)
            response = None
            retry_after = 0.0
            try:
                # 官方站点可能把 robots.txt 重定向到统一的官方域名。此请求仅用于
                # 读取爬取规则，实际资源请求仍在 get() 中接受入口范围校验。
                response = self.get(robots_url, stream=False)
                if response.status_code == 429:
                    retry_after = self._retry_after_seconds(response, attempt)
                elif response.status_code in {401, 403}:
                    parser.parse(["User-agent: *", "Disallow: /"])
                elif response.status_code < 400:
                    parser.parse(response.text.splitlines())
                elif response.status_code >= 500:
                    raise requests.RequestException(f"robots.txt 返回 HTTP {response.status_code}")
                else:
                    parser.parse([])
            finally:
                if response is not None:
                    response.close()

            if response is not None and response.status_code == 429:
                if attempt == 2:
                    break
                LOGGER.warning(
                    "robots.txt 返回 HTTP 429；等待 %.0f 秒后重试（%d/2）",
                    retry_after,
                    attempt + 1,
                )
                time.sleep(retry_after)
                continue

            self.robot_cache[origin] = parser
            return parser

        raise requests.RequestException("无法核对 robots.txt：连续 3 次收到 HTTP 429 限流")

    @staticmethod
    def _retry_after_seconds(response: requests.Response, attempt: int) -> float:
        """Return a bounded, polite delay for a throttled response."""
        raw_value = response.headers.get("Retry-After", "").strip()
        try:
            seconds = float(raw_value)
        except ValueError:
            seconds = 5.0 * (2**attempt)
        return min(max(seconds, 5.0), 60.0)

    def allowed(self, url: str, site_seed_url: str = "", allowed_domains: tuple[str, ...] = ()) -> bool:
        if not self.config.respect_robots:
            return True
        # robots.txt 必须从待访问地址自身的站点读取。入口可位于另一个已允许的
        # 官方域名（例如 EUR-Lex 入口与 Publications Office 文件库），不应使
        # 该文件库的 robots.txt 请求被入口域名规则误拦截。
        return self._robots(url, url, allowed_domains).can_fetch(self.config.user_agent, url)
