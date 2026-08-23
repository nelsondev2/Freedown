"""
File utility functions: naming, size checks, and multi-volume splitting.
"""
from __future__ import annotations

import math
import os
import shutil
import tempfile
import urllib.parse
import zipfile
from pathlib import Path
from typing import Optional

import requests

from utils.logger import get_logger

logger = get_logger(__name__)


def filename_from_response(url: str, response: requests.Response) -> str:
    """
    Derive a safe filename from the Content-Disposition header or the URL path.
    """
    disposition = response.headers.get("Content-Disposition", "")
    if disposition:
        # e.g.: attachment; filename="report.pdf"
        for part in disposition.split(";"):
            part = part.strip()
            if part.startswith("filename="):
                return part.split("=", 1)[1].strip().strip('"').strip("'")

    decoded = urllib.parse.unquote(url, encoding="utf-8", errors="replace")
    name = decoded.rstrip("/").rsplit("/", 1)[-1]
    return name or "downloaded_file"


def file_size_mb(path: str | Path) -> float:
    return os.path.getsize(path) / (1024 * 1024)


def human_size(size_mb: float) -> str:
    if size_mb < 1024:
        return f"{size_mb:.2f} MB"
    return f"{size_mb / 1024:.2f} GB"


def split_into_volumes(filepath: str | Path, part_size_mb: float) -> list[str]:
    """
    Return a list of file paths ready for upload.

    - If the file fits within part_size_mb it is returned as-is (no copy made).
    - Otherwise the file is compressed into a ZIP and split into numbered volumes:
      filename.zip.0001, filename.zip.0002, …
    """
    filepath = Path(filepath)
    size_mb = file_size_mb(filepath)

    logger.info("Evaluating split: %s (%.2f MB), part limit: %.0f MB", filepath.name, size_mb, part_size_mb)

    if size_mb <= part_size_mb:
        logger.info("File fits in a single part — skipping split")
        return [str(filepath)]

    logger.info("File exceeds limit — creating multi-volume ZIP")
    return _create_multivolume_zip(filepath, part_size_mb)


def _create_multivolume_zip(filepath: Path, part_size_mb: float) -> list[str]:
    part_size_bytes = int(part_size_mb * 1024 * 1024)
    zip_path = filepath.with_suffix(".zip")

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        tmp_copy = tmp_dir / filepath.name
        shutil.copy2(filepath, tmp_copy)

        logger.debug("Compressing to %s", zip_path)
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(tmp_copy, filepath.name)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    parts = _split_binary(zip_path, part_size_bytes)

    if len(parts) > 1:
        zip_path.unlink(missing_ok=True)

    return parts


def _split_binary(source: Path, chunk_bytes: int) -> list[str]:
    total_size = os.path.getsize(source)
    num_parts = math.ceil(total_size / chunk_bytes)
    logger.info("Splitting into %d volumes", num_parts)

    parts: list[str] = []

    if num_parts == 1:
        dest = source.with_suffix(source.suffix + ".0001")
        source.rename(dest)
        return [str(dest)]

    with open(source, "rb") as fh:
        for idx in range(num_parts):
            volume_path = Path(f"{source}.{idx + 1:04d}")
            logger.debug("Writing volume %s", volume_path.name)
            fh.seek(idx * chunk_bytes)
            data = fh.read(chunk_bytes)
            volume_path.write_bytes(data)
            parts.append(str(volume_path))

    return parts
