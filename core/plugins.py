"""Gestor del complemento MCP de Studio Cut.

El complemento se publica en un repositorio aparte (por defecto
``Jomacogo/StudioCut-AI``). Studio Cut lee un ``plugin.json``, muestra
versión/tamaño/hash, descarga el ZIP, **verifica el SHA-256** y lo instala en
``%LOCALAPPDATA%\\StudioCut\\plugins`` sin pedir permisos de administrador.

Nada se instala en silencio: siempre hay que confirmar desde la interfaz.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

REPO_COMPLEMENTO = "Jomacogo/StudioCut-AI"
URL_MANIFIESTO = (
    f"https://raw.githubusercontent.com/{REPO_COMPLEMENTO}/main/plugin.json"
)
NOMBRE_COMPLEMENTO = "studiocut-ai"
VERSION_APP = "1.0.0"

_MAX_ZIP = 200 * 1024 * 1024          # 200 MB comprimido
_MAX_DESCOMPRIMIDO = 500 * 1024 * 1024  # 500 MB en total
_MAX_ARCHIVOS = 3000


class ErrorComplemento(RuntimeError):
    pass


# ------------------------------------------------------------------ Rutas
def ruta_base() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".studiocut")
    return Path(base) / "StudioCut" / "plugins"


def ruta_complemento() -> Path:
    return ruta_base() / NOMBRE_COMPLEMENTO


def ruta_instalado() -> Path:
    return ruta_complemento() / "installed.json"


def ruta_version(version: str) -> Path:
    return ruta_complemento() / _version_segura(version)


def _version_segura(version: str) -> str:
    limpia = "".join(c for c in str(version or "") if c.isalnum() or c in "._-")
    return limpia or "0.0.0"


# --------------------------------------------------------------- Manifiesto
def consultar_manifest(timeout: float = 30.0) -> dict:
    """Descarga y valida el manifiesto publicado."""
    req = urllib.request.Request(
        URL_MANIFIESTO,
        headers={"User-Agent": "StudioCut/1.0", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ErrorComplemento(
                f"El complemento aún no está publicado en {REPO_COMPLEMENTO}. "
                "Crea el repositorio y sube plugin.json + el ZIP de la versión."
            ) from exc
        raise ErrorComplemento(f"GitHub respondió HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ErrorComplemento(f"No se pudo consultar GitHub: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise ErrorComplemento("El plugin.json publicado no es JSON válido.") from exc
    return validar_manifest(datos)


def validar_manifest(datos: dict) -> dict:
    if not isinstance(datos, dict):
        raise ErrorComplemento("El manifiesto debe ser un objeto JSON.")
    for campo in ("version", "url", "sha256"):
        if not str(datos.get(campo) or "").strip():
            raise ErrorComplemento(f"El manifiesto no incluye «{campo}».")
    sha = str(datos["sha256"]).strip().lower()
    if len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        raise ErrorComplemento("El SHA-256 del manifiesto no es válido.")
    if not str(datos["url"]).startswith("https://"):
        raise ErrorComplemento("La URL del complemento debe usar HTTPS.")
    minimo = str(datos.get("min_app") or "0")
    if not _version_ok(VERSION_APP, minimo):
        raise ErrorComplemento(
            f"El complemento pide Studio Cut {minimo} o superior "
            f"(tienes {VERSION_APP}). Actualiza la app."
        )
    datos["version"] = _version_segura(datos["version"])
    return datos


def _version_ok(actual: str, minimo: str) -> bool:
    def partes(v: str) -> tuple[int, ...]:
        numeros = []
        for trozo in str(v).split("."):
            digitos = "".join(c for c in trozo if c.isdigit())
            numeros.append(int(digitos or 0))
        return tuple(numeros[:4])
    return partes(actual) >= partes(minimo)


# --------------------------------------------------------------- Descarga
def descargar(url: str, timeout: float = 120.0) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "StudioCut/1.0", "Accept-Encoding": "identity"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            longitud = int(resp.headers.get("Content-Length") or 0)
            if longitud > _MAX_ZIP:
                raise ErrorComplemento("El ZIP del complemento es demasiado grande.")
            datos = resp.read(_MAX_ZIP + 1)
    except urllib.error.HTTPError as exc:
        raise ErrorComplemento(f"No se pudo descargar el complemento (HTTP {exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise ErrorComplemento(f"No se pudo descargar el complemento: {exc.reason}") from exc
    if len(datos) > _MAX_ZIP:
        raise ErrorComplemento("El ZIP del complemento supera el tamaño máximo.")
    return datos


def sha256(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def verificar_hash(datos: bytes, esperado: str) -> None:
    real = sha256(datos)
    if real != str(esperado or "").strip().lower():
        raise ErrorComplemento(
            "El SHA-256 no coincide. El archivo está corrupto o fue modificado.\n"
            f"Esperado: {esperado}\nObtenido: {real}"
        )


# -------------------------------------------------------------- Instalación
def instalar_desde_zip(
    datos: bytes,
    *,
    version: str,
    sha256_esperado: str = "",
) -> Path:
    """Verifica e instala el ZIP. Devuelve la carpeta de la versión."""
    if sha256_esperado:
        verificar_hash(datos, sha256_esperado)
    version = _version_segura(version)
    destino = ruta_version(version)
    if destino.exists():
        shutil.rmtree(destino, ignore_errors=True)
    destino.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="studiocut_plugin_") as tmp:
        zip_ruta = Path(tmp) / "plugin.zip"
        zip_ruta.write_bytes(datos)
        try:
            with zipfile.ZipFile(zip_ruta) as z:
                _extraer_seguro(z, destino)
        except zipfile.BadZipFile as exc:
            shutil.rmtree(destino, ignore_errors=True)
            raise ErrorComplemento("El archivo descargado no es un ZIP válido.") from exc
        except Exception:
            shutil.rmtree(destino, ignore_errors=True)
            raise

    if not (destino / "studiocut_ai" / "__main__.py").exists() and not any(
        destino.rglob("__main__.py")
    ):
        shutil.rmtree(destino, ignore_errors=True)
        raise ErrorComplemento(
            "El ZIP no contiene el paquete «studiocut_ai». Revisa el empaquetado."
        )

    _guardar_instalado({
        "version": version,
        "ruta": str(destino),
        "sha256": sha256(datos),
        "fecha": _ahora(),
    })
    _limpiar_versiones_anteriores(version)
    return destino


def _extraer_seguro(z: zipfile.ZipFile, destino: Path) -> None:
    infos = z.infolist()
    if len(infos) > _MAX_ARCHIVOS:
        raise ErrorComplemento("El ZIP contiene demasiados archivos.")
    total = sum(max(0, info.file_size) for info in infos)
    if total > _MAX_DESCOMPRIMIDO:
        raise ErrorComplemento("El contenido descomprimido es demasiado grande.")
    base = destino.resolve()
    for info in infos:
        nombre = info.filename.replace("\\", "/")
        if nombre.startswith("/") or ".." in Path(nombre).parts:
            raise ErrorComplemento(f"Ruta insegura dentro del ZIP: {nombre}")
        if (info.external_attr >> 16) & 0o170000 == 0o120000:
            raise ErrorComplemento(f"El ZIP contiene un enlace simbólico: {nombre}")
        objetivo = (base / nombre).resolve()
        if base != objetivo and base not in objetivo.parents:
            raise ErrorComplemento(f"Ruta fuera de la carpeta de instalación: {nombre}")
    z.extractall(base)


def instalar_desde_carpeta(origen: str | Path) -> Path:
    """Instala desde una carpeta local (útil para probar antes de publicar)."""
    origen = Path(origen)
    if not origen.is_dir():
        raise ErrorComplemento(f"No existe la carpeta: {origen}")
    paquete = origen / "studiocut_ai"
    if not (paquete / "__main__.py").exists():
        paquete = next(
            (p for p in origen.rglob("studiocut_ai") if (p / "__main__.py").exists()),
            None,
        )
    if paquete is None:
        raise ErrorComplemento("La carpeta no contiene el paquete «studiocut_ai».")

    version = _leer_version_local(origen)
    destino = ruta_version(version)
    if destino.exists():
        shutil.rmtree(destino, ignore_errors=True)
    shutil.copytree(
        origen,
        destino,
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".git", "dist", ".pytest_cache"
        ),
    )
    _guardar_instalado({
        "version": version,
        "ruta": str(destino),
        "sha256": "",
        "fecha": _ahora(),
        "origen": "local",
    })
    _limpiar_versiones_anteriores(version)
    return destino


def _leer_version_local(carpeta: Path) -> str:
    manifiesto = carpeta / "plugin.json"
    if manifiesto.exists():
        try:
            return _version_segura(json.loads(manifiesto.read_text(encoding="utf-8"))["version"])
        except Exception:  # noqa: BLE001
            pass
    return "local"


def instalar_desde_github() -> Path:
    manifiesto = consultar_manifest()
    datos = descargar(str(manifiesto["url"]))
    return instalar_desde_zip(
        datos, version=str(manifiesto["version"]),
        sha256_esperado=str(manifiesto["sha256"]),
    )


# ------------------------------------------------------------------ Estado
def _leer_instalado() -> dict:
    if not ruta_instalado().exists():
        return {}
    try:
        return json.loads(ruta_instalado().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _guardar_instalado(datos: dict) -> None:
    ruta_complemento().mkdir(parents=True, exist_ok=True)
    ruta_instalado().write_text(
        json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def estado() -> dict:
    datos = _leer_instalado()
    ruta = Path(str(datos.get("ruta") or ""))
    instalado = bool(datos) and ruta.exists()
    return {
        "instalado": instalado,
        "version": str(datos.get("version") or "") if instalado else "",
        "ruta": str(ruta) if instalado else "",
        "sha256": str(datos.get("sha256") or ""),
        "fecha": str(datos.get("fecha") or ""),
    }


def desinstalar() -> None:
    shutil.rmtree(ruta_complemento(), ignore_errors=True)


def _limpiar_versiones_anteriores(version_actual: str) -> None:
    for carpeta in ruta_complemento().iterdir():
        if not carpeta.is_dir():
            continue
        if carpeta.name == version_actual or carpeta.name == _version_segura(version_actual):
            continue
        shutil.rmtree(carpeta, ignore_errors=True)


def _ahora() -> str:
    import datetime

    return datetime.datetime.now().isoformat(timespec="seconds")


# ------------------------------------------------------------- Lanzamiento
def comando_lanzamiento() -> tuple[list[str], str]:
    """Devuelve (comando, carpeta de trabajo) del complemento instalado."""
    datos = estado()
    if not datos["instalado"]:
        raise ErrorComplemento("El complemento no está instalado.")
    ruta = Path(datos["ruta"])
    paquete = ruta / "studiocut_ai"
    if not (paquete / "__main__.py").exists():
        candidatos = list(ruta.rglob("__main__.py"))
        if not candidatos:
            raise ErrorComplemento("La instalación no tiene __main__.py.")
        paquete = candidatos[0].parent
    return [sys.executable, "-m", "studiocut_ai", "--stdio"], str(paquete.parent)


def configuracion_mcp(
    *, ruta_python: str | None = None, incluir_claves: bool = False,
) -> dict:
    """Bloque JSON listo para pegar en un cliente MCP."""
    if estado()["instalado"]:
        comando, cwd = comando_lanzamiento()
        entorno = {"STUDIOCUT_LAUNCH_TOKEN": _token_lanzamiento()}
        if incluir_claves:
            entorno.update(_entorno_claves())
        return {
            "mcpServers": {
                NOMBRE_COMPLEMENTO: {
                    "command": comando[0],
                    "args": comando[1:],
                    "cwd": cwd,
                    "env": entorno,
                }
            }
        }
    # Sin complemento instalado: servidor interno de Studio Cut.
    raiz = Path(__file__).resolve().parent.parent
    entorno = {
        "STUDIOCUT_LAUNCH_TOKEN": _token_lanzamiento(),
        "PYTHONPATH": str(raiz),
    }
    if incluir_claves:
        entorno.update(_entorno_claves())
    return {
        "mcpServers": {
            NOMBRE_COMPLEMENTO: {
                "command": ruta_python or sys.executable,
                "args": ["-m", "core.ia.mcp_server", "--stdio"],
                "cwd": str(raiz),
                "env": entorno,
            }
        }
    }


def lanzar_mcp(prueba: bool = False) -> subprocess.Popen:
    """Arranca el servidor MCP con el token y las claves en el entorno."""
    comando, cwd = comando_lanzamiento()
    entorno = dict(os.environ)
    entorno["STUDIOCUT_LAUNCH_TOKEN"] = _token_lanzamiento()
    entorno.update(_entorno_claves())
    proceso = subprocess.Popen(
        comando,
        cwd=cwd,
        env=entorno,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    if prueba:
        try:
            salida, error = proceso.communicate(
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n",
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            proceso.kill()
            raise ErrorComplemento("El complemento no respondió (timeout).")
        if '"result"' not in (salida or ""):
            raise ErrorComplemento(
                "El complemento no respondió correctamente.\n" + (error or "")[:400]
            )
    return proceso


def _token_lanzamiento() -> str:
    import secrets

    return secrets.token_urlsafe(24)


def _entorno_claves() -> dict:
    entorno = {}
    try:
        from core.ia import credenciales

        openrouter = credenciales.obtener_clave("openrouter")
        eleven = credenciales.obtener_clave("elevenlabs")
        if openrouter:
            entorno["STUDIOCUT_OPENROUTER_KEY"] = openrouter
        if eleven:
            entorno["STUDIOCUT_ELEVENLABS_KEY"] = eleven
    except Exception:  # noqa: BLE001
        pass
    return entorno
