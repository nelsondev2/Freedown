"""
Bot message handlers.

Flujo de subida:
  1. Usuario envía un enlace HTTP/HTTPS.
  2. El bot descarga el archivo, lo sube a Moodle y responde con:
       • El enlace permanente de descarga.
       • Un enlace mailto: que, al abrirse, pre-rellena un mensaje "/delete <url>"
         al bot para eliminar el archivo del servidor.

Flujo de eliminación:
  1. Usuario envía (o reenvía desde el mailto) "/delete <url>".
  2. El bot extrae el event_id de la URL (patrón /event_description/{id}/).
  3. Borra el evento de calendario en Moodle (y en cascada su archivo).
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
import urllib.parse

from deltachat2 import MsgData, events

from config import config
from services.downloader import Downloader
from services.uploader import MoodleUploader
from utils.exceptions import DownloadError, FileTooLargeError, MoodleBotError, UrlConversionError
from utils.files import file_size_mb, human_size, split_into_volumes
from utils.logger import get_logger

logger = get_logger(__name__)

# Extrae el event_id de URLs tipo /event_description/{id}/
_EVENT_ID_RE = re.compile(r'/event_description/(\d+)/')

# ── Textos ────────────────────────────────────────────────────────────────────

HELP_TEXT = (
    "📦 **FreeDown**\n\n"
    "Envía cualquier enlace público y lo subiré a Moodle.\n\n"
    "**Comandos:**\n"
    "• Pega un enlace `http://...` → descarga y sube automáticamente.\n"
    "• `/delete <enlace>` → elimina el archivo del servidor.\n"
    "• `/help` → muestra este mensaje.\n\n"
    f"**Límite:** {int(config.bot.max_file_size_mb)} MB por archivo."
)


# ── Registro de handlers ──────────────────────────────────────────────────────

def register(cli) -> None:
    """Registra todos los event handlers en el bot."""

    @cli.on(events.RawEvent)
    def _log_raw(bot, accid, event):
        bot.logger.debug(event)

    @cli.on(events.NewMessage(command="/help"))
    def _help(bot, accid, event):
        bot.rpc.send_msg(accid, event.msg.chat_id, MsgData(text=HELP_TEXT))

    @cli.on(events.NewMessage(command="/delete"))
    def _delete(bot, accid, event):
        msg  = event.msg
        text = (msg.text or "").strip()
        # Extraer la URL que sigue a "/delete"
        parts = text.split(None, 1)
        if len(parts) < 2:
            bot.rpc.send_msg(accid, msg.chat_id, MsgData(
                text="❌ Uso: `/delete <enlace>`"
            ))
            return
        url = parts[1].strip()
        _handle_delete(bot, accid, msg, url)

    @cli.on(events.NewMessage)
    def _upload(bot, accid, event):
        msg  = event.msg
        text = (msg.text or "").strip()

        # Ignorar comandos ya manejados arriba
        if text.startswith("/"):
            return

        if not text.startswith(("http://", "https://")):
            return

        _handle_upload(bot, accid, msg, text)


# ── Flujo de subida ───────────────────────────────────────────────────────────

def _handle_upload(bot, accid, msg, url: str) -> None:
    bot.rpc.send_reaction(accid, msg.id, ["⏳"])
    tmp = tempfile.mkdtemp()

    try:
        # 1. Descarga
        filepath = _download_file(bot, accid, msg, url, tmp)
        if filepath is None:
            return

        filename = os.path.basename(filepath)
        size_mb  = file_size_mb(filepath)

        # 2. Dividir en volúmenes si el archivo es grande
        volumes = split_into_volumes(filepath, config.bot.part_size_mb)

        if len(volumes) == 1:
            # ── Archivo pequeño: subida directa ──
            bot.rpc.send_msg(accid, msg.chat_id, MsgData(
                text=(
                    f"📤 Subiendo…\n"
                    f"📄 {filename}\n"
                    f"📦 {human_size(size_mb)}"
                )
            ))

            uploader = MoodleUploader()
            try:
                result = uploader.upload(filepath)
            except MoodleBotError as exc:
                bot.rpc.send_msg(accid, msg.chat_id, MsgData(
                    text=f"❌ Error al subir archivo\n{exc}"
                ))
                bot.rpc.send_reaction(accid, msg.id, ["❌"])
                return

            # 3. Respuesta con enlace + botón de eliminación
            bot.rpc.send_msg(
                accid,
                msg.chat_id,
                MsgData(text=_build_success_message(filename, size_mb, result.url, result.event_id)),
            )
            bot.rpc.send_reaction(accid, msg.id, ["✅"])

        else:
            # ── Archivo grande: subir cada volumen ──
            bot.rpc.send_msg(accid, msg.chat_id, MsgData(
                text=(
                    f"📤 Archivo grande — dividiendo en {len(volumes)} partes…\n"
                    f"📄 {filename}\n"
                    f"📦 {human_size(size_mb)}"
                )
            ))

            uploader = MoodleUploader()
            links: list[str] = []
            event_ids: list[int] = []

            for idx, vol_path in enumerate(volumes, start=1):
                vol_name = os.path.basename(vol_path)
                logger.info("Subiendo volumen %d/%d: %s", idx, len(volumes), vol_name)

                try:
                    result = uploader.upload(vol_path)
                    links.append(result.url)
                    event_ids.append(result.event_id)
                except MoodleBotError as exc:
                    bot.rpc.send_msg(accid, msg.chat_id, MsgData(
                        text=f"❌ Error al subir volumen {idx}/{len(volumes)}\n{vol_name}\n{exc}"
                    ))
                    bot.rpc.send_reaction(accid, msg.id, ["❌"])
                    return

                if idx < len(volumes):
                    time.sleep(config.bot.upload_delay_seconds)

            # Enviar archivo .txt con todos los enlaces
            links_text = _build_links_file(filename, links)
            links_path = os.path.join(tmp, f"{filename}_links.txt")
            with open(links_path, "w", encoding="utf-8") as f:
                f.write(links_text)

            bot.rpc.send_msg(accid, msg.chat_id, MsgData(
                text=(
                    f"✅ **{len(volumes)} partes subidas correctamente**\n\n"
                    f"📄 {filename}\n"
                    f"📦 {human_size(size_mb)}\n\n"
                    f"Descarga todas las partes y abre la primera con 7-Zip/WinRAR."
                )
            ))

            bot.rpc.send_msg(accid, msg.chat_id, MsgData(
                text=links_text
            ))
            bot.rpc.send_reaction(accid, msg.id, ["✅"])

    except Exception as exc:
        logger.exception("Error inesperado en el flujo de subida")
        bot.rpc.send_msg(accid, msg.chat_id, MsgData(
            text=f"❌ Error inesperado\n{exc}"
        ))
        bot.rpc.send_reaction(accid, msg.id, ["❌"])

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _download_file(bot, accid, msg, url: str, tmp: str):
    """Descarga la URL y devuelve la ruta local, o None si falla."""
    downloader = Downloader(max_size_mb=config.bot.max_file_size_mb)
    try:
        return downloader.download(url, tmp)
    except FileTooLargeError as exc:
        bot.rpc.send_msg(accid, msg.chat_id, MsgData(
            text=f"❌ Archivo demasiado grande\n{exc}"
        ))
    except DownloadError as exc:
        bot.rpc.send_msg(accid, msg.chat_id, MsgData(
            text=f"❌ Error al descargar\n{exc}"
        ))
    bot.rpc.send_reaction(accid, msg.id, ["❌"])
    return None


def _build_success_message(filename: str, size_mb: float, url: str, event_id: int) -> str:
    """
    Construye el mensaje de éxito con el enlace de descarga y el botón mailto de eliminación.

    El enlace mailto pre-rellena el comando "/delete <url>" dirigido al bot,
    de modo que el usuario solo tiene que pulsar Enviar en su cliente de correo.
    """
    delete_body    = urllib.parse.quote(f"/delete {url}", safe="")
    delete_mailto  = f"mailto:{config.bot.bot_email}?body={delete_body}"

    return (
        f"✅ **Archivo listo**\n\n"
        f"📄 {filename}\n"
        f"📦 {human_size(size_mb)}\n\n"
        f"🔗 {url}\n\n"
        f"[🗑️ Eliminar del servidor]({delete_mailto})"
    )


def _build_links_file(filename: str, links: list[str]) -> str:
    """Build a plain-text listing of all volume download links."""
    lines = [
        f"FreeDown — enlaces de descarga para: {filename}",
        f"Total de partes: {len(links)}",
        "",
        "Instrucciones:",
        "1. Descarga TODAS las partes en la misma carpeta.",
        "2. Abre la primera parte con 7-Zip o WinRAR.",
        "3. Extrae normalmente — el tool leerá los volúmenes automáticamente.",
        "",
        "─" * 50,
    ]
    for idx, link in enumerate(links, start=1):
        lines.append(f"Parte {idx:04d}:  {link}")
    lines.append("")
    return "\n".join(lines)


# ── Flujo de eliminación ──────────────────────────────────────────────────────

def _handle_delete(bot, accid, msg, url: str) -> None:
    """
    Extrae el event_id de la URL y elimina el evento de Moodle.
    Ejemplo de URL:
      https://host/webservice/pluginfile.php/2797/calendar/event_description/3970/file.ext?token=...
    """
    bot.rpc.send_reaction(accid, msg.id, ["⏳"])

    # Extraer event_id de la URL
    match = _EVENT_ID_RE.search(url)
    if not match:
        bot.rpc.send_msg(accid, msg.chat_id, MsgData(
            text=(
                "❌ No se pudo identificar el archivo en ese enlace.\n"
                "Asegúrate de enviar el enlace exacto que entregó el bot."
            )
        ))
        bot.rpc.send_reaction(accid, msg.id, ["❌"])
        return

    event_id = int(match.group(1))
    logger.info("Eliminando evento Moodle id=%d", event_id)

    uploader = MoodleUploader()
    try:
        uploader.delete(event_id)
    except (MoodleBotError, UrlConversionError) as exc:
        bot.rpc.send_msg(accid, msg.chat_id, MsgData(
            text=f"❌ No se pudo eliminar el archivo\n{exc}"
        ))
        bot.rpc.send_reaction(accid, msg.id, ["❌"])
        return
    except Exception as exc:
        logger.exception("Error inesperado al eliminar evento %d", event_id)
        bot.rpc.send_msg(accid, msg.chat_id, MsgData(
            text=f"❌ Error inesperado al eliminar\n{exc}"
        ))
        bot.rpc.send_reaction(accid, msg.id, ["❌"])
        return

    bot.rpc.send_msg(accid, msg.chat_id, MsgData(
        text=(
            "🗑️ **Archivo eliminado**\n\n"
            "El archivo ha sido borrado del servidor correctamente.\n"
            "El enlace de descarga ya no estará disponible."
        )
    ))
    bot.rpc.send_reaction(accid, msg.id, ["✅"])
