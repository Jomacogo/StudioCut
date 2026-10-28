"""Gestión de proyectos (.stcut) estilo Premiere (.prproj) o DaVinci (.drp).

Permite guardar y cargar todo el estado del proyecto de video:
- Lista de audios e imágenes
- Marcas de capítulos con tiempos exactos y asignaciones
- Modo de edición
- Ajustes de renderizado (resolución, transiciones, Ken Burns, subtítulos)
- Metadatos de publicación en YouTube
- Guion y voz TTS
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

EXTENSION_PROYECTO = ".stcut"
FILTRO_PROYECTO = "Proyecto Studio Cut (*.stcut)"


class ErrorProyecto(Exception):
    """Error al leer o escribir un archivo de proyecto."""


def guardar_proyecto(ruta: str | Path, datos: dict) -> Path:
    """Guarda los datos del proyecto en formato .stcut estructurado."""
    p = Path(ruta)
    if not p.name.lower().endswith(EXTENSION_PROYECTO):
        p = p.with_suffix(EXTENSION_PROYECTO)

    paquete = {
        "formato": "StudioCutProject",
        "version": "1.0",
        "nombre": p.stem,
        "fecha_guardado": datetime.now().isoformat(),
        "datos": datos,
    }

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            json.dump(paquete, f, indent=2, ensure_ascii=False)
        return p
    except Exception as exc:
        raise ErrorProyecto(f"No se pudo guardar el proyecto en {p}: {exc}") from exc


def cargar_proyecto(ruta: str | Path) -> dict:
    """Carga y valida un archivo .stcut."""
    p = Path(ruta)
    if not p.exists():
        raise ErrorProyecto(f"El archivo de proyecto no existe: {p}")

    try:
        with open(p, "r", encoding="utf-8") as f:
            paquete = json.load(f)
    except Exception as exc:
        raise ErrorProyecto(f"Error al leer el archivo JSON {p}: {exc}") from exc

    if not isinstance(paquete, dict) or paquete.get("formato") != "StudioCutProject":
        # Compatibilidad si cargara directamente el dict de datos
        if isinstance(paquete, dict) and "audios" in paquete:
            return paquete
        raise ErrorProyecto(f"El archivo {p.name} no es un proyecto Studio Cut válido.")

    return paquete.get("datos", {})
