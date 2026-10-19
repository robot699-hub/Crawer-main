from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Sequence

from .config import CrawlConfig, DEFAULT_EXCLUDED_DOMAINS, DEFAULT_INPUT, DEFAULT_OUTPUT, USER_AGENT
from .parsers.input import iter_input_seeds, load_job_settings
from .pipeline import Crawler
from .validators.urls import is_excluded

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="通用官方数据、政策文件和文献爬虫")
    parser.add_argument("--url", action="append", default=[], help="入口 URL，可重复")
    parser.add_argument("--input", help="URL 输入文件：json、txt、csv、xlsx 或 xlsm；不指定 --url 时默认 config/site_config.json")
    parser.add_argument("--candidate-id", action="append", default=[], help="仅运行指定候选 id；可重复，如 D02 或 D04-2")
    parser.add_argument("--sheet", help="Excel 工作表名称；默认第一张表")
    parser.add_argument("--header-row", type=int, default=1, help="Excel 表头行，默认 1")
    parser.add_argument("--url-column", help="CSV/Excel 链接列名；默认自动识别")
    parser.add_argument("--output", help="输出目录；默认读取 JSON 的 run.output_dir，否则使用项目下 data/")
    parser.add_argument("--max-depth", type=int, help="站内跟进深度；默认读取配置，否则为 1")
    parser.add_argument("--max-pages", type=int, help="单次运行的页面探索总上限；默认按所选候选配置求和")
    parser.add_argument("--max-pages-per-site", type=int, help="同一站点页面探索上限；配置候选默认与总上限一致")
    parser.add_argument("--min-link-score", type=int, help="普通页面链接的最低相关性分数；默认 2")
    parser.add_argument("--delay", type=float, help="同域名请求间隔秒数；默认 1")
    parser.add_argument("--timeout", type=float, help="请求超时秒数；默认 30")
    parser.add_argument("--max-file-mb", type=int, help="单个文件大小上限 MB；默认 200")
    parser.add_argument("--exclude-domain", action="append", default=[], help="额外排除域名，可重复")
    parser.add_argument("--include-fao", action="store_true", default=None, help="允许抓取 FAO；可写入 run.include_fao")
    parser.add_argument("--ignore-robots", action="store_true", default=None, help="忽略 robots.txt（仅在获得许可时使用）")
    parser.add_argument("--skip-dns-check", action="store_true", default=None, help="跳过公网 DNS 安全检查")
    parser.add_argument("--user-agent", help="自定义 User-Agent；默认读取配置")
    parser.add_argument("--dry-run", action="store_true", help="只读取并列出入口，不发起网络请求")
    parser.add_argument("--verbose", action="store_true", help="显示调试日志")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        input_path = args.input if args.input is not None else (str(DEFAULT_INPUT) if not args.url else None)
        run = load_job_settings(input_path)
        seeds = list(iter_input_seeds(args.url, input_path, args.sheet, args.header_row, args.url_column))
    except Exception as exc:
        logging.error("读取入口失败: %s", exc)
        return 2
    output_format = str(run.get("output_format", "xlsx")).lower().lstrip(".")
    if output_format != "xlsx":
        logging.error("run.output_format 当前只支持 xlsx")
        return 2
    include_fao = args.include_fao if args.include_fao is not None else bool(run.get("include_fao", False))
    configured_exclusions = run.get("exclude_domains", [])
    if not isinstance(configured_exclusions, list) or any(not isinstance(item, str) for item in configured_exclusions):
        logging.error("run.exclude_domains 必须是域名字符串数组")
        return 2
    excluded = [*configured_exclusions, *args.exclude_domain]
    if not include_fao:
        excluded.extend(DEFAULT_EXCLUDED_DOMAINS)
    seeds = [seed for seed in seeds if not is_excluded(seed.url, excluded)]
    if args.candidate_id:
        selected = set(args.candidate_id)
        seeds = [seed for seed in seeds if seed.candidate_id in selected]
        missing = selected - {seed.candidate_id for seed in seeds}
        if missing:
            logging.error("找不到已启用的候选编号: %s", ", ".join(sorted(missing)))
            return 2
    max_candidates = run.get("max_candidates")
    if max_candidates is not None:
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or max_candidates < 1:
            logging.error("run.max_candidates 或 job.latest_items 必须是正整数")
            return 2
        seeds = seeds[:max_candidates]
    if not seeds:
        logging.error("没有可处理的入口 URL")
        return 2
    if args.dry_run:
        for seed in seeds:
            scope_state = "范围已配置" if seed.rules.scope_ready or seed.rules.max_items is None else "范围待配置"
            target = str(seed.rules.max_items) if seed.rules.max_items is not None else "未设置"
            print(f"{seed.candidate_id or seed.source_id}\t{seed.country_or_region}\t{seed.name}"
                  f"\t{scope_state}\t条目目标:{target}\t页面上限:{seed.rules.max_pages or '默认'}\t{seed.url}")
        print(f"共 {len(seeds)} 个入口；排除域名: {', '.join(excluded) or '无'}", file=sys.stderr)
        return 0

    unready = [seed for seed in seeds if seed.rules.max_items is not None and not seed.rules.scope_ready]
    if unready:
        logging.warning("%s 个候选的范围尚未确认，已跳过。请填写 crawl 规则并将 scope_ready 设为 true：%s",
                        len(unready), ", ".join(seed.candidate_id for seed in unready))
        seeds = [seed for seed in seeds if seed not in unready]
    if not seeds:
        logging.error("没有已确认范围的候选可运行；先编辑 config/site_config.json，或用 --dry-run 查看")
        return 2

    output_value = args.output or run.get("output_dir") or str(DEFAULT_OUTPUT)
    output_dir = Path(str(output_value)).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    resume_download_dir = None
    if run.get("resume_download_dir") is not None:
        resume_download_dir = Path(str(run["resume_download_dir"])).expanduser().resolve()
        if not resume_download_dir.is_dir():
            logging.error("resume_download_dir 不存在或不是目录: %s", resume_download_dir)
            return 2
    configured_max_pages = args.max_pages if args.max_pages is not None else run.get("max_pages")
    max_pages = int(configured_max_pages) if configured_max_pages is not None else sum(seed.rules.max_pages or 500 for seed in seeds)
    configured_site_pages = args.max_pages_per_site if args.max_pages_per_site is not None else run.get("max_pages_per_site")
    max_pages_per_site = int(configured_site_pages) if configured_site_pages is not None else (
        max_pages if any(seed.rules.max_items is not None for seed in seeds) else 80
    )
    max_depth = args.max_depth if args.max_depth is not None else int(run.get("max_depth", 1))
    min_link_score = args.min_link_score if args.min_link_score is not None else int(run.get("min_link_score", 2))
    delay = args.delay if args.delay is not None else float(run.get("delay", 1.0))
    timeout = args.timeout if args.timeout is not None else float(run.get("timeout", 30.0))
    max_file_mb = args.max_file_mb if args.max_file_mb is not None else int(run.get("max_file_mb", 200))
    respect_robots = False if args.ignore_robots else bool(run.get("respect_robots", True))
    verify_dns = False if args.skip_dns_check else bool(run.get("verify_dns", True))
    user_agent = args.user_agent or str(run.get("user_agent", USER_AGENT))
    crawler = Crawler(CrawlConfig(
        output_dir=output_dir,
        excluded_domains=tuple(excluded),
        max_depth=max(max_depth, 0),
        max_pages=max(max_pages, 1),
        max_pages_per_site=max(max_pages_per_site, 1),
        delay=max(delay, 0),
        timeout=max(timeout, 1),
        max_file_mb=max(max_file_mb, 1),
        min_link_score=min_link_score,
        respect_robots=respect_robots,
        verify_dns=verify_dns,
        user_agent=user_agent,
        resume_download_dir=resume_download_dir,
    ))
    try:
        crawler.crawl(seeds)
    except KeyboardInterrupt:
        logging.warning("用户中断；正在导出已完成记录")
        crawler.export_records(interrupted=True)
        return 130
    finally:
        crawler.close()
    return 0
