"""组织入口遍历、页面解析、下载与目录更新。"""

from __future__ import annotations

import logging
import hashlib
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Iterable
from urllib.parse import urlsplit

from .clients.http import HttpClient
from .clients.eurlex import CellarClient, CellarQueryError
from .config import CrawlConfig
from .models import Record, Seed, Task, utc_now
from .parsers.page import classify_source, discover_links, extract_metadata, extract_years, score_link
from .storage.catalogue import StateStore
from .storage.filesystem import FileStore
from .storage.logger import CrawlLogger
from .validators.scope import is_dataset_detail, is_pagination_link, scope_mismatch
from .validators.urls import detect_extension, is_excluded, normalize_url, within_site_scope


LIVE_EXPORT_INTERVAL = 25


class Crawler:
    def __init__(self, config: CrawlConfig):
        self.config = config
        self.output_dir = config.output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.state = StateStore(self.output_dir / "crawler_state.sqlite3")
        self.http = HttpClient(config)
        self.eurlex = CellarClient(self.http)
        self.files = FileStore(self.output_dir, config.max_file_mb)
        self.logger = CrawlLogger(self.output_dir)
        self._seeds: dict[str, Seed] = {}
        self._candidate_counts: dict[str, int] = defaultdict(int)
        self._item_counts: dict[str, int] = defaultdict(int)
        self._global_limit_reached = False
        self._site_cap_hits: set[str] = set()
        self._records_since_export = 0
        self._previous_download_hashes = self._load_previous_download_hashes(config.resume_download_dir)
        if self._previous_download_hashes:
            logging.info("续跑基线：识别到 %s 个先前下载文件，将跳过相同的 Cellar 数据流", len(self._previous_download_hashes))

    def close(self) -> None:
        self.state.close()
        self.http.close()

    @staticmethod
    def _load_previous_download_hashes(directory: Path | None) -> set[str]:
        """从既有 PDF 文件名读取由 safe_filename 生成的 URL 指纹。"""
        if directory is None:
            return set()
        pattern = re.compile(r"_([0-9a-f]{10})\.pdf$", re.IGNORECASE)
        hashes: set[str] = set()
        for path in directory.rglob("*.pdf"):
            match = pattern.search(path.name)
            if match:
                hashes.add(match.group(1).lower())
        return hashes

    def _was_previously_downloaded(self, task: Task) -> bool:
        fingerprint = hashlib.sha256(task.url.encode("utf-8")).hexdigest()[:10]
        return fingerprint in self._previous_download_hashes

    def _base_record(self, task: Task) -> Record:
        return Record(
            candidate_id=task.seed.candidate_id,
            source_id=task.seed.source_id,
            priority=task.seed.priority,
            record_name=task.link_text or task.seed.name,
            first_batch_scope=task.seed.first_batch_scope,
            license_and_boundaries=task.seed.license_and_boundaries,
            country_or_region=task.seed.country_or_region,
            publisher=task.seed.publisher,
            publication_date=task.seed.publication_date,
            usage_license=task.seed.usage_license or "未识别，需人工核验",
            original_url=task.url,
            seed_url=task.seed.url,
            discovered_from=task.discovered_from,
        )

    @staticmethod
    def _refresh_seed_metadata(record: Record, seed: Seed) -> Record:
        """让增量复查记录采用配置文件中已核实的元数据。"""
        if seed.name:
            record.record_name = seed.name
        if seed.country_or_region:
            record.country_or_region = seed.country_or_region
        if seed.publisher:
            record.publisher = seed.publisher
        if seed.publication_date:
            record.publication_date = seed.publication_date
        if seed.usage_license:
            record.usage_license = seed.usage_license
        record.first_batch_scope = seed.first_batch_scope or record.first_batch_scope
        record.license_and_boundaries = seed.license_and_boundaries or record.license_and_boundaries
        return record

    def _failed_record(self, task: Task, reason: str, status: str = "") -> Record:
        record = self._base_record(task)
        record.failure_reason = reason[:500]
        record.download_status = "失败"
        record.http_status = status
        record.access_conditions = "无法访问"
        return record

    def process(self, task: Task) -> tuple[Record, list[Task]]:
        if is_excluded(task.url, self.config.excluded_domains):
            record = self._failed_record(task, "域名在排除列表中")
            record.download_status = "已跳过"
            return record, []
        rules = task.seed.rules
        if not within_site_scope(task.url, task.seed.url, rules.allowed_domains):
            return self._restricted_record(task, "地址超出该入口的允许域名"), []
        known_extension = detect_extension(task.url)
        if known_extension and rules.file_types and known_extension.lstrip(".") not in rules.file_types:
            return self._restricted_record(task, "文件类型不在该入口配置的允许范围内", known_extension), []
        if known_extension:
            mismatch = scope_mismatch(rules, f"{task.link_text or (task.seed.name if task.depth == 0 else '')} {task.url}")
            if mismatch:
                record = self._restricted_record(task, mismatch, known_extension)
                record.scope_status = "范围外"
                return record, []
        if known_extension and not rules.download:
            record = self._restricted_record(task, "配置为仅记录链接，不下载文件", known_extension, "仅记录链接")
            record.item_type = "文件"
            record.scope_status = "范围内"
            return record, []
        previous = self.state.get_url_state(task.url)
        prior_record = self.state.get_record(task.url, task.seed.candidate_id)
        try:
            if not self.http.allowed(task.url, task.seed.url, rules.allowed_domains):
                record = self._failed_record(task, "robots.txt 不允许抓取")
                record.download_status = "已跳过"
                record.access_conditions = "robots.txt 限制"
                return record, []
            conditional_headers: dict[str, str] = {}
            # HTML 页面需要重新解析链接，才能在再次运行时继续发现尚未采集的条目。
            reuse_file = bool(prior_record and detect_extension(task.url))
            if reuse_file and previous.get("etag"):
                conditional_headers["If-None-Match"] = previous["etag"]
            if reuse_file and previous.get("last_modified"):
                conditional_headers["If-Modified-Since"] = previous["last_modified"]
            if rules.discovery_mode == "eurlex_cellar" and task.url.startswith(
                "https://publications.europa.eu/resource/celex/"
            ):
                conditional_headers.update({
                    "Accept": "application/pdf",
                    "Accept-Language": rules.eurlex_language,
                })
            response = self.http.get(task.url, headers=conditional_headers,
                                     site_seed_url=task.seed.url, allowed_domains=rules.allowed_domains)
        except Exception as exc:
            if previous.get("local_path"):
                record = self._refresh_seed_metadata(prior_record or self._base_record(task), task.seed)
                record.local_path = previous.get("local_path", "").replace("\\", "/")
                record.sha256 = previous.get("sha256", "")
                record.file_type = (known_extension or detect_extension(task.url)).lstrip(".").upper()
                record.item_type = "文件"
                record.scope_status = "范围内"
                record.download_status = "未变化"
                record.failure_reason = f"本次复查失败，已保留先前下载文件: {exc}"[:500]
                return record, []
            return self._failed_record(task, f"请求异常: {exc}"), []

        try:
            if response.status_code == 304:
                record = prior_record or self._base_record(task)
                record = self._refresh_seed_metadata(record, task.seed)
                record.download_status = "未变化"
                record.local_path = previous.get("local_path", "").replace("\\", "/")
                record.sha256 = previous.get("sha256", "")
                if known_extension:
                    record.file_type = known_extension.lstrip(".").upper()
                    record.item_type = "文件"
                    record.scope_status = "范围内"
                record.failure_reason = ""
                record.http_status = "304"
                record.download_date = utc_now()
                return record, []

            if response.status_code >= 400:
                record = self._failed_record(task, f"HTTP {response.status_code}", str(response.status_code))
                if response.status_code in {401, 403}:
                    record.access_conditions = "需要登录、授权或站点拒绝访问"
                elif response.status_code == 429:
                    record.access_conditions = "访问频率受限"
                return record, []

            final_url = normalize_url(response.url or task.url) or task.url
            if is_excluded(final_url, self.config.excluded_domains):
                record = self._failed_record(task, "重定向至排除域名")
                record.download_status = "已跳过"
                return record, []
            if not within_site_scope(final_url, task.seed.url, rules.allowed_domains):
                return self._restricted_record(task, "重定向地址超出该入口的允许域名"), []
            content_type = response.headers.get("Content-Type", "")
            extension = detect_extension(final_url, content_type, response.headers.get("Content-Disposition", ""))
            if extension and "html" not in content_type.lower():
                if rules.file_types and extension.lstrip(".") not in rules.file_types:
                    return self._restricted_record(task, "文件类型不在该入口配置的允许范围内", extension), []
                mismatch = scope_mismatch(rules, f"{task.link_text or (task.seed.name if task.depth == 0 else '')} {final_url}")
                if mismatch:
                    record = self._restricted_record(task, mismatch, extension)
                    record.scope_status = "范围外"
                    return record, []
                if not rules.download:
                    record = self._restricted_record(task, "配置为仅记录链接，不下载文件", extension, "仅记录链接")
                    record.original_url = final_url
                    record.item_type = "文件"
                    record.scope_status = "范围内"
                    return record, []
                record = self._base_record(task)
                record.original_url = final_url
                record.record_name = task.link_text or task.seed.name or Path(urlsplit(final_url).path).name
                record.scope_status = "范围内"
                record.file_type = extension.lstrip(".").upper()
                record.download_date = utc_now()
                record.http_status = str(response.status_code)
                record.source_category = classify_source(record.record_name, final_url)
                record.coverage_years = extract_years(record.record_name)
                if record.source_category == "政策相关页面（待核验）":
                    record.policy_status = "待核验"
                try:
                    local_path, digest = self.files.save(
                        response, task.seed.source_id, record.record_name, extension, final_url
                    )
                    record.local_path = local_path
                    record.sha256 = digest
                    record.download_status = "已下载"
                    record.item_type = "文件"
                    self.state.save_url_state(final_url, response.headers, digest, local_path)
                except Exception as exc:
                    record.download_status = "失败"
                    record.failure_reason = str(exc)
                return record, []

            mime = content_type.split(";", 1)[0].lower()
            if "html" not in mime and mime not in {"", "text/plain"}:
                record = self._failed_record(task, f"不支持的内容类型: {content_type}", str(response.status_code))
                record.file_type = mime
                return record, []

            raw = response.content
            encoding = response.encoding or response.apparent_encoding or "utf-8"
            html = raw.decode(encoding, errors="replace")
            record, soup = extract_metadata(html, final_url, task.link_text or task.seed.name)
            base = self._base_record(task)
            record.source_id = base.source_id
            record.candidate_id = base.candidate_id
            record.priority = base.priority
            record.first_batch_scope = base.first_batch_scope
            record.license_and_boundaries = base.license_and_boundaries
            record.country_or_region = base.country_or_region
            record.seed_url = base.seed_url
            record.discovered_from = base.discovered_from
            record.download_date = utc_now()
            record.http_status = str(response.status_code)
            record.file_type = "HTML"
            # 对已人工确认的入口，候选名称本身是范围证据的一部分。部分机构页先
            # 输出很长的全站导航，主题正文位于截取窗口之后；仅按正文开头核验会把
            # 这类入口错误标为范围外，进而漏存正文快照。
            seed_label = task.seed.name if task.depth == 0 else ""
            mismatch = scope_mismatch(
                rules,
                f"{seed_label} {record.record_name} {final_url} {soup.get_text(' ', strip=True)[:12000]}",
            )
            record.scope_status = "范围外" if mismatch else "范围内"
            if not mismatch and is_dataset_detail(final_url, rules):
                record.item_type = "数据集详情页"
            if record.source_category == "政策相关页面（待核验）" and not record.policy_status:
                record.policy_status = "待核验"
            if not mismatch and rules.save_html:
                try:
                    local_path, digest = self.files.save_content(
                        raw, task.seed.source_id, record.record_name, ".html", final_url
                    )
                    record.local_path = local_path
                    record.sha256 = digest
                    record.item_type = "HTML 正文"
                    record.download_status = "HTML正文已保存"
                except Exception as exc:
                    record.failure_reason = f"HTML 正文保存失败: {exc}"[:500]
            links = discover_links(soup, final_url)
            downloads = [item for item in links if detect_extension(item[0])]
            if not record.local_path:
                record.download_status = "发现可下载文件" if downloads else "仅记录网页"
            if not downloads and not record.local_path and not record.failure_reason:
                record.failure_reason = "页面未发现可直接下载的目标文件"
            self.state.save_url_state(final_url, response.headers)

            discovered: list[Task] = []
            # 范围外的 HTML 页面可作为审计记录保留，但不应成为继续发现链接的
            # 跳板；否则一个宽泛的数据库导航页会把抓取带到无关主题。
            if mismatch:
                return record, discovered
            max_depth = rules.max_depth if rules.max_depth is not None else self.config.max_depth
            min_link_score = rules.min_link_score if rules.min_link_score is not None else self.config.min_link_score
            if max_depth > 0:
                for url, link_text, score in links:
                    child_depth = task.depth if is_pagination_link(url, link_text) else task.depth + 1
                    if child_depth > max_depth:
                        continue
                    if score < min_link_score and not detect_extension(url):
                        continue
                    if is_excluded(url, self.config.excluded_domains):
                        continue
                    if not within_site_scope(url, task.seed.url, rules.allowed_domains):
                        continue
                    label = f"{link_text} {url}".casefold()
                    if any(term.casefold() in label for term in rules.exclude_terms):
                        continue
                    if detect_extension(url) and scope_mismatch(rules, f"{link_text} {url}"):
                        continue
                    discovered.append(
                        Task(url=url, seed=task.seed, depth=child_depth,
                             discovered_from=final_url, link_text=link_text)
                    )
            return record, discovered
        finally:
            response.close()

    def _restricted_record(self, task: Task, reason: str, extension: str = "", status: str = "已跳过") -> Record:
        record = self._base_record(task)
        record.download_status = status
        record.failure_reason = reason
        record.access_conditions = "配置限制"
        record.file_type = extension.lstrip(".").upper()
        record.download_date = utc_now()
        return record

    def _drain_queue(
        self,
        queue: deque[Task],
        visited: set[tuple[str, str]],
        site_counts: dict[str, int],
        processed: int,
    ) -> int:
        """处理一个已发现的任务批次，并保留跨批次的去重与计数。"""
        while queue and processed < self.config.max_pages:
            task = queue.popleft()
            url = normalize_url(task.url)
            candidate_key = task.seed.candidate_id or task.seed.url
            visit_key = (candidate_key, url)
            if not url or visit_key in visited:
                continue
            host = (urlsplit(url).hostname or "").lower()
            candidate_limit = task.seed.rules.max_pages
            if candidate_limit is not None and self._candidate_counts[candidate_key] >= candidate_limit:
                continue
            item_limit = task.seed.rules.max_items
            if item_limit is not None and self._item_counts[candidate_key] >= item_limit:
                continue
            if site_counts[host] >= self.config.max_pages_per_site:
                self._site_cap_hits.add(candidate_key)
                continue
            visited.add(visit_key)
            site_counts[host] += 1
            self._candidate_counts[candidate_key] += 1
            processed += 1
            task = Task(url=url, seed=task.seed, depth=task.depth,
                        discovered_from=task.discovered_from, link_text=task.link_text)
            logging.info("[%s/%s] depth=%s %s", processed, self.config.max_pages, task.depth, url)
            record, discovered = self.process(task)
            self._save_record(record)
            if record.item_type and record.scope_status == "范围内" and record.download_status in {
                "已下载", "仅记录链接", "仅记录网页", "发现可下载文件", "未变化", "HTML正文已保存"
            }:
                self._item_counts[candidate_key] += 1
            discovered.sort(key=lambda item: (not bool(detect_extension(item.url)),
                                               -score_link(item.url, item.link_text)))
            for child in reversed(discovered):
                if (candidate_key, child.url) not in visited:
                    queue.appendleft(child)
        return processed

    def _save_record(self, record: Record) -> None:
        """持久化单条记录，并定期刷新用户可直接打开的 Excel 索引。"""
        self.state.save_record(record)
        self.logger.write(record)
        self._records_since_export += 1
        if self._records_since_export >= LIVE_EXPORT_INTERVAL:
            self._export_records_safely(in_progress=True)
            self._records_since_export = 0

    def _export_records_safely(self, *, in_progress: bool = False, interrupted: bool = False) -> None:
        """导出失败不应中断已在进行的下载（常见原因是 Excel 正在打开）。"""
        try:
            self.export_records(interrupted=interrupted, in_progress=in_progress)
        except OSError as exc:
            logging.warning("无法刷新采集结果.xlsx（请先关闭该文件后等待下一批刷新）：%s", exc)

    def _write_cellar_notice(self, seed: Seed, reason: str, status: str, access: str) -> None:
        task = Task(url=seed.url, seed=seed)
        record = self._failed_record(task, reason)
        record.download_status = status
        record.access_conditions = access
        self._save_record(record)

    def crawl(self, seeds: Iterable[Seed]) -> None:
        seed_list = list(seeds)
        self._seeds = {seed.candidate_id or seed.url: seed for seed in seed_list}
        visited: set[tuple[str, str]] = set()
        site_counts: dict[str, int] = defaultdict(int)
        processed = 0

        for seed in seed_list:
            if processed >= self.config.max_pages:
                break
            if seed.rules.discovery_mode != "eurlex_cellar":
                processed = self._drain_queue(
                    deque([Task(url=seed.url, seed=seed)]), visited, site_counts, processed
                )
                continue

            found = False
            skipped_previous = False
            try:
                for batch in self.eurlex.iter_discover(seed):
                    if processed >= self.config.max_pages:
                        break
                    candidate_key = seed.candidate_id or seed.url
                    item_limit = seed.rules.max_items
                    if item_limit is not None and self._item_counts[candidate_key] >= item_limit:
                        break
                    new_tasks = [task for task in batch if not self._was_previously_downloaded(task)]
                    skipped = len(batch) - len(new_tasks)
                    if skipped:
                        skipped_previous = True
                        logging.info("EUR-Lex Cellar 跳过 %s 个已在先前批次下载的文件", skipped)
                    if not new_tasks:
                        continue
                    found = True
                    logging.info(
                        "EUR-Lex Cellar 本批发现 %s 个候选文件，开始下载：%s",
                        len(new_tasks), seed.candidate_id,
                    )
                    processed = self._drain_queue(deque(new_tasks), visited, site_counts, processed)
            except CellarQueryError as exc:
                self._write_cellar_notice(
                    seed, f"EUR-Lex Cellar 检索失败: {exc}", "失败", "Cellar 接口无法访问"
                )
                logging.error("EUR-Lex Cellar 检索失败：%s", exc)
                continue

            if not found and not skipped_previous:
                self._write_cellar_notice(
                    seed, "EUR-Lex Cellar 未发现符合主题和语言条件的 PDF",
                    "仅记录网页", "公开接口未返回匹配项",
                )
                logging.warning("EUR-Lex Cellar 未发现候选文件：%s", seed.candidate_id)
            elif not found:
                logging.info("EUR-Lex Cellar 返回的候选文件均已在先前批次下载：%s", seed.candidate_id)

        self._global_limit_reached = processed >= self.config.max_pages
        self._export_records_safely()
        logging.info("完成：处理 %s 个地址，符合范围的数据条目 %s 条，记录写入 %s",
                     processed, sum(self._item_counts.values()), self.output_dir)

    def export_records(self, interrupted: bool = False, in_progress: bool = False) -> None:
        self.state.export_records(self.output_dir)
        progress = []
        for key, seed in self._seeds.items():
            pages = self._candidate_counts[key]
            items = self._item_counts[key]
            if in_progress:
                reason = "正在采集中"
            elif seed.rules.max_items is not None and items >= seed.rules.max_items:
                reason = "达到条目目标"
            elif seed.rules.max_pages is not None and pages >= seed.rules.max_pages:
                reason = "达到页面探索上限"
            elif interrupted:
                reason = "用户中断"
            elif self._global_limit_reached:
                reason = "达到本次总页面上限"
            elif key in self._site_cap_hits:
                reason = "达到站点页面上限"
            else:
                reason = "可发现链接耗尽"
            progress.append({
                "candidate_id": seed.candidate_id,
                "source_id": seed.source_id,
                "seed_url": seed.url,
                "target_items": seed.rules.max_items or "",
                "items_collected": items,
                "pages_processed": pages,
                "stop_reason": reason,
            })
        self.state.export_progress(self.output_dir, progress)
