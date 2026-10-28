"""Cliente mínimo de IA del complemento (solo biblioteca estándar)."""
from __future__ import annotations

import base64
import json
import random
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_OPENROUTER = "https://openrouter.ai/api/v1"
BASE_OPENAI = "https://api.openai.com/v1"
BASE_ELEVENLABS = "https://api.elevenlabs.io/v1"

MODELO_TEXTO = "nvidia/nemotron-3-ultra-550b-a55b:free"
RESPALDOS = [
    "nvidia/nemotron-3.5-lightning:free",
    "google/gemma-4-31b-it:free",
    "openrouter/free",
]
MODELO_IMAGEN = "google/gemini-2.5-flash-image"


class ErrorCliente(RuntimeError):
    pass


def _cabeceras(clave: str) -> dict:
    cab = {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "User-Agent": "StudioCut-AI/1.0",
        "HTTP-Referer": "https://studiocut.local",
        "X-Title": "StudioCut AI",
    }
    if clave:
        cab["Authorization"] = f"Bearer {clave}"
    return cab


def _json(url: str, *, payload=None, clave="", timeout=180.0, metodo="POST") -> dict:
    datos = None
    if payload is not None:
        datos = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=datos, headers=_cabeceras(clave), method=metodo
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise ErrorCliente("Clave de API inválida (HTTP 401).") from exc
        if exc.code == 402:
            raise ErrorCliente("El proveedor pide crédito (HTTP 402).") from exc
        if exc.code == 429:
            raise ErrorCliente("Límite de peticiones alcanzado (HTTP 429).") from exc
        raise ErrorCliente(f"HTTP {exc.code}: {detalle}") from exc
    except urllib.error.URLError as exc:
        raise ErrorCliente(f"Sin conexión: {exc.reason}") from exc


def _texto(contenido) -> str:
    if isinstance(contenido, str):
        return contenido
    if isinstance(contenido, list):
        return "\n".join(
            str(p.get("text") or "")
            for p in contenido
            if isinstance(p, dict) and p.get("type") == "text"
        )
    return ""


def generar_texto(
    mensajes: list[dict], *, clave: str, modelo: str = "", timeout: float = 240.0
) -> str:
    if not clave:
        raise ErrorCliente("Falta la clave de OpenRouter.")
    modelos = [modelo or MODELO_TEXTO] + [m for m in RESPALDOS if m != modelo]
    errores = []
    for nombre in modelos:
        try:
            datos = _json(
                BASE_OPENROUTER + "/chat/completions",
                payload={
                    "model": nombre,
                    "messages": mensajes,
                    "temperature": 0.8,
                    "max_tokens": 4096,
                },
                clave=clave,
                timeout=timeout,
            )
            eleccion = (datos.get("choices") or [{}])[0]
            texto = _texto((eleccion.get("message") or {}).get("content")).strip()
            if texto:
                return texto
            errores.append(f"{nombre}: respuesta vacía")
        except ErrorCliente as exc:
            errores.append(f"{nombre}: {exc}")
            if "401" in str(exc):
                raise
            if "429" in str(exc):
                time.sleep(2)
    raise ErrorCliente("Ningún modelo respondió.\n" + "\n".join(errores))


def listar_modelos_gratis(clave: str = "") -> list[dict]:
    datos = _json(
        BASE_OPENROUTER + "/models", clave=clave, timeout=60, metodo="GET"
    )
    libres = []
    for modelo in datos.get("data") or []:
        precio = modelo.get("pricing") or {}
        try:
            gratis = float(precio.get("prompt", 1)) == 0 and float(
                precio.get("completion", 1)
            ) == 0
        except (TypeError, ValueError):
            gratis = False
        if not gratis:
            continue
        salidas = (modelo.get("architecture") or {}).get("output_modalities") or ["text"]
        if "text" in salidas:
            libres.append({
                "id": modelo.get("id"),
                "nombre": modelo.get("name"),
                "contexto": modelo.get("context_length", 0),
            })
    libres.sort(key=lambda m: -(m.get("contexto") or 0))
    return libres


