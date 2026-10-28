"""Servidor MCP local de Studio Cut (JSON-RPC por stdio).

Expone las mismas capacidades que el panel «🤖 IA» para que un agente externo
(Claude Desktop, Cursor, etc.) pueda pedir guiones, imágenes y voz.

Seguridad: por defecto solo arranca si Studio Cut le pasa un token de
lanzamiento en la variable de entorno ``STUDIOCUT_LAUNCH_TOKEN``. Así no se
queda escuchando solo por abrirlo con doble clic.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .guion import parsear_guion, validar_guion
from .imagenes import ESTILOS, generar_imagenes
from .proveedores import (
    BASE_OPENROUTER,
    MODELO_IMAGEN_DEFECTO,
    MODELO_TEXTO_DEFECTO,
    ErrorIA,
    listar_modelos_gratis,
)

PROTOCOLO = "2024-11-05"
NOMBRE = "studiocut-ia"
VERSION = "1.0.0"


def _carpeta_salida() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    carpeta = Path(base) / "StudioCut" / "mcp_salidas"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _clave(proveedor: str) -> str:
    variables = {
        "openrouter": ("STUDIOCUT_OPENROUTER_KEY", "OPENROUTER_API_KEY"),
        "elevenlabs": ("STUDIOCUT_ELEVENLABS_KEY", "ELEVENLABS_API_KEY"),
    }
    for nombre in variables.get(proveedor, ()):
        valor = os.environ.get(nombre)
        if valor:
            return valor
    try:
        from . import credenciales

        return credenciales.obtener_clave(proveedor)
    except Exception:  # noqa: BLE001
        return ""


HERRAMIENTAS = [
    {
        "name": "generar_guion",
        "description": (
            "Genera un guion de YouTube en español con la estructura "
            "Introducción / Capítulo uno / Capítulo dos / Cierre, lista para "
            "el detector de capítulos de Studio Cut."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tema": {"type": "string", "description": "Tema o idea del video."},
                "duracion_min": {"type": "integer", "default": 5},
                "n_capitulos": {"type": "integer", "default": 3},
                "tono": {"type": "string", "default": "cercano y directo"},
                "publico": {"type": "string", "default": "público general"},
                "modelo": {"type": "string", "default": MODELO_TEXTO_DEFECTO},
            },
            "required": ["tema"],
        },
    },
    {
        "name": "generar_imagenes",
        "description": (
            "Genera una imagen por cada sección de un guion y devuelve las rutas. "
            "Proveedores: openrouter (Gemini), pollinations (gratis) u openai."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "guion": {"type": "string", "description": "Texto del guion."},
                "tema": {"type": "string"},
                "estilo": {
                    "type": "string",
                    "enum": list(ESTILOS.keys()),
                    "default": "cinematografico",
                },
                "proveedor": {
                    "type": "string",
                    "enum": ["openrouter", "pollinations", "openai"],
                    "default": "pollinations",
                },
                "modelo": {"type": "string"},
                "ancho": {"type": "integer", "default": 1920},
                "alto": {"type": "integer", "default": 1080},
            },
            "required": ["guion"],
        },
    },
    {
        "name": "generar_voz",
        "description": (
            "Convierte un texto en audio. Usa ElevenLabs si se indica y hay "
            "clave; si no, el TTS neuronal de Microsoft."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "texto": {"type": "string"},
                "proveedor": {
                    "type": "string",
                    "enum": ["edge", "elevenlabs"],
                    "default": "edge",
                },
                "voice_id": {"type": "string"},
            },
            "required": ["texto"],
        },
    },
    {
        "name": "listar_modelos_gratis",
        "description": "Lista los modelos gratuitos de OpenRouter con salida de texto.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "estado",
        "description": "Estado de la conexión: proveedores y claves disponibles (sin mostrarlas).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _texto(valor) -> str:
    return str(valor or "")


def _resultado(texto: str, es_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": texto}],
        "isError": bool(es_error),
    }


def _ejecutar_herramienta(nombre: str, argumentos: dict) -> dict:
    argumentos = argumentos or {}
    if nombre == "generar_guion":
        from .guion import generar_guion

        clave = _clave("openrouter")
        if not clave:
            return _resultado("Falta la clave de OpenRouter.", True)
        guion = generar_guion(
            _texto(argumentos.get("tema")),
            clave=clave,
            modelo=_texto(argumentos.get("modelo")) or MODELO_TEXTO_DEFECTO,
            duracion_min=int(argumentos.get("duracion_min") or 5),
            n_capitulos=int(argumentos.get("n_capitulos") or 3),
            tono=_texto(argumentos.get("tono")) or "cercano y directo",
            publico=_texto(argumentos.get("publico")) or "público general",
        )
        problemas = validar_guion(guion)
        return _resultado(json.dumps({
            "texto": guion.texto,
            "palabras": guion.palabras,
            "bloques": [b.a_dict() for b in guion.bloques],
            "problemas": problemas,
        }, ensure_ascii=False, indent=2))

    if nombre == "generar_imagenes":
        guion = parsear_guion(_texto(argumentos.get("guion")))
        if not guion.bloques:
            return _resultado("El guion está vacío o no se pudo interpretar.", True)
        guion.idea = _texto(argumentos.get("tema"))
        rutas = generar_imagenes(
            guion,
            _carpeta_salida(),
            proveedor=_texto(argumentos.get("proveedor")) or "pollinations",
            clave=_clave("openrouter"),
            modelo=_texto(argumentos.get("modelo")) or MODELO_IMAGEN_DEFECTO,
            ancho=int(argumentos.get("ancho") or 1920),
            alto=int(argumentos.get("alto") or 1080),
            estilo=_texto(argumentos.get("estilo")) or "cinematografico",
        )
        return _resultado(json.dumps({"imagenes": rutas}, ensure_ascii=False, indent=2))

    if nombre == "generar_voz":
        from .voz import generar_voz_ia

        texto = _texto(argumentos.get("texto"))
        if not texto.strip():
            return _resultado("El texto está vacío.", True)
        salida = _carpeta_salida() / f"voz_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        ruta = generar_voz_ia(
            texto,
            salida,
            proveedor=_texto(argumentos.get("proveedor")) or "edge",
            clave_elevenlabs=_clave("elevenlabs"),
            voice_id=_texto(argumentos.get("voice_id")),
        )
        return _resultado(json.dumps({"audio": ruta}, ensure_ascii=False))

    if nombre == "listar_modelos_gratis":
        modelos = listar_modelos_gratis(_clave("openrouter"))
        return _resultado(json.dumps(modelos, ensure_ascii=False, indent=2))

    if nombre == "estado":
        return _resultado(json.dumps({
            "servidor": NOMBRE,
            "version": VERSION,
            "openrouter": bool(_clave("openrouter")),
            "elevenlabs": bool(_clave("elevenlabs")),
            "carpeta_salida": str(_carpeta_salida()),
        }, ensure_ascii=False, indent=2))

    return _resultado(f"Herramienta desconocida: {nombre}", True)


def manejar_solicitud(solicitud: dict, *, al_log=None) -> dict | None:
    """Procesa una solicitud JSON-RPC y devuelve la respuesta (o ``None``)."""
    if not isinstance(solicitud, dict):
        return _error(None, -32600, "Solicitud inválida")
    metodo = solicitud.get("method")
    ident = solicitud.get("id")
    params = solicitud.get("params") or {}

    if metodo == "initialize":
        return _ok(ident, {
            "protocolVersion": PROTOCOLO,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": NOMBRE, "version": VERSION},
        })
    if metodo in ("notifications/initialized", "notifications/cancelled"):
        return None
    if metodo == "ping":
        return _ok(ident, {})
    if metodo == "tools/list":
        return _ok(ident, {"tools": HERRAMIENTAS})
    if metodo == "tools/call":
        nombre = _texto(params.get("name"))
        argumentos = params.get("arguments") or {}
        try:
            resultado = _ejecutar_herramienta(nombre, argumentos)
            return _ok(ident, resultado)
        except ErrorIA as exc:
            return _ok(ident, _resultado(str(exc), True))
        except Exception as exc:  # noqa: BLE001
            if al_log:
                al_log(f"[MCP] Error en {nombre}: {exc}")
            return _ok(ident, _resultado(f"Error: {exc}", True))
    return _error(ident, -32601, f"Método no encontrado: {metodo}")


def _ok(ident, resultado) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "result": resultado}


def _error(ident, codigo: int, mensaje: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": ident,
        "error": {"code": codigo, "message": mensaje},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Servidor MCP de Studio Cut")
    parser.add_argument("--stdio", action="store_true", help="Usar stdio (obligatorio).")
    parser.add_argument(
        "--permitir-externo",
        action="store_true",
        help="No exigir el token de lanzamiento de Studio Cut.",
    )
    args = parser.parse_args(argv)

    if not args.stdio:
        print(
            "Este complemento se abre desde Studio Cut (pestaña «🤖 IA»).\n"
            "Si eres un cliente MCP, ejecútalo con --stdio.",
            file=sys.stderr,
        )
        return 2
    if not args.permitir_externo and not os.environ.get("STUDIOCUT_LAUNCH_TOKEN"):
        print(
            "Falta el token de lanzamiento de Studio Cut. Abre el complemento "
            "desde la pestaña «🤖 IA» o usa --permitir-externo.",
            file=sys.stderr,
        )
        return 2

    for linea in sys.stdin:
        linea = linea.strip()
        if not linea:
            continue
        try:
            solicitud = json.loads(linea)
        except json.JSONDecodeError:
            respuesta = _error(None, -32700, "JSON inválido")
        else:
            respuesta = manejar_solicitud(solicitud)
        if respuesta is not None:
            sys.stdout.write(json.dumps(respuesta, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
