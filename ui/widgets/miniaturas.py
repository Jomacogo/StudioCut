"""Miniaturas ligeras para listas y combos.

`QIcon(ruta)` carga la imagen completa en memoria: con decenas de fotos de
1920×1080 o más, las listas se vuelven lentas y consumen cientos de MB.
Aquí se escala una sola vez y se cachea el icono resultante.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon, QPixmap

_CACHE: dict[tuple[str, int, int], QIcon] = {}
_LIMITE = 400


def icono_lista(ruta: str, ancho: int = 112, alto: int = 63) -> QIcon:
    """Devuelve un icono pequeño y cacheado para la ruta indicada."""
    if not ruta:
        return QIcon()
    try:
        clave = (str(Path(ruta).resolve()), int(ancho), int(alto))
    except OSError:
        return QIcon()
    existente = _CACHE.get(clave)
    if existente is not None:
        return existente

    pix = QPixmap(str(ruta))
    if pix.isNull():
        return QIcon()
    pix = pix.scaled(
        ancho, alto,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    icono = QIcon(pix)
    if len(_CACHE) >= _LIMITE:
        _CACHE.clear()
    _CACHE[clave] = icono
    return icono


def limpiar_cache_miniaturas() -> None:
    _CACHE.clear()
