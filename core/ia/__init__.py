"""Conexión con proveedores de IA para Studio Cut.

El paquete agrupa:

* :mod:`credenciales` — guardado cifrado de claves (DPAPI de Windows).
* :mod:`proveedores`  — clientes HTTP (OpenRouter, OpenAI-compatible, Pollinations).
* :mod:`guion`        — generación, limpieza y validación de guiones.
* :mod:`imagenes`     — generación de imágenes por capítulo.
* :mod:`voz`          — ElevenLabs opcional con vuelta al TTS de siempre.
* :mod:`pipeline`     — flujo «casi automático»: guion → imágenes → voz.
* :mod:`mcp_server`   — servidor MCP local por stdio.
"""
from __future__ import annotations

from .guion import Bloque, Guion, generar_guion, marcas_estimadas, parsear_guion
from .pipeline import ConfigIA, ResultadoIA, ejecutar_pipeline
from .proveedores import ErrorIA

__all__ = [
    "Bloque",
    "ConfigIA",
    "ErrorIA",
    "Guion",
    "ResultadoIA",
    "ejecutar_pipeline",
    "generar_guion",
    "marcas_estimadas",
    "parsear_guion",
]
