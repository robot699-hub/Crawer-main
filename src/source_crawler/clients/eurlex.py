"""EUR-Lex Cellar 的公开 SPARQL 检索与 CELEX 下载地址生成。"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlsplit

import requests

from ..models import Seed, Task
from ..validators.urls import ensure_public_url
from .http import HttpClient


CELLAR_SPARQL_ENDPOINT = "https://publications.europa.eu/webapi/rdf/sparql"
CELLAR_ITEM_PREFIX = "https://publications.europa.eu/resource/cellar/"
CELEX_ID = re.compile(r"^[0-9][0-9A-Z()_.-]+$")
LOGGER = logging.getLogger(__name__)


class CellarQueryError(RuntimeError):
    """Cellar 无法返回可解析的查询结果。"""


class CellarClient:
    """使用官方 Cellar 知识图谱按 EuroVoc 主题发现法规 PDF。"""

    def __init__(self, http: HttpClient):
        self.http = http

    @staticmethod
    def _escape_literal(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _query(self, topics: tuple[str, ...], language: str, limit: int, offset: int) -> str:
        topic_filter = " || ".join(
            f'CONTAINS(LCASE(STR(?topicLabel)), "{self._escape_literal(topic.casefold())}")'
            for topic in topics
        )
        return f"""
PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>
PREFIX purl: <http://purl.org/dc/elements/1.1/>
PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
SELECT DISTINCT ?celex ?topicLabel ?item WHERE {{
  ?work cdm:work_id_document ?celex ;
        cdm:work_is_about_concept_eurovoc ?topic .
  ?topic skos:prefLabel ?topicLabel .
  FILTER(LANG(?topicLabel) = "en")
  FILTER({topic_filter})
  FILTER(REGEX(STR(?celex), "^celex:3[0-9]{{4}}R"))
  ?expression cdm:expression_belongs_to_work ?work ;
              cdm:expression_uses_language ?language .
  ?language purl:identifier "{language}" .
  ?manifestation cdm:manifestation_manifests_expression ?expression ;
                 cdm:manifestation_type ?format .
  FILTER(STRSTARTS(STR(?format), "pdf"))
  ?item cdm:item_belongs_to_manifestation ?manifestation .
}}
LIMIT {limit}
OFFSET {offset}
""".strip()

    @staticmethod
    def _celex_id(value: str) -> str:
        prefix = "/resource/celex/"
        if prefix in value:
            value = value.split(prefix, 1)[1]
        if value.casefold().startswith("celex:"):
            value = value.split(":", 1)[1]
        return value.strip()

    @staticmethod
    def _item_url(value: str) -> str:
        """将 SPARQL 返回的 Cellar 数据流地址规范为安全的 HTTPS URL。"""
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname != "publications.europa.eu"
            or not parts.path.startswith("/resource/cellar/")
        ):
            return ""
        return CELLAR_ITEM_PREFIX + parts.path.removeprefix("/resource/cellar/")

    def iter_discover(self, seed: Seed):
        """按页生成 Cellar 已确认有目标语种 PDF 的法规下载任务。"""
        rules = seed.rules
        result_limit = rules.eurlex_result_limit or rules.max_items or 1000
        page_size = min(rules.eurlex_page_size, result_limit)
        seen: set[str] = set()
        discovered = 0
        offset = 0
        ensure_public_url(CELLAR_SPARQL_ENDPOINT, resolve_dns=self.http.config.verify_dns)
        while discovered < result_limit:
            query = self._query(rules.eurlex_topics, rules.eurlex_language, page_size, offset)
            LOGGER.info("EUR-Lex Cellar 查询：候选 %s/%s", offset + 1, result_limit)
            response: requests.Response | None = None
            last_error: Exception | None = None
            for attempt in range(1, 4):
                self.http._wait(CELLAR_SPARQL_ENDPOINT)
                try:
                    candidate = self.http.session.post(
                        CELLAR_SPARQL_ENDPOINT,
                        data={"query": query},
                        headers={"Accept": "application/sparql-results+json"},
                        timeout=self.http.config.timeout,
                    )
                    if candidate.status_code in {429, 500, 502, 503, 504}:
                        candidate.close()
                        raise requests.RequestException(f"SPARQL 接口返回 HTTP {candidate.status_code}")
                    response = candidate
                    break
                except requests.RequestException as exc:
                    last_error = exc
                    if attempt == 3:
                        break
                    wait_seconds = attempt * 5
                    LOGGER.warning("EUR-Lex Cellar 查询失败（第 %s/3 次）：%s；%s 秒后重试",
                                   attempt, exc, wait_seconds)
                    time.sleep(wait_seconds)
            if response is None:
                raise CellarQueryError(f"请求 SPARQL 接口失败: {last_error}") from last_error
            try:
                if response.status_code >= 400:
                    raise CellarQueryError(f"SPARQL 接口返回 HTTP {response.status_code}")
                payload = response.json()
            except ValueError as exc:
                raise CellarQueryError("SPARQL 接口未返回 JSON 结果") from exc
            finally:
                response.close()
            try:
                bindings = payload["results"]["bindings"]
            except (KeyError, TypeError) as exc:
                raise CellarQueryError("SPARQL 返回缺少 results.bindings") from exc
            if not isinstance(bindings, list):
                raise CellarQueryError("SPARQL 返回的 bindings 格式无效")
            tasks: list[Task] = []
            for binding in bindings:
                if not isinstance(binding, dict):
                    continue
                celex = self._celex_id(str(binding.get("celex", {}).get("value", "")))
                item_url = self._item_url(str(binding.get("item", {}).get("value", "")))
                if not CELEX_ID.fullmatch(celex) or not item_url or celex in seen:
                    continue
                seen.add(celex)
                topic = " ".join(str(binding.get("topicLabel", {}).get("value", "")).split())
                label = f"EUR-Lex {topic or 'agricultural policy'} ({celex})"
                tasks.append(Task(
                    url=item_url,
                    seed=seed,
                    discovered_from=CELLAR_SPARQL_ENDPOINT,
                    link_text=label,
                ))
                discovered += 1
                if discovered >= result_limit:
                    break
            if tasks:
                yield tasks
            if len(bindings) < page_size:
                break
            offset += page_size

    def discover(self, seed: Seed) -> list[Task]:
        """一次性收集任务；用于小规模调用和测试。"""
        return [task for batch in self.iter_discover(seed) for task in batch]