def _descargar(url: str, timeout: float = 240.0) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "StudioCut-AI/1.0", "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def generar_imagen(
    prompt: str,
    *,
    proveedor: str = "pollinations",
    clave: str = "",
    modelo: str = "",
    ancho: int = 1920,
    alto: int = 1080,
    semilla: int | None = None,
) -> bytes:
    proveedor = (proveedor or "pollinations").lower()
    if proveedor == "pollinations":
        semilla = semilla or random.randint(1, 999_999)
        url = (
            "https://image.pollinations.ai/prompt/"
            + urllib.parse.quote(prompt[:1500], safe="")
            + "?"
            + urllib.parse.urlencode({
                "width": ancho, "height": alto, "nologo": "true",
                "model": "flux", "seed": semilla,
            })
        )
        return _descargar(url)
    if proveedor == "openrouter":
        if not clave:
            raise ErrorCliente("OpenRouter necesita clave para imágenes.")
        datos = _json(
            BASE_OPENROUTER + "/chat/completions",
            payload={
                "model": modelo or MODELO_IMAGEN,
                "messages": [{"role": "user", "content": prompt}],
                "modalities": ["image", "text"],
            },
            clave=clave,
            timeout=300,
        )
        return _imagen_de_chat(datos)
    if proveedor == "openai":
        if not clave:
            raise ErrorCliente("OpenAI necesita clave para imágenes.")
        datos = _json(
            BASE_OPENAI + "/images/generations",
            payload={
                "model": modelo or "gpt-image-1",
                "prompt": prompt,
                "size": "1536x1024",
                "n": 1,
            },
            clave=clave,
            timeout=300,
        )
        item = (datos.get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            return _descargar(item["url"])
        raise ErrorCliente("La respuesta no trae imagen.")
    raise ErrorCliente(f"Proveedor de imágenes desconocido: {proveedor}")


def _imagen_de_chat(datos: dict) -> bytes:
    mensaje = ((datos.get("choices") or [{}])[0].get("message") or {})
    for imagen in mensaje.get("images") or []:
        url = ""
        if isinstance(imagen, dict):
            valor = imagen.get("image_url") or imagen.get("url") or ""
            url = valor.get("url") if isinstance(valor, dict) else valor
        elif isinstance(imagen, str):
            url = imagen
        if url:
            return _bytes(url)
    contenido = mensaje.get("content")
    if isinstance(contenido, list):
        for parte in contenido:
            valor = parte.get("image_url") if isinstance(parte, dict) else None
            url = valor.get("url") if isinstance(valor, dict) else valor
            if url:
                return _bytes(url)
    if isinstance(contenido, str):
        m = re.search(r"\((data:image/[^)]+|https?://[^)]+)\)", contenido)
        if m:
            return _bytes(m.group(1))
    raise ErrorCliente("El modelo respondió sin imagen. Prueba otro modelo.")


def _bytes(url: str) -> bytes:
    if url.startswith("data:"):
        return base64.b64decode(url.partition(",")[2])
    return _descargar(url)


def generar_voz(
    texto: str,
    salida: Path,
    *,
    proveedor: str = "edge",
    clave_eleven: str = "",
    voice_id: str = "",
) -> str:
    if proveedor == "elevenlabs" and clave_eleven:
        if not voice_id:
            raise ErrorCliente("Falta voice_id de ElevenLabs.")
        cab = {
            "xi-api-key": clave_eleven,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
            "User-Agent": "StudioCut-AI/1.0",
        }
        req = urllib.request.Request(
            f"{BASE_ELEVENLABS}/text-to-speech/{voice_id}",
            data=json.dumps({
                "text": texto,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
            }, ensure_ascii=False).encode("utf-8"),
            headers=cab,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                audio = resp.read()
        except urllib.error.HTTPError as exc:
            raise ErrorCliente(f"ElevenLabs HTTP {exc.code}.") from exc
        salida = salida.with_suffix(".mp3")
        salida.write_bytes(audio)
        return str(salida)

    # TTS local de Windows (SAPI).
    salida.parent.mkdir(parents=True, exist_ok=True)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="studiocut_tts_") as tmp:
        txt = Path(tmp) / "texto.txt"
        txt.write_text(texto, encoding="utf-8-sig")
        ps = Path(tmp) / "voz.ps1"
        ps.write_text(
            "Add-Type -AssemblyName System.Speech\n"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
            "$s.SetOutputToWaveFile('" + str(salida.resolve()) + "')\n"
            "$texto = Get-Content -Raw -Encoding UTF8 '" + str(txt.resolve()) + "'\n"
            "$s.Speak($texto)\n"
            "$s.Dispose()\n",
            encoding="utf-8-sig",
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps)],
            check=True,
            capture_output=True,
            timeout=3600,
        )
    return str(salida)
