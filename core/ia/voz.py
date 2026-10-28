"""Voz con ElevenLabs (opcional) y vuelta al TTS de siempre.

ElevenLabs solo se usa si el usuario lo activa y guarda su clave. Si falla
o no hay clave, se genera la voz con Edge/Windows sin interrumpir el flujo.
"""
from __future__ import annotations

import subprocess
import urllib.error
import urllib.request
from pathlib import Path

from core.media import ruta_ffmpeg

BASE_ELEVENLABS = "https://api.elevenlabs.io/v1"
MODELO_ELEVENLABS = "eleven_multilingual_v2"


class ErrorVozIA(RuntimeError):
    pass


def _cabeceras(clave: str) -> dict:
    return {
        "xi-api-key": clave,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "StudioCut/1.0",
    }


def listar_voces_elevenlabs(clave: str, timeout: float = 30.0) -> list[dict]:
    """Voces de la cuenta de ElevenLabs."""
    if not clave:
        raise ErrorVozIA("Falta la clave de ElevenLabs.")
    req = urllib.request.Request(
        BASE_ELEVENLABS + "/voices", headers=_cabeceras(clave), method="GET"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            import json

            datos = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise ErrorVozIA("Clave de ElevenLabs inválida (HTTP 401).") from exc
        raise ErrorVozIA(f"ElevenLabs respondió HTTP {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ErrorVozIA(f"No se pudo conectar con ElevenLabs: {exc.reason}") from exc

    voces = []
    for voz in datos.get("voices") or []:
        voces.append({
            "id": str(voz.get("voice_id") or ""),
            "nombre": str(voz.get("name") or "Voz"),
            "categoria": str(voz.get("category") or ""),
            "descripcion": str(voz.get("description") or ""),
        })
    return [v for v in voces if v["id"]]


def generar_voz_elevenlabs(
    texto: str,
    salida: str | Path,
    *,
    clave: str,
    voice_id: str,
    modelo: str = MODELO_ELEVENLABS,
    estabilidad: float = 0.5,
    similitud: float = 0.75,
    velocidad: float = 1.0,
    timeout: float = 600.0,
) -> str:
    """Genera MP3 con ElevenLabs y lo convierte a WAV si es posible."""
    if not texto.strip():
        raise ErrorVozIA("El texto está vacío.")
    if not clave:
        raise ErrorVozIA("Falta la clave de ElevenLabs.")
    if not voice_id:
        raise ErrorVozIA("Elige una voz de ElevenLabs.")

    import json

    payload = {
        "text": texto,
        "model_id": modelo,
        "voice_settings": {
            "stability": max(0.0, min(1.0, estabilidad)),
            "similarity_boost": max(0.0, min(1.0, similitud)),
            "speed": max(0.7, min(1.2, velocidad)),
        },
    }
    cabeceras = _cabeceras(clave)
    cabeceras["Accept"] = "audio/mpeg"
    req = urllib.request.Request(
        f"{BASE_ELEVENLABS}/text-to-speech/{voice_id}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=cabeceras,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            audio = resp.read()
    except urllib.error.HTTPError as exc:
        detalle = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise ErrorVozIA("Clave de ElevenLabs inválida (HTTP 401).") from exc
        if exc.code == 402:
            raise ErrorVozIA("ElevenLabs pide crédito o plan de pago (HTTP 402).") from exc
        raise ErrorVozIA(f"ElevenLabs respondió HTTP {exc.code}: {detalle}") from exc
    except urllib.error.URLError as exc:
        raise ErrorVozIA(f"No se pudo conectar con ElevenLabs: {exc.reason}") from exc

    if len(audio) < 1000:
        raise ErrorVozIA("ElevenLabs devolvió un audio vacío.")

    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    mp3 = salida.with_suffix(".mp3")
    mp3.write_bytes(audio)
    try:
        wav = salida.with_suffix(".wav")
        subprocess.run(
            [ruta_ffmpeg(), "-y", "-loglevel", "error", "-i", str(mp3),
             "-ar", "44100", "-ac", "2", str(wav)],
            check=True, capture_output=True, text=True, timeout=180,
        )
        mp3.unlink(missing_ok=True)
        return str(wav)
    except Exception:
        # Sin ffmpeg el MP3 también sirve para el resto del flujo.
        return str(mp3)


def generar_voz_ia(
    texto: str,
    salida: str | Path,
    *,
    proveedor: str = "edge",
    clave_elevenlabs: str = "",
    voice_id: str = "",
    voz_edge: str = "",
    velocidad: int = 0,
    al_log=None,
    cancelar_ev=None,
) -> str:
    """Genera la voz con ElevenLabs si se pidió; si no, con el TTS actual."""
    proveedor = (proveedor or "edge").strip().lower()
    if proveedor == "elevenlabs":
        if not clave_elevenlabs:
            if al_log:
                al_log("[Voz] No hay clave de ElevenLabs; se usa el TTS de siempre.")
        else:
            try:
                if al_log:
                    al_log("[Voz] Generando con ElevenLabs…")
                ruta = generar_voz_elevenlabs(
                    texto, salida, clave=clave_elevenlabs, voice_id=voice_id
                )
                if al_log:
                    al_log(f"[Voz] ElevenLabs listo: {Path(ruta).name}")
                return ruta
            except Exception as exc:  # noqa: BLE001
                if al_log:
                    al_log(f"[Voz] ElevenLabs falló ({exc}); se usa el TTS de siempre.")

    from core import voz_tts

    return voz_tts.generar_voz(
        texto,
        str(salida),
        voz=voz_edge or None,
        velocidad=velocidad,
        al_log=al_log,
        cancelar_ev=cancelar_ev,
    )
