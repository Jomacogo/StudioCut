"""Forma de onda real del audio para la pista A1 del timeline.

Antes se dibujaba una onda sintética decorativa. Aquí se decodifica el audio
con FFmpeg a PCM mono 8 kHz y se guarda un resumen de amplitudes en caché,
de modo que abrir el mismo proyecto no vuelve a analizar los archivos.
"""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
from array import array
from pathlib import Path

from .media import ErrorMedia, ruta_ffmpeg

CARPETA_CACHE = Path(__file__).resolve().parent.parent / "ui" / ".config" / "wavecache"
MUESTREO = 8000
PICOS_POR_SEGUNDO = 50  # resolución del resumen guardado


def _ruta_cache(archivo: Path, mtime: int, tamano: int) -> Path:
    nombre = hashlib.sha1(str(archivo).encode("utf-8")).hexdigest()[:16]
    return CARPETA_CACHE / f"{nombre}_{mtime}_{tamano}.json"


def _leer_cache(ruta: Path) -> list[float] | None:
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    valores = datos.get("valores")
    if not isinstance(valores, list):
        return None
    try:
        return [float(v) for v in valores]
    except (TypeError, ValueError):
        return None


def _escribir_cache(ruta: Path, valores: list[float]) -> None:
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps({"valores": valores}, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError:
        # La caché es una optimización: si no se puede escribir, se sigue.
        pass


def picos_audio(ruta: str, por_segundo: int = PICOS_POR_SEGUNDO) -> list[float]:
    """Amplitudes 0..1 (una por cada 1/``por_segundo`` segundos de audio)."""
    archivo = Path(ruta)
    try:
        estado = archivo.stat()
    except OSError as exc:
        raise ErrorMedia(f"No se pudo leer {archivo.name}") from exc

    cache = _ruta_cache(archivo, estado.st_mtime_ns, estado.st_size)
    valores = _leer_cache(cache)
    if valores is not None:
        return valores

    comando = [
        ruta_ffmpeg(), "-v", "error", "-i", str(archivo),
        "-vn", "-ac", "1", "-ar", str(MUESTREO), "-f", "s16le", "-",
    ]
    try:
        salida = subprocess.run(comando, capture_output=True, timeout=900)
    except subprocess.TimeoutExpired as exc:
        raise ErrorMedia(f"Tiempo agotado analizando {archivo.name}") from exc
    if salida.returncode != 0:
        detalle = salida.stderr.decode("utf-8", "ignore").strip()
        raise ErrorMedia(detalle or f"FFmpeg no pudo leer {archivo.name}")

    crudo = salida.stdout
    muestras = array("h")
    muestras.frombytes(crudo[: len(crudo) - (len(crudo) % 2)])
    if not muestras:
        raise ErrorMedia(f"{archivo.name} no contiene audio legible.")

    tramo = max(1, MUESTREO // max(1, por_segundo))
    crudos: list[float] = []
    for inicio in range(0, len(muestras), tramo):
        bloque = muestras[inicio: inicio + tramo]
        if not bloque:
            break
        crudos.append(max(max(bloque), -min(bloque)) / 32768.0)

    pico = max(crudos) or 1.0
    valores = [round(math.sqrt(min(1.0, v / pico)), 4) for v in crudos]
    _escribir_cache(cache, valores)
    return valores


def onda_combinada(
    rutas: list[str],
    duraciones: list[float],
    puntos: int = 900,
) -> list[float]:
    """Mezcla las ondas de varios audios en una sola de ``puntos`` valores.

    El timeline muestra los audios concatenados, así que cada archivo ocupa un
    tramo proporcional a su duración dentro de la onda total.
    """
    puntos = max(20, int(puntos))
    total = sum(d for d in duraciones if d and d > 0) or 1.0
    salida = [0.0] * puntos
    desplazamiento = 0.0

    for ruta, duracion in zip(rutas, duraciones):
        if not ruta or not duracion or duracion <= 0:
            continue
        try:
            valores = picos_audio(ruta)
        except ErrorMedia:
            desplazamiento += duracion
            continue
        inicio = int(desplazamiento / total * puntos)
        desplazamiento += duracion
        fin = int(desplazamiento / total * puntos)
        ancho = max(1, fin - inicio)
        if not valores:
            continue
        largo = len(valores)
        for k in range(ancho):
            destino = inicio + k
            if destino >= puntos:
                break
            a = int(k * largo / ancho)
            b = max(a + 1, int((k + 1) * largo / ancho))
            salida[destino] = max(valores[a:min(b, largo)] or [0.0])

    return salida
