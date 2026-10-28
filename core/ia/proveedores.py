"""Clientes HTTP para los proveedores de IA.

Solo usa la biblioteca estándar (``urllib``) para no añadir dependencias al
instalador de Studio Cut.

Proveedores soportados:

* **OpenRouter** (texto e imágenes). Es el que usa Studio Cut por defecto con
  ``nvidia/nemotron-3-ultra-550b-a55b:free``.
* **OpenAI-compatible** para texto (OpenAI, Groq, DeepSeek, Ollama local…) y
  para imágenes (``/images/generations``).
* **Pollinations** para imágenes gratis y sin clave (respaldo de pruebas).
"""
from __future__ import annotations

import base64
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_OPENROUTER = "https://openrouter.ai/api/v1"
BASE_OPENAI = "https://api.openai.com/v1"

MODELO_TEXTO_DEFECTO = "nvidia/nemotron-3-ultra-550b-a55b:free"
MODELOS_TEXTO_RESPALDO = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "google/gemma-4-31b-it:free",
    "dots-studio/dots-3-note-preview:free",
    "openrouter/free",
]

MODELO_IMAGEN_DEFECTO = "google/gemini-2.5-flash-image"
MODELO_IMAGEN_GRATIS = "flux"

_USER_AGENT = "StudioCut/1.0"
_REFERER = "https://studiocut.local"


class ErrorIA(RuntimeError):
    """Error de un proveedor de IA, con un mensaje entendible para el usuario."""


# ------------------------------------------------------------------ HTTP
def _cabeceras(clave: str, extra: dict | None = None) -> dict:
    cab = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "User-Agent": _USER_AGENT,
        "HTTP-Referer": _REFERER,
        "X-Title": "StudioCut",
    }
    if clave:
        cab["Authorization"] = f"Bearer {clave}"
    if extra:
        cab.update(extra)
    return cab


