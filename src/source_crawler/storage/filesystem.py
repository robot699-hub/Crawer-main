"""按来源编号保存下载文件，并计算内容摘要。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import requests

from ..validators.urls import safe_filename


class FileStore:
    def __init__(self, output_dir: Path, max_file_mb: int):
        self.output_dir = output_dir
        self.download_dir = output_dir / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.max_file_bytes = max_file_mb * 1024 * 1024

    def save(
        self,
        response: requests.Response,
        source_id: str,
        title: str,
        extension: str,
        url: str,
    ) -> tuple[str, str]:
        expected = response.headers.get("Content-Length")
        if expected and expected.isdigit() and int(expected) > self.max_file_bytes:
            raise ValueError(f"文件超过大小限制（{int(expected) / 1024 / 1024:.1f} MB）")
        folder = self.download_dir / (source_id or "unclassified")
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / safe_filename(title, url, extension)
        temporary = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        total = 0
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 128):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > self.max_file_bytes:
                        raise ValueError(
                            f"文件超过大小限制（{self.max_file_bytes / 1024 / 1024:.0f} MB）"
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return destination.relative_to(self.output_dir).as_posix(), digest.hexdigest()

    def save_content(
        self,
        content: bytes,
        source_id: str,
        title: str,
        extension: str,
        url: str,
    ) -> tuple[str, str]:
        """保存已读取的 HTML 正文等小型内容，并返回相对路径和摘要。"""
        if len(content) > self.max_file_bytes:
            raise ValueError(f"文件超过大小限制（{self.max_file_bytes / 1024 / 1024:.0f} MB）")
        folder = self.download_dir / (source_id or "unclassified")
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / safe_filename(title, url, extension)
        temporary = destination.with_suffix(destination.suffix + ".part")
        try:
            temporary.write_bytes(content)
            temporary.replace(destination)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
        return destination.relative_to(self.output_dir).as_posix(), hashlib.sha256(content).hexdigest()
