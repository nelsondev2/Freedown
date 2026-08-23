"""
MoodleConverter — authenticates with Moodle via the web UI and uses the
Calendar AJAX endpoint to convert a draftfile URL into a permanent
webservice/pluginfile URL, also returning the calendar event ID so the
file can be deleted later.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Optional

import aiohttp
from bs4 import BeautifulSoup

from utils.exceptions import AuthenticationError, UrlConversionError
from utils.logger import get_logger

logger = get_logger(__name__)

# Compiled once at import time
_SESSKEY_RE = re.compile(r'"sesskey"\s*:\s*"([^"]+)"')
_USERID_RE  = re.compile(r'(?:data-userid="|"userid":)(\d+)')
_PROFILE_RE = re.compile(r'/user/profile\.php\?id=(\d+)')
_URL_RE     = re.compile(r'https?://[^\s<>"]+')


class ConvertResult:
    """Holds the permanent URL and the Moodle calendar event ID that stores the file."""
    __slots__ = ("url", "event_id")

    def __init__(self, url: str, event_id: int) -> None:
        self.url      = url
        self.event_id = event_id

    def __repr__(self) -> str:  # pragma: no cover
        return f"ConvertResult(event_id={self.event_id}, url={self.url!r})"


class MoodleConverter:
    """
    Converts a Moodle draftfile URL to a permanent webservice/pluginfile URL
    by embedding it in a calendar-event description and reading back the
    resolved URL.

    Returns a ConvertResult with both the URL and the event_id so callers
    can delete the event (and its file) later.
    """

    def __init__(
        self,
        host: str,
        user: str,
        password: str,
        token: str,
        verify_ssl: bool = True,
    ) -> None:
        self._host       = host.rstrip("/")
        self._user       = user
        self._password   = password
        self._token      = token
        self._verify_ssl = verify_ssl

    # ── public API ────────────────────────────────────────────────────────────

    async def convert(self, draft_url: str, draft_itemid: int, filename: str) -> ConvertResult:
        """
        Convert a single draftfile URL.
        *draft_itemid* is the Moodle draft-area itemid returned by the upload
        (webservice/upload.php).  *filename* is the original stored filename.
        Returns ConvertResult(url, event_id).
        Raises UrlConversionError on failure.
        """
        connector = aiohttp.TCPConnector(ssl=self._verify_ssl)
        async with aiohttp.ClientSession(connector=connector) as session:
            sesskey, userid = await self._authenticate(session)
            return await self._submit_and_extract(
                session, sesskey, userid, draft_url, draft_itemid, filename,
            )

    async def delete_event(self, event_id: int) -> None:
        """
        Delete a calendar event (and its attached file) by event_id.
        Raises UrlConversionError on failure.
        """
        connector = aiohttp.TCPConnector(ssl=self._verify_ssl)
        async with aiohttp.ClientSession(connector=connector) as session:
            sesskey, _ = await self._authenticate(session)
            await self._do_delete(session, sesskey, event_id)

    # ── authentication ────────────────────────────────────────────────────────

    async def _authenticate(self, session: aiohttp.ClientSession) -> tuple[str, str]:
        login_url = f"{self._host}/login/index.php"

        async with session.get(login_url) as resp:
            html = await resp.text()

        soup        = BeautifulSoup(html, "html.parser")
        tok_input   = soup.find("input", attrs={"name": "logintoken"})
        login_token = tok_input["value"] if tok_input else ""

        async with session.post(login_url, data={
            "anchor":           "",
            "logintoken":       login_token,
            "username":         self._user,
            "password":         self._password,
            "rememberusername": 1,
        }) as resp:
            html = await resp.text()

        sesskey = self._extract_sesskey(html)
        userid  = self._extract_userid(html)

        if not sesskey or not userid:
            raise AuthenticationError(
                "Login fallido: sesskey/userid no encontrados. "
                "Verifica MOODLE_USER y MOODLE_PASS."
            )

        logger.debug("Autenticado — sesskey=%s userid=%s", sesskey, userid)
        return sesskey, userid

    # ── conversion ────────────────────────────────────────────────────────────

    async def _submit_and_extract(
        self,
        session:      aiohttp.ClientSession,
        sesskey:      str,
        userid:       str,
        draft_url:    str,
        draft_itemid: int,
        filename:     str,
    ) -> ConvertResult:
        endpoint = (
            f"{self._host}/lib/ajax/service.php"
            f"?sesskey={sesskey}&info=core_calendar_submit_create_update_form"
        )

        # Use @@PLUGINFILE@@ placeholder so Moodle rewrites it to a permanent
        # pluginfile URL when the calendar event is created.  The file itself
        # lives in the draft area identified by *draft_itemid*.
        description_html    = f'<p dir="ltr"><span style="font-size: 14.25px;">@@PLUGINFILE@@/{filename}</span></p>'
        encoded_description = urllib.parse.quote(description_html)

        formdata = (
            f"id=0&userid={userid}&modulename=&instance=0&visible=1&eventtype=user"
            f"&sesskey={sesskey}&_qf__core_calendar_local_event_forms_create=1"
            f"&mform_showmore_id_general=1&name=Evento"
            f"&timestart[day]=4&timestart[month]=4&timestart[year]=2000"
            f"&timestart[hour]=18&timestart[minute]=55"
            f"&description[text]={encoded_description}"
            f"&description[format]=1&description[itemid]={draft_itemid}"
            f"&location=&duration=0"
        )

        payload = [{
            "index":      0,
            "methodname": "core_calendar_submit_create_update_form",
            "args":       {"formdata": formdata},
        }]

        async with session.post(endpoint, json=payload) as resp:
            data = await resp.json()

        if not data or data[0].get("error"):
            raise UrlConversionError(
                f"Calendar API devolvió error: {data[0] if data else 'respuesta vacía'}"
            )

        event_data = data[0].get("data", {}).get("event", {})
        event_id   = event_data.get("id")
        description = event_data.get("description", "")

        if not event_id:
            raise UrlConversionError("No se obtuvo event_id de la respuesta del calendario.")

        found = _URL_RE.findall(description)
        logger.debug("URLs extraídas de descripción: %s", found)

        if not found:
            raise UrlConversionError(
                "La descripción del evento está vacía — URL no resuelta."
            )

        permanent_url = self._normalize(found[0])
        logger.info("URL permanente: %s  (event_id=%s)", permanent_url, event_id)
        return ConvertResult(url=permanent_url, event_id=int(event_id))

    # ── deletion ──────────────────────────────────────────────────────────────

    async def _do_delete(
        self,
        session: aiohttp.ClientSession,
        sesskey: str,
        event_id: int,
    ) -> None:
        endpoint = (
            f"{self._host}/lib/ajax/service.php"
            f"?sesskey={sesskey}&info=core_calendar_delete_event"
        )

        payload = [{
            "index":      0,
            "methodname": "core_calendar_delete_event",
            "args":       {"eventid": event_id, "repeat": 0},
        }]

        async with session.post(endpoint, json=payload) as resp:
            data = await resp.json()

        if not data or data[0].get("error"):
            err = data[0].get("data") if data else "respuesta vacía"
            raise UrlConversionError(
                f"Error al borrar evento {event_id}: {err}"
            )

        logger.info("Evento %d borrado correctamente.", event_id)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _normalize(self, url: str) -> str:
        url = url.strip().strip('"').strip("'")
        if "/pluginfile.php/" in url and "/webservice/pluginfile.php/" not in url:
            url = url.replace("/pluginfile.php/", "/webservice/pluginfile.php/")
        if "token=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}token={self._token}"
        return url

    @staticmethod
    def _extract_sesskey(html: str) -> Optional[str]:
        m = _SESSKEY_RE.search(html)
        if m:
            return m.group(1)
        soup   = BeautifulSoup(html, "html.parser")
        hidden = soup.find("input", attrs={"name": "sesskey"})
        return hidden.get("value") if hidden else None

    @staticmethod
    def _extract_userid(html: str) -> Optional[str]:
        m = _USERID_RE.search(html)
        if m:
            return m.group(1)
        m = _PROFILE_RE.search(html)
        return m.group(1) if m else None