def peticion_json(
    url: str,
    *,
    metodo: str = "GET",
    payload: dict | None = None,
    clave: str = "",
    timeout: float = 120.0,
    cabeceras: dict | None = None,
) -> dict:
    """Hace una petición JSON y devuelve el objeto decodificado."""
    cuerpo = None
    if payload is not None:
        cuerpo = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=cuerpo,
        headers=_cabeceras(clave, cabeceras),
        method=metodo,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            crudo = resp.read()
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")
        raise ErrorIA(_mensaje_http(exc.code, detalle)) from exc
    except urllib.error.URLError as exc:
        raise ErrorIA(
            f"No se pudo conectar con el proveedor ({exc.reason}). "
            "Revisa tu conexión a internet."
        ) from exc
    except TimeoutError as exc:
        raise ErrorIA("El proveedor tardó demasiado en responder.") from exc
    try:
        datos = json.loads(crudo.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ErrorIA("El proveedor devolvió una respuesta que no es JSON.") from exc
    if isinstance(datos, dict) and datos.get("error"):
        err = datos["error"]
        mensaje = err.get("message") if isinstance(err, dict) else str(err)
        raise ErrorIA(f"El proveedor devolvió un error: {mensaje}")
    return datos


def _mensaje_http(codigo: int, detalle: str) -> str:
    try:
        datos = json.loads(detalle)
        err = datos.get("error") if isinstance(datos, dict) else None
        if isinstance(err, dict) and err.get("message"):
            detalle = str(err["message"])
    except json.JSONDecodeError:
        pass
    detalle = (detalle or "").strip().replace("\n", " ")[:400]
    if codigo == 401:
        return "Clave de API inválida o caducada (HTTP 401)."
    if codigo == 402:
        return (
            "El proveedor pide crédito para esta operación (HTTP 402). "
            "Prueba un modelo gratis o cambia de proveedor de imágenes."
        )
    if codigo == 403:
        return f"El proveedor no permite esta operación (HTTP 403). {detalle}"
    if codigo == 404:
        return f"Modelo o ruta no encontrada (HTTP 404). {detalle}"
    if codigo == 429:
        return f"Límite de peticiones alcanzado (HTTP 429). {detalle}"
    return f"Error HTTP {codigo}: {detalle}"


def _descargar(url: str, timeout: float = 180.0) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")[:300]
        raise ErrorIA(f"No se pudo descargar la imagen (HTTP {exc.code}). {detalle}") from exc
    except urllib.error.URLError as exc:
        raise ErrorIA(f"No se pudo descargar la imagen: {exc.reason}") from exc


# ------------------------------------------------------------------ Texto
def llamar_chat(
    mensajes: list[dict],
    *,
    clave: str,
    modelo: str = MODELO_TEXTO_DEFECTO,
    base_url: str = BASE_OPENROUTER,
    temperatura: float = 0.75,
    max_tokens: int = 4096,
    razonamiento: bool = False,
    timeout: float = 180.0,
) -> dict:
    """Una llamada de chat. Devuelve ``{texto, razonamiento, uso, modelo}``."""
    payload: dict = {
        "model": modelo,
        "messages": mensajes,
        "temperature": temperatura,
        "max_tokens": max_tokens,
    }
    if razonamiento:
        payload["reasoning"] = {"enabled": True}
    url = base_url.rstrip("/") + "/chat/completions"
    datos = peticion_json(url, metodo="POST", payload=payload, clave=clave, timeout=timeout)
    elecciones = datos.get("choices") or []
    if not elecciones:
        raise ErrorIA("El proveedor no devolvió ninguna respuesta.")
    mensaje = elecciones[0].get("message") or {}
    texto = _texto_de_contenido(mensaje.get("content"))
    razon = mensaje.get("reasoning") or mensaje.get("reasoning_content") or ""
    return {
        "texto": texto.strip(),
        "razonamiento": str(razon or "").strip(),
        "uso": datos.get("usage") or {},
        "modelo": datos.get("model") or modelo,
        "fin": elecciones[0].get("finish_reason"),
    }


def _texto_de_contenido(contenido) -> str:
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        partes = []
        for parte in contenido:
            if isinstance(parte, dict) and parte.get("type") == "text":
                partes.append(str(parte.get("text") or ""))
            elif isinstance(parte, str):
                partes.append(parte)
        return "\n".join(p for p in partes if p)
    return ""


def _es_local(base_url: str) -> bool:
    """True si la URL apunta a este equipo (Ollama, LM Studio…)."""
    url = (base_url or "").lower()
    return any(
        marca in url
        for marca in ("localhost", "127.0.0.1", "0.0.0.0", "::1")
    )


def generar_texto(
    mensajes: list[dict],
    *,
    clave: str,
    modelo: str = "",
    modelos_respaldo: list[str] | None = None,
    base_url: str = BASE_OPENROUTER,
    temperatura: float = 0.75,
    max_tokens: int = 4096,
    timeout: float = 180.0,
    al_log=None,
) -> dict:
    """Prueba el modelo pedido y, si falla, una lista de respaldos gratis.

    Reintenta ante límite de peticiones (429) y respuestas vacías, que es lo
    habitual con los modelos gratuitos de OpenRouter.
    """
    if not clave and not _es_local(base_url):
        raise ErrorIA(
            "Falta la clave de API. Pégala en la pestaña «🤖 IA» y pulsa Guardar."
        )
    lista: list[str] = []
    respaldos = modelos_respaldo
    if respaldos is None:
        respaldos = MODELOS_TEXTO_RESPALDO if "openrouter" in base_url else []
    for candidato in [modelo, *respaldos]:
        candidato = (candidato or "").strip()
        if candidato and candidato not in lista:
            lista.append(candidato)
    if not lista:
        lista = [MODELO_TEXTO_DEFECTO]

    errores: list[str] = []
    for i, nombre in enumerate(lista):
        if al_log:
            al_log(f"[IA] Probando modelo: {nombre}")
        try:
            resultado = llamar_chat(
                mensajes,
                clave=clave,
                modelo=nombre,
                base_url=base_url,
                temperatura=temperatura,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if resultado["texto"].strip():
                if al_log:
                    al_log(f"[IA] Respondió {resultado['modelo']}.")
                return resultado
            errores.append(f"{nombre}: respuesta vacía")
        except ErrorIA as exc:
            mensaje = str(exc)
            errores.append(f"{nombre}: {mensaje}")
            if "401" in mensaje or "Clave de API inválida" in mensaje:
                raise
            if i + 1 < len(lista):
                if "429" in mensaje:
                    time.sleep(min(4.0, 1.0 + i))
                if al_log:
                    al_log(f"[IA] {nombre} no respondió; probando el siguiente.")
        except Exception as exc:  # noqa: BLE001
            errores.append(f"{nombre}: {exc}")
    raise ErrorIA(
        "Ningún modelo de texto respondió correctamente.\n" + "\n".join(errores[-6:])
    )


def listar_modelos_gratis(clave: str = "", timeout: float = 60.0) -> list[dict]:
    """Modelos con precio 0 y salida de texto, ordenados por contexto."""
    datos = peticion_json(
        BASE_OPENROUTER + "/models", clave=clave, timeout=timeout
    )
    libres: list[dict] = []
    for modelo in datos.get("data") or []:
        precio = modelo.get("pricing") or {}
        if not _es_cero(precio.get("prompt")) or not _es_cero(precio.get("completion")):
            continue
        arquitectura = modelo.get("architecture") or {}
        salidas = arquitectura.get("output_modalities") or ["text"]
        if "text" not in salidas:
            continue
        libres.append({
            "id": str(modelo.get("id") or ""),
            "nombre": str(modelo.get("name") or modelo.get("id") or ""),
            "contexto": int(modelo.get("context_length") or 0),
        })
    libres.sort(key=lambda m: (-m["contexto"], m["id"]))
    return [m for m in libres if m["id"]]


def _es_cero(valor) -> bool:
    try:
        return float(valor) == 0.0
    except (TypeError, ValueError):
        return False


def verificar_clave(
    clave: str,
    base_url: str = BASE_OPENROUTER,
    timeout: float = 30.0,
    modelo: str = "",
) -> str:
    """Comprueba la clave con una petición mínima. Devuelve un mensaje de éxito."""
    if not clave and not _es_local(base_url):
        raise ErrorIA("Escribe una clave antes de probar.")
    if "openrouter" in base_url:
        peticion_json(BASE_OPENROUTER + "/key", clave=clave, timeout=timeout)
        return "Clave válida. OpenRouter conectado."
    resultado = llamar_chat(
        [{"role": "user", "content": "Responde solo: OK"}],
        clave=clave,
        modelo=modelo or "gpt-4o-mini",
        base_url=base_url,
        max_tokens=5,
        timeout=timeout,
    )
    return f"Clave válida. El proveedor respondió: {resultado['texto'][:40] or 'OK'}"


# --------------------------------------------------------------- Imágenes
def generar_imagen_bytes(
    prompt: str,
    *,
    proveedor: str = "openrouter",
    clave: str = "",
    modelo: str = "",
    ancho: int = 1920,
    alto: int = 1080,
    base_url: str = BASE_OPENAI,
    timeout: float = 240.0,
    semilla: int | None = None,
) -> bytes:
    """Genera una imagen y devuelve sus bytes (PNG/JPEG/WebP)."""
    proveedor = (proveedor or "openrouter").strip().lower()
    if proveedor in ("ninguno", "none", ""):
        raise ErrorIA("La generación de imágenes está desactivada.")
    if proveedor == "pollinations":
        return _imagen_pollinations(prompt, ancho, alto, timeout, semilla)
    if proveedor == "openrouter":
        if not clave:
            raise ErrorIA("OpenRouter necesita una clave para generar imágenes.")
        payload = {
            "model": modelo or MODELO_IMAGEN_DEFECTO,
            "messages": [{"role": "user", "content": prompt}],
            "modalities": ["image", "text"],
        }
        datos = peticion_json(
            BASE_OPENROUTER + "/chat/completions",
            metodo="POST",
            payload=payload,
            clave=clave,
            timeout=timeout,
        )
        return _extraer_imagen(datos)
    if proveedor in ("openai", "compatible"):
        if not clave:
            raise ErrorIA("El proveedor de imágenes necesita una clave.")
        tamano = _tamano_openai(ancho, alto)
        payload = {
            "model": modelo or "gpt-image-1",
            "prompt": prompt,
            "size": tamano,
            "n": 1,
        }
        datos = peticion_json(
            base_url.rstrip("/") + "/images/generations",
            metodo="POST",
            payload=payload,
            clave=clave,
            timeout=timeout,
        )
        return _imagen_de_respuesta_openai(datos)
    raise ErrorIA(f"Proveedor de imágenes desconocido: {proveedor}")


def _imagen_pollinations(
    prompt: str, ancho: int, alto: int, timeout: float, semilla: int | None
) -> bytes:
    semilla = semilla if semilla is not None else random.randint(1, 999_999)
    consulta = urllib.parse.urlencode({
        "width": ancho,
        "height": alto,
        "nologo": "true",
        "model": MODELO_IMAGEN_GRATIS,
        "seed": semilla,
    })
    url = (
        "https://image.pollinations.ai/prompt/"
        + urllib.parse.quote(prompt[:1500], safe="")
        + "?"
        + consulta
    )
    datos = _descargar(url, timeout=timeout)
    if len(datos) < 1024:
        raise ErrorIA("Pollinations devolvió una imagen vacía. Inténtalo de nuevo.")
    return datos


def _tamano_openai(ancho: int, alto: int) -> str:
    if alto > ancho:
        return "1024x1536"
    if ancho > alto:
        return "1536x1024"
    return "1024x1024"


def _imagen_de_respuesta_openai(datos: dict) -> bytes:
    items = datos.get("data") or []
    if not items:
        raise ErrorIA("El proveedor no devolvió ninguna imagen.")
    item = items[0] or {}
    if item.get("b64_json"):
        return base64.b64decode(item["b64_json"])
    if item.get("url"):
        return _descargar(str(item["url"]))
    raise ErrorIA("La respuesta de imagen no trae datos ni URL.")


def _extraer_imagen(datos: dict) -> bytes:
    """Saca la imagen de una respuesta de chat con ``modalities``."""
    elecciones = datos.get("choices") or []
    if not elecciones:
        raise ErrorIA("El proveedor no devolvió ninguna imagen.")
    mensaje = elecciones[0].get("message") or {}

    for imagen in mensaje.get("images") or []:
        url = ""
        if isinstance(imagen, dict):
            url = (
                (imagen.get("image_url") or {}).get("url")
                if isinstance(imagen.get("image_url"), dict)
                else imagen.get("url") or imagen.get("image_url") or ""
            )
        elif isinstance(imagen, str):
            url = imagen
        if url:
            return _bytes_de_url_o_data(str(url))

    contenido = mensaje.get("content")
    if isinstance(contenido, list):
        for parte in contenido:
            if not isinstance(parte, dict):
                continue
            url = parte.get("image_url") or parte.get("url") or ""
            if isinstance(url, dict):
                url = url.get("url") or ""
            if url:
                return _bytes_de_url_o_data(str(url))
    if isinstance(contenido, str):
        m = re.search(r"!\[[^\]]*\]\((data:image/[^)]+|https?://[^)]+)\)", contenido)
        if m:
            return _bytes_de_url_o_data(m.group(1))
        m = re.search(r"(data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+)", contenido)
        if m:
            return _bytes_de_url_o_data(m.group(1).replace("\n", ""))
        if contenido.strip().startswith("http"):
            return _descargar(contenido.strip())

    texto = _texto_de_contenido(contenido)
    if texto:
        raise ErrorIA(
            "El modelo respondió con texto en vez de una imagen. "
            "Prueba otro modelo de imagen (por ejemplo google/gemini-2.5-flash-image) "
            "o el proveedor Pollinations (gratis)."
        )
    raise ErrorIA("La respuesta no contiene ninguna imagen.")


def _bytes_de_url_o_data(url: str) -> bytes:
    if url.startswith("data:"):
        _, _, datos = url.partition(",")
        if ";base64" in url[: url.find(",")]:
            return base64.b64decode(datos)
        return urllib.parse.unquote_to_bytes(datos)
    return _descargar(url)
