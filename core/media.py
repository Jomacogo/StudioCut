"""Utilidades de medios: ffprobe/ffmpeg y duración de audios."""
from __future__ import annotations

import shutil
import subprocess
from functools import lru_cache
from pathlib import Path


class ErrorMedia(Exception):
    pass


def ruta_ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise ErrorMedia(
            "No se encontró ffmpeg en el PATH. Instálalo o añádelo al PATH "
            "(winget install ffmpeg o https://www.gyan.dev/ffmpeg/builds/)."
        )
    return exe


def ruta_ffprobe() -> str:
    exe = shutil.which("ffprobe")
    if not exe:
        raise ErrorMedia("No se encontró ffprobe en el PATH.")
    return exe


@lru_cache(maxsize=512)
def _duracion_ffprobe(ruta: str, _mtime: int, _tam: int) -> float:
    """Consulta ffprobe. Cacheada por ruta, fecha de modificación y tamaño."""
    cmd = [
        ruta_ffprobe(),
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(ruta),
    ]
    try:
        salida = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60
        )
    except subprocess.TimeoutExpired as exc:
        raise ErrorMedia(f"Tiempo agotado leyendo {ruta}") from exc
    if salida.returncode != 0:
        raise ErrorMedia(f"No se pudo leer {ruta}: {salida.stderr.strip()}")
    try:
        return float(salida.stdout.strip())
    except ValueError as exc:
        raise ErrorMedia(f"Duración inválida en {ruta}: {salida.stdout!r}") from exc


def duracion_audio(ruta: str) -> float:
    """Duración en segundos de un archivo de audio, vía ffprobe.

    El resultado se cachea: la interfaz consulta la duración muchas veces
    (timeline, tabla, subtítulos) y cada ffprobe cuesta ~100 ms.
    """
    ruta = str(ruta)
    try:
        estado = Path(ruta).stat()
    except OSError:
        return _duracion_ffprobe(ruta, 0, 0)
    return _duracion_ffprobe(ruta, estado.st_mtime_ns, estado.st_size)


def limpiar_cache_duraciones() -> None:
    """Olvida las duraciones memorizadas (útil tras reemplazar un archivo)."""
    _duracion_ffprobe.cache_clear()
