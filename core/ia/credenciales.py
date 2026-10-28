"""Guardado cifrado de claves de API.

En Windows se usa **DPAPI** (``CryptProtectData``), que cifra con la cuenta
del usuario actual: el archivo copiado a otro equipo o a otra cuenta no se
puede descifrar. En otros sistemas se guarda en base64 (ofuscado, no cifrado)
para que el resto de la app siga funcionando.

Las claves **nunca** se escriben en ``QSettings``, en el proyecto ``.stcut``,
en los logs ni en el repositorio. El archivo vive en ``ui/.config/``, que está
excluido por ``.gitignore``.
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

_CARPETA = Path(__file__).resolve().parents[2] / "ui" / ".config"
_ARCHIVO = _CARPETA / "ia_credenciales.json"
_ENTROPIA = b"StudioCut-IA-v1"
_VERSION = 1


class ErrorCredenciales(RuntimeError):
    """No se pudo leer, guardar o descifrar una clave."""


# ------------------------------------------------------------------ DPAPI
class _BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob(datos: bytes) -> _BLOB:
    buf = ctypes.create_string_buffer(datos, len(datos))
    return _BLOB(len(datos), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _cifrar(datos: bytes) -> bytes:
    if os.name != "nt":
        return b"b64:" + base64.b64encode(datos)
    entrada = _blob(datos)
    entropia = _blob(_ENTROPIA)
    salida = _BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(entrada),
        ctypes.c_wchar_p("StudioCut"),
        ctypes.byref(entropia),
        None,
        None,
        0,
        ctypes.byref(salida),
    )
    if not ok:
        raise ErrorCredenciales(
            "Windows no pudo cifrar la clave (DPAPI)."
        )
    try:
        return ctypes.string_at(salida.pbData, salida.cbData)
    finally:
        kernel32.LocalFree(salida.pbData)


def _descifrar(datos: bytes) -> bytes:
    if datos.startswith(b"b64:"):
        return base64.b64decode(datos[4:])
    if os.name != "nt":
        raise ErrorCredenciales("Las credenciales cifradas requieren Windows.")
    entrada = _blob(datos)
    entropia = _blob(_ENTROPIA)
    salida = _BLOB()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(entrada),
        None,
        ctypes.byref(entropia),
        None,
        None,
        0,
        ctypes.byref(salida),
    )
    if not ok:
        raise ErrorCredenciales(
            "No se pudo descifrar la clave. Puede ser de otro usuario de Windows."
        )
    try:
        return ctypes.string_at(salida.pbData, salida.cbData)
    finally:
        kernel32.LocalFree(salida.pbData)


# --------------------------------------------------------------- Archivo
def ruta_credenciales() -> Path:
    """Ruta del archivo de credenciales (útil en pruebas y diagnóstico)."""
    return _ARCHIVO


def _leer() -> dict:
    if not _ARCHIVO.exists():
        return {"version": _VERSION, "proveedores": {}}
    try:
        datos = json.loads(_ARCHIVO.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": _VERSION, "proveedores": {}}
    if not isinstance(datos, dict):
        return {"version": _VERSION, "proveedores": {}}
    datos.setdefault("version", _VERSION)
    datos.setdefault("proveedores", {})
    return datos


def _escribir(datos: dict) -> None:
    _CARPETA.mkdir(parents=True, exist_ok=True)
    temporal = _ARCHIVO.with_suffix(".tmp")
    temporal.write_text(
        json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporal, _ARCHIVO)
    try:  # Solo el usuario actual puede leerlo (Windows).
        if os.name == "nt":
            os.chmod(_ARCHIVO, 0o600)
    except OSError:
        pass


# -------------------------------------------------------------------- API
def guardar_clave(proveedor: str, clave: str) -> None:
    """Guarda (cifrada) la clave de un proveedor. Cadena vacía la borra."""
    proveedor = (proveedor or "").strip().lower()
    if not proveedor:
        raise ErrorCredenciales("Falta el nombre del proveedor.")
    datos = _leer()
    if not clave:
        datos["proveedores"].pop(proveedor, None)
        _escribir(datos)
        return
    cifrado = _cifrar(clave.encode("utf-8"))
    datos["proveedores"][proveedor] = {
        "cifrado": base64.b64encode(cifrado).decode("ascii"),
        "pista": _pista(clave),
    }
    _escribir(datos)


def obtener_clave(proveedor: str) -> str:
    """Devuelve la clave en claro o ``""`` si no existe."""
    proveedor = (proveedor or "").strip().lower()
    entrada = _leer().get("proveedores", {}).get(proveedor)
    if not entrada:
        return ""
    try:
        crudo = base64.b64decode(entrada.get("cifrado", ""))
        return _descifrar(crudo).decode("utf-8")
    except Exception:
        return ""


def hay_clave(proveedor: str) -> bool:
    return bool(obtener_clave(proveedor))


def pista_clave(proveedor: str) -> str:
    entrada = _leer().get("proveedores", {}).get(proveedor) or {}
    return str(entrada.get("pista") or "")


def borrar_clave(proveedor: str) -> None:
    guardar_clave(proveedor, "")


def listar_proveedores() -> list[str]:
    return sorted(_leer().get("proveedores", {}).keys())


def _pista(clave: str) -> str:
    clave = (clave or "").strip()
    if len(clave) <= 10:
        return "•" * len(clave)
    return f"{clave[:6]}…{clave[-4:]}"
