"""Servidor MCP del complemento StudioCut AI (JSON-RPC por stdio)."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import __version__
from .cliente import (
    MODELO_IMAGEN,
    MODELO_TEXTO,
    ErrorCliente,
    generar_imagen,
    generar_texto,
    generar_voz,
    listar_modelos_gratis,
)

PROTOCOLO = "2024-11-05"
NOMBRE = "studiocut-ai"


def _carpeta() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    carpeta = Path(base) / "StudioCut" / "mcp_salidas"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def _clave_openrouter() -> str:
    return (
        os.environ.get("STUDIOCUT_OPENROUTER_KEY")
        or os.environ.get("OPENROUTER_API_KEY")
        or ""
    )


def _clave_eleven() -> str:
    return (
        os.environ.get("STUDIOCUT_ELEVENLABS_KEY")
        or os.environ.get("ELEVENLABS_API_KEY")
        or ""
    )


HERRAMIENTAS = [
    {
        "name": "generar_guion",
        "description": (
            "Genera un guion de YouTube en español con Introducción, capítulos "
            "numerados con letra y Cierre. Devuelve solo el texto del guion."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "tema": {"type": "string"},
                "duracion_min": {"type": "integer", "default": 5},
                "n_capitulos": {"type": "integer", "default": 3},
                "tono": {"type": "string", "default": "cercano y directo"},
                "publico": {"type": "string", "default": "público general"},
                "modelo": {"type": "string", "default": MODELO_TEXTO},
            },
            "required": ["tema"],
        },
    },
    {
        "name": "generar_imagen",
        "description": (
            "Genera una imagen a partir de una descripción y devuelve su ruta. "
            "Usa pollinations (gratis) por defecto."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string"},
                "proveedor": {
                    "type": "string",
                    "enum": ["pollinations", "openrouter", "openai"],
                    "default": "pollinations",
                },
                "modelo": {"type": "string"},
                "ancho": {"type": "integer", "default": 1920},
                "alto": {"type": "integer", "default": 1080},
                "nombre": {"type": "string"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "generar_voz",
        "description": "Convierte texto en audio y devuelve la ruta del archivo.",
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
        "description": "Modelos gratuitos de OpenRouter con salida de texto.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "estado",
        "description": "Estado del complemento y de las claves disponibles.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def _ok(ident, resultado) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "result": resultado}


def _error(ident, codigo: int, mensaje: str) -> dict:
    return {"jsonrpc": "2.0", "id": ident, "error": {"code": codigo, "message": mensaje}}


def _texto(texto: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": texto}], "isError": error}


def _prompt_guion(tema: str, duracion: int, capitulos: int, tono: str, publico: str) -> str:
    numeros = ["uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho",
               "nueve", "diez"]
    estructura = "\n".join(
        f"Capítulo {numeros[i]}: Título breve.\nCuatro a ocho frases con un ejemplo.\n"
        for i in range(min(max(1, capitulos), 10))
    )
    return (
        "Escribe un guion de YouTube en español neutro para leer en voz alta. "
        f"Tema: {tema}. Duración aproximada: {duracion} minutos "
        f"(unas {duracion * 150} palabras). Tono: {tono}. Público: {publico}.\n\n"
        "Formato exacto, sin markdown ni notas:\n"
        "Introducción.\nDos o tres frases que presenten el tema.\n\n"
        f"{estructura}\n"
        "Cierre.\nDos o tres frases de despedida y llamada a la acción.\n\n"
        "Escribe los números de los capítulos con letra. No escribas nada antes "
        "de «Introducción.» ni después del cierre."
    )


def ejecutar(nombre: str, args: dict) -> dict:
    args = args or {}
    if nombre == "generar_guion":
        texto = generar_texto(
            [
                {"role": "system", "content": "Eres guionista profesional de YouTube en español."},
                {"role": "user", "content": _prompt_guion(
                    str(args.get("tema") or ""),
                    int(args.get("duracion_min") or 5),
                    int(args.get("n_capitulos") or 3),
                    str(args.get("tono") or "cercano y directo"),
                    str(args.get("publico") or "público general"),
                )},
            ],
            clave=_clave_openrouter(),
            modelo=str(args.get("modelo") or MODELO_TEXTO),
        )
        return _texto(texto)

    if nombre == "generar_imagen":
        datos = generar_imagen(
            str(args.get("prompt") or ""),
            proveedor=str(args.get("proveedor") or "pollinations"),
            clave=_clave_openrouter(),
            modelo=str(args.get("modelo") or MODELO_IMAGEN),
            ancho=int(args.get("ancho") or 1920),
            alto=int(args.get("alto") or 1080),
        )
        extension = ".png" if datos.startswith(b"\x89PNG") else ".jpg"
        nombre_archivo = "".join(
            c for c in str(args.get("nombre") or f"imagen_{time.strftime('%Y%m%d_%H%M%S')}")
            if c.isalnum() or c in "._-"
        ) or "imagen"
        ruta = _carpeta() / (nombre_archivo + extension)
        ruta.write_bytes(datos)
        return _texto(json.dumps({"imagen": str(ruta)}, ensure_ascii=False))

    if nombre == "generar_voz":
        texto = str(args.get("texto") or "")
        if not texto.strip():
            return _texto("El texto está vacío.", True)
        salida = _carpeta() / f"voz_{time.strftime('%Y%m%d_%H%M%S')}.wav"
        ruta = generar_voz(
            texto,
            salida,
            proveedor=str(args.get("proveedor") or "edge"),
            clave_eleven=_clave_eleven(),
            voice_id=str(args.get("voice_id") or ""),
        )
        return _texto(json.dumps({"audio": ruta}, ensure_ascii=False))

    if nombre == "listar_modelos_gratis":
        return _texto(json.dumps(
            listar_modelos_gratis(_clave_openrouter()), ensure_ascii=False, indent=2
        ))

    if nombre == "estado":
        return _texto(json.dumps({
            "servidor": NOMBRE,
            "version": __version__,
            "openrouter": bool(_clave_openrouter()),
            "elevenlabs": bool(_clave_eleven()),
            "carpeta_salida": str(_carpeta()),
        }, ensure_ascii=False, indent=2))

    return _texto(f"Herramienta desconocida: {nombre}", True)


def manejar(solicitud: dict) -> dict | None:
    if not isinstance(solicitud, dict):
        return _error(None, -32600, "Solicitud inválida")
    metodo = solicitud.get("method")
    ident = solicitud.get("id")
    params = solicitud.get("params") or {}

    if metodo == "initialize":
        return _ok(ident, {
            "protocolVersion": PROTOCOLO,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": NOMBRE, "version": __version__},
        })
    if metodo in ("notifications/initialized", "notifications/cancelled"):
        return None
    if metodo == "ping":
        return _ok(ident, {})
    if metodo == "tools/list":
        return _ok(ident, {"tools": HERRAMIENTAS})
    if metodo == "tools/call":
        try:
            return _ok(ident, ejecutar(str(params.get("name") or ""), params.get("arguments") or {}))
        except ErrorCliente as exc:
            return _ok(ident, _texto(str(exc), True))
        except Exception as exc:  # noqa: BLE001
            return _ok(ident, _texto(f"Error: {exc}", True))
    return _error(ident, -32601, f"Método no encontrado: {metodo}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StudioCut AI (MCP)")
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument("--permitir-externo", action="store_true")
    args = parser.parse_args(argv)

    if not args.stdio:
        print(
            "Abre este complemento desde Studio Cut → pestaña «🤖 IA».\n"
            "Para usarlo como servidor MCP: python -m studiocut_ai --stdio",
            file=sys.stderr,
        )
        return 2
    if not args.permitir_externo and not os.environ.get("STUDIOCUT_LAUNCH_TOKEN"):
        print(
            "Falta el token de lanzamiento de Studio Cut. "
            "Ábrelo desde la app o usa --permitir-externo.",
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
            respuesta = manejar(solicitud)
        if respuesta is not None:
            sys.stdout.write(json.dumps(respuesta, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0
