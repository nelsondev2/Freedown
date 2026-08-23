"""
MoodleUploader — uploads a local file to the Moodle draft area and returns
a permanent, token-authenticated download URL plus the calendar event_id
needed to delete the file later.
"""
from __future__ import annotations

import asyncio
import json
import os
import urllib.parse
from dataclasses import dataclass

import requests

from config import config
from services.converter import ConvertResult, MoodleConverter
from utils.exceptions import UploadError, UrlConversionError
from utils.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class UploadResult:
    """Result of a successful upload."""
    url:      str   # Permanent webservice/pluginfile URL
    event_id: int   # Moodle calendar event that holds the file (needed for deletion)


class MoodleUploader:
    """
    Uploads a file to Moodle and converts the temporary draftfile URL into
    a permanent webservice/pluginfile URL with a token.

    Usage:
        uploader = MoodleUploader()
        result   = uploader.upload(filepath)
        print(result.url, result.event_id)
    """

    def __init__(self) -> None:
        self._host       = config.moodle.host
        self._token      = config.moodle.token
        self._verify_ssl = config.moodle.verify_ssl
        if not self._verify_ssl:
            logger.warning(
                "La verificación TLS de Moodle está desactivada. "
                "Úsalo solo temporalmente hasta renovar su certificado."
            )
        self._converter = MoodleConverter(
            host=self._host,
            user=config.moodle.user,
            password=config.moodle.password,
            token=self._token,
            verify_ssl=self._verify_ssl,
        )

    # ── public API ────────────────────────────────────────────────────────────

    def upload(self, filepath: str) -> UploadResult:
        """
        Upload *filepath* to Moodle.
        Returns UploadResult(url, event_id).
        Raises UploadError or UrlConversionError on failure.
        """
        if not os.path.isfile(filepath):
            raise UploadError(f"Archivo no encontrado: {filepath}")

        filename  = os.path.basename(filepath)
        file_size = os.path.getsize(filepath)
        logger.info("Subiendo '%s' (%d bytes) a Moodle", filename, file_size)

        draft_url, draft_itemid = self._upload_to_draft(filepath, filename)
        logger.debug("Draft URL obtenida: %s  (itemid=%s)", draft_url, draft_itemid)

        result = self._convert(draft_url, draft_itemid, filename)
        logger.info("Subida completada — event_id=%d  url=%s", result.event_id, result.url)
        return UploadResult(url=result.url, event_id=result.event_id)

    def delete(self, event_id: int) -> None:
        """
        Delete the Moodle calendar event (and its attached file) by event_id.
        Raises UrlConversionError on failure.
        """
        self._run(self._converter.delete_event(event_id))

    # ── private helpers ───────────────────────────────────────────────────────

    def _upload_to_draft(self, filepath: str, filename: str) -> tuple:
        """Upload file to Moodle draft area. Returns (draft_url, itemid)."""
        # All non-file params go in the query string so PHP $_FILES stays clean
        upload_url = (
            f"{self._host}/webservice/upload.php"
            f"?token={urllib.parse.quote(self._token)}"
            f"&filearea=draft&itemid=0"
        )

        try:
            with open(filepath, "rb") as fh:
                resp = requests.post(
                    upload_url,
                    files={"file": (filename, fh, "application/octet-stream")},
                    timeout=300,
                    verify=self._verify_ssl,
                )
        except requests.exceptions.SSLError as exc:
            raise UploadError(
                "El certificado HTTPS de Moodle no es válido o expiró. "
                "Renueva el certificado o configura temporalmente "
                "MOODLE_VERIFY_SSL=false."
            ) from exc
        except requests.RequestException as exc:
            raise UploadError(f"Error de red durante la subida: {exc}") from exc

        if resp.status_code != 200:
            raise UploadError(
                f"Moodle devolvió HTTP {resp.status_code}: {resp.text[:300]}"
            )

        logger.debug("Respuesta raw de upload: %s", resp.text[:500])

        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise UploadError(f"Respuesta no es JSON válido: {resp.text[:300]}") from exc

        if not payload:
            raise UploadError(
                "Moodle devolvió lista vacía []. "
                "Causas posibles: tamaño excedido, permisos insuficientes, token inválido."
            )

        entry = payload[0]

        if "exception" in entry:
            raise UploadError(f"Error de Moodle: {entry.get('message', entry)}")

        contextid       = entry.get("contextid")
        itemid          = entry.get("itemid")
        stored_filename = entry.get("filename")

        if not all([contextid, itemid, stored_filename]):
            raise UploadError(f"Respuesta de subida incompleta: {entry}")

        draft_url = (
            f"{self._host}/draftfile.php/{contextid}/user/draft"
            f"/{itemid}/{urllib.parse.quote(stored_filename)}"
        )
        return draft_url, itemid

    def _convert(self, draft_url: str, draft_itemid: int, filename: str) -> ConvertResult:
        try:
            return self._run(self._converter.convert(draft_url, draft_itemid, filename))
        except Exception as exc:
            raise UrlConversionError(f"No se pudo convertir la URL draft: {exc}") from exc

    @staticmethod
    def _run(coro):
        """Run an async coroutine synchronously in a fresh event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()
