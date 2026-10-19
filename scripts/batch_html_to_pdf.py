from __future__ import annotations

import argparse
import concurrent.futures
import html as html_escape
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
import uuid
from pathlib import Path
from urllib.parse import urljoin

from lxml import etree, html as lxml_html


PRINT_CSS = r"""
@page { size: A4; margin: 13mm 12mm 14mm 12mm; }
html, body { background: white !important; color: #111 !important; }
body {
  font-family: Arial, "Segoe UI", sans-serif;
  font-size: 10pt;
  line-height: 1.42;
  margin: 0;
  overflow-wrap: anywhere;
}
.document-header {
  border-bottom: 1px solid #b8b8b8;
  margin: 0 0 8mm 0;
  padding: 0 0 4mm 0;
}
.document-header h1 { font-size: 17pt; margin: 0 0 2mm 0; line-height: 1.2; }
.document-header p { color: #555; font-size: 8pt; margin: 0; }
h1 { font-size: 16pt; } h2 { font-size: 13.5pt; } h3 { font-size: 11.5pt; }
h1, h2, h3, h4 { break-after: avoid; margin-top: 5mm; margin-bottom: 2mm; }
p, li { orphans: 3; widows: 3; }
a { color: #164f8f; text-decoration: underline; }
table { width: 100%; border-collapse: collapse; margin: 3mm 0; font-size: 8.7pt; }
th, td { border: 1px solid #888; padding: 1.5mm; vertical-align: top; }
th { background: #eeeeee; }
pre, code { font-family: Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
blockquote { border-left: 3px solid #aaa; margin-left: 2mm; padding-left: 4mm; color: #333; }
img, picture, video, canvas { max-width: 100%; height: auto; }
nav, header, footer, aside, form, button, dialog, [role="navigation"], [aria-hidden="true"] {
  display: none !important;
}
* { box-sizing: border-box; }
"""

REMOVE_XPATH = (
    ".//script|.//style|.//noscript|.//template|.//iframe|.//object|.//embed|"
    ".//nav|.//header|.//footer|.//aside|.//form|.//button|.//dialog|"
    ".//*[@role='navigation']|.//*[@aria-hidden='true']"
)


def first_text(nodes: list[etree._Element]) -> str:
    for node in nodes:
        value = " ".join(" ".join(node.itertext()).split())
        if value:
            return value
    return ""


def remove_node(node: etree._Element) -> None:
    parent = node.getparent()
    if parent is not None:
        parent.remove(node)


def clean_document(
    source: Path, cleaned_path: Path, base_url: str = ""
) -> tuple[str, int]:
    parser = lxml_html.HTMLParser(encoding="utf-8", recover=True, remove_comments=True)
    document = lxml_html.fromstring(source.read_bytes(), parser=parser)

    title = first_text(document.xpath("//title"))
    if not title:
        title = first_text(document.xpath("//h1")) or source.stem

    candidates = document.xpath("//main") or document.xpath("//article") or document.xpath("//body")
    content = candidates[0] if candidates else document

    for bad in content.xpath(REMOVE_XPATH):
        remove_node(bad)

    for element in content.iter():
        keep: dict[str, str] = {}
        href = element.get("href") if element.tag == "a" else None
        src = element.get("src") if element.tag in {"img", "source"} else None
        if href:
            keep["href"] = urljoin(base_url, href) if base_url else href
        if src:
            keep["src"] = urljoin(base_url, src) if base_url else src
            if element.get("alt"):
                keep["alt"] = element.get("alt", "")
        if element.tag in {"td", "th"} and element.get("colspan"):
            keep["colspan"] = element.get("colspan", "")
        element.attrib.clear()
        element.attrib.update(keep)

    content_html = lxml_html.tostring(content, encoding="unicode", method="html")
    text_chars = len(" ".join(content.itertext()).strip())
    safe_title = html_escape.escape(title)
    safe_name = html_escape.escape(source.name)
    base_element = f'<base href="{html_escape.escape(base_url, quote=True)}">' if base_url else ""
    final_html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  {base_element}
  <title>{safe_title}</title>
  <style>{PRINT_CSS}</style>
</head>
<body>
  <section class="document-header">
    <h1>{safe_title}</h1>
    <p>Source file: {safe_name}</p>
  </section>
  {content_html}
