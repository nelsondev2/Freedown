"""
Downloader — streams a remote file to disk with proper error handling.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests

from utils.exceptions import DownloadError, FileTooLargeError
from utils.files import filename_from_response, file_size_mb, human_size
from utils.logger import get_logger

logger = get_logger(__name__)

_CHUNK_SIZE = 65_536  # 64 KiB


class Downloader:
    def __init__(self, max_size_mb: float, timeout: int = 30) -> None:
        self._max_size_mb = max_size_mb
        self._timeout = timeout

    def download(self, url: str, dest_dir: str | Path) -> str:
        """
        Stream *url* into *dest_dir* and return the absolute path of the
        saved file. Raises DownloadError or FileTooLargeError on problems.
        """
        dest_dir = Path(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)

        logger.info("Starting download: %s", url)

        try:
            with requests.get(url, stream=True, timeout=self._timeout) as resp:
                if resp.status_code != 200:
                    raise DownloadError(
                        f"Server returned HTTP {resp.status_code} for URL: {url}"
                    )

                filename = filename_from_response(url, resp)
                filepath = dest_dir / filename

                logger.debug("Saving to: %s", filepath)

                downloaded_bytes = 0
                with open(filepath, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=_CHUNK_SIZE):
                        fh.write(chunk)
                        downloaded_bytes += len(chunk)

        except requests.RequestException as exc:
            raise DownloadError(f"Network error while downloading {url}: {exc}") from exc

        size_mb = file_size_mb(filepath)
        logger.info("Downloaded '%s' — %s", filename, human_size(size_mb))

        if size_mb > self._max_size_mb:
            os.unlink(filepath)
            raise FileTooLargeError(
                f"File is {human_size(size_mb)}, which exceeds the "
                f"{human_size(self._max_size_mb)} limit."
            )

        return str(filepath)