</body>
</html>"""
    cleaned_path.write_text(final_html, encoding="utf-8")
    return title, text_chars


def find_edge(explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        if not explicit_path.is_file():
            raise FileNotFoundError(f"Microsoft Edge not found: {explicit_path}")
        return explicit_path

    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate

    from_path = shutil.which("msedge") or shutil.which("msedge.exe")
    if from_path:
        return Path(from_path)
    raise FileNotFoundError(
        "Microsoft Edge was not found. Install Edge or pass --edge PATH_TO_MSEDGE.EXE."
    )


def edge_print(edge: Path, cleaned_html: Path, destination: Path, profile_dir: Path) -> None:
    temporary_pdf = destination.with_suffix(destination.suffix + ".part.pdf")
    temporary_pdf.unlink(missing_ok=True)
    cmd = [
        str(edge),
        "--headless=new",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--disable-breakpad",
        "--disable-crash-reporter",
        "--no-sandbox",
        "--no-first-run",
        "--no-default-browser-check",
        "--no-pdf-header-footer",
        f"--user-data-dir={profile_dir}",
        f"--print-to-pdf={temporary_pdf}",
        cleaned_html.resolve().as_uri(),
    ]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if completed.returncode != 0 or not temporary_pdf.is_file() or temporary_pdf.stat().st_size == 0:
        raise RuntimeError(
            f"Edge PDF failure rc={completed.returncode}: {completed.stderr[-800:]}"
        )
    os.replace(temporary_pdf, destination)


def convert_one(
    source: Path, output_dir: Path, source_root: Path, work_dir: Path,
    edge: Path, base_url: str,
) -> dict[str, object]:
    digest = hashlib.sha1(str(source).encode("utf-8")).hexdigest()[:12]
    cleaned = work_dir / f"{digest}.html"
    profile = work_dir / f"profile_{digest}"
    relative = source.relative_to(source_root)
    destination = (output_dir / relative).with_suffix(".pdf")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        title, text_chars = clean_document(source, cleaned, base_url)
        if text_chars < 20:
            raise ValueError(f"cleaned HTML contains too little text ({text_chars} characters)")
        edge_print(edge, cleaned, destination, profile)
        return {
            "ok": True,
            "source": str(source),
            "output": str(destination),
            "bytes": destination.stat().st_size,
            "text_chars": text_chars,
            "title": title,
        }
    except Exception as exc:
        return {
            "ok": False,
            "source": str(source),
            "output": str(destination),
            "error": str(exc),
            "traceback": traceback.format_exc(limit=5),
        }
    finally:
        cleaned.unlink(missing_ok=True)
        shutil.rmtree(profile, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recursively convert HTML files to cleaned, print-ready PDF files using Microsoft Edge."
    )
    parser.add_argument("source_dir", type=Path, help="Directory containing HTML files")
    parser.add_argument("output_dir", type=Path, help="Directory for generated PDF files")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel conversions (default: 4)")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of files to convert; 0 means all")
    parser.add_argument("--match", default="", help="Only convert filenames containing this text")
    parser.add_argument("--base-url", default="", help="Base URL for resolving relative links and images")
    parser.add_argument("--edge", type=Path, help="Path to msedge.exe when Edge is not auto-detected")
    parser.add_argument(
        "--report", type=Path, default=Path("html_pdf_conversion_report.json"),
        help="JSON report path (default: html_pdf_conversion_report.json)",
    )
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.limit < 0:
        parser.error("--limit cannot be negative")
    return args


def main() -> int:
    args = parse_args()
    source_root = args.source_dir.resolve()
    if not source_root.is_dir():
        raise NotADirectoryError(f"HTML source directory not found: {source_root}")
    edge = find_edge(args.edge)
    sources = sorted(
        (path for path in source_root.rglob("*") if path.is_file() and path.suffix.lower() in {".html", ".htm"}),
        key=lambda path: str(path).casefold(),
    )
    if args.match:
        needle = args.match.casefold()
        sources = [path for path in sources if needle in path.name.casefold()]
    if args.limit:
        sources = sources[:args.limit]

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    work_root = Path.cwd() / "tmp" / f"batch_html_pdf_{uuid.uuid4().hex[:10]}"
    work_root.mkdir(parents=True, exist_ok=False)
    results: list[dict[str, object]] = []
    lock = threading.Lock()
    completed_count = 0
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(
                    convert_one, source, output_dir, source_root, work_root, edge, args.base_url
                ): source
                for source in sources
            }
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
                with lock:
                    completed_count += 1
                    if completed_count % 50 == 0 or completed_count == len(sources):
                        successes = sum(1 for item in results if item["ok"])
                        print(
                            f"progress={completed_count}/{len(sources)} "
                            f"success={successes} failure={len(results)-successes}",
                            flush=True,
                        )
    finally:
        shutil.rmtree(work_root, ignore_errors=True)

    results.sort(key=lambda item: str(item["source"]).casefold())
    report = {
        "source_dir": str(source_root),
        "output_dir": str(output_dir),
        "source_count": len(sources),
        "success_count": sum(1 for item in results if item["ok"]),
        "failure_count": sum(1 for item in results if not item["ok"]),
        "results": results,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8-sig")
    print(f"report={args.report}", flush=True)
    return 0 if report["failure_count"] == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
