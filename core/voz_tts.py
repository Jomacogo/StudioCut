"""Generación de voz con Microsoft Neural TTS (Edge-TTS) y Windows SAPI.

Soporta voces neuronales naturales de alta calidad (como Jorge Multilingüe es-MX)
con fallback a las voces locales de Windows SAPI.
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from core.media import ErrorMedia, ruta_ffmpeg


class ErrorVoz(ErrorMedia):
    pass


# Lista de voces neurales en español de alta fidelidad (Microsoft Neural)
# «Jorge» (es-MX) como voz predeterminada número 1.
VOCES_NEURALES_ESPANOL = [
    {
        "id": "es-MX-JorgeNeural",
        "nombre": "Jorge (Multilingüe)",
        "cultura": "es-MX (México) [Neural]",
        "genero": "Masculino",
        "motor": "edge",
    },
    {
        "id": "es-MX-DaliaNeural",
        "nombre": "Dalia",
        "cultura": "es-MX (México) [Neural]",
        "genero": "Femenino",
        "motor": "edge",
    },
    {
        "id": "es-ES-AlvaroNeural",
        "nombre": "Álvaro",
        "cultura": "es-ES (España) [Neural]",
        "genero": "Masculino",
        "motor": "edge",
    },
    {
        "id": "es-ES-ElviraNeural",
        "nombre": "Elvira",
        "cultura": "es-ES (España) [Neural]",
        "genero": "Femenino",
        "motor": "edge",
    },
    {
        "id": "es-CO-GonzaloNeural",
        "nombre": "Gonzalo",
        "cultura": "es-CO (Colombia) [Neural]",
        "genero": "Masculino",
        "motor": "edge",
    },
    {
        "id": "es-CO-SalomeNeural",
        "nombre": "Salomé",
        "cultura": "es-CO (Colombia) [Neural]",
        "genero": "Femenino",
        "motor": "edge",
    },
    {
        "id": "es-AR-TomasNeural",
        "nombre": "Tomás",
        "cultura": "es-AR (Argentina) [Neural]",
        "genero": "Masculino",
        "motor": "edge",
    },
    {
        "id": "es-AR-ElenaNeural",
        "nombre": "Elena",
        "cultura": "es-AR (Argentina) [Neural]",
        "genero": "Femenino",
        "motor": "edge",
    },
    {
        "id": "es-US-AlonsoNeural",
        "nombre": "Alonso",
        "cultura": "es-US (EE.UU.) [Neural]",
        "genero": "Masculino",
        "motor": "edge",
    },
    {
        "id": "es-US-PalomaNeural",
        "nombre": "Paloma",
        "cultura": "es-US (EE.UU.) [Neural]",
        "genero": "Femenino",
        "motor": "edge",
    },
    {
        "id": "es-CL-LorenzoNeural",
        "nombre": "Lorenzo",
        "cultura": "es-CL (Chile) [Neural]",
        "genero": "Masculino",
        "motor": "edge",
    },
    {
        "id": "es-CL-CatalinaNeural",
        "nombre": "Catalina",
        "cultura": "es-CL (Chile) [Neural]",
        "genero": "Femenino",
        "motor": "edge",
    },
    {
        "id": "es-PE-AlexNeural",
        "nombre": "Alex",
        "cultura": "es-PE (Perú) [Neural]",
        "genero": "Masculino",
        "motor": "edge",
    },
    {
        "id": "es-PE-CamilaNeural",
        "nombre": "Camila",
        "cultura": "es-PE (Perú) [Neural]",
        "genero": "Femenino",
        "motor": "edge",
    },
    {
        "id": "es-VE-SebastianNeural",
        "nombre": "Sebastián",
        "cultura": "es-VE (Venezuela) [Neural]",
        "genero": "Masculino",
        "motor": "edge",
    },
    {
        "id": "es-VE-PaolaNeural",
        "nombre": "Paola",
        "cultura": "es-VE (Venezuela) [Neural]",
        "genero": "Femenino",
        "motor": "edge",
    },
]

_PS_LISTAR = (
    "Add-Type -AssemblyName System.Speech;"
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
    "$s.GetInstalledVoices() | ForEach-Object { "
    "'{0}|{1}' -f $_.VoiceInfo.Name, $_.VoiceInfo.Culture.Name }"
)


def _listar_voces_sapi() -> list[dict]:
    """Voces SAPI5 instaladas localmente en Windows."""
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", _PS_LISTAR],
            capture_output=True, text=True, timeout=10,
            encoding="utf-8", errors="replace",
        )
        voces = []
        for linea in (r.stdout or "").splitlines():
            if "|" in linea:
                nombre, cultura = linea.split("|", 1)
                nombre, cultura = nombre.strip(), cultura.strip()
                if any(x in nombre.lower() for x in ("jorge", "spanish", "español")) \
                        or cultura.lower().startswith("es"):
                    voces.append({
                        "id": nombre,
                        "nombre": nombre,
                        "cultura": f"{cultura} [Local Windows]",
                        "genero": "Local",
                        "motor": "sapi",
                    })
        return voces
    except Exception:
        return []


def listar_voces() -> list[dict]:
    """Lista combinada de voces disponibles.
    
    1. Voces neuronales de Microsoft (Jorge Multilingüe, Dalia, Álvaro, etc.)
    2. Voces locales de Windows SAPI5 (offline fallback)
    """
    voces = list(VOCES_NEURALES_ESPANOL)
    voces_locales = _listar_voces_sapi()
    for vl in voces_locales:
        if not any(v["id"] == vl["id"] for v in voces):
            voces.append(vl)
    return voces


def elegir_voz(preferida: str | None = None) -> str:
    """Devuelve el ID de la voz a usar (priorizando Jorge Multilingüe)."""
    voces = listar_voces()
    if preferida:
        # Coincidencia exacta por ID o nombre
        for v in voces:
            if v["id"] == preferida or v["nombre"] == preferida:
                return v["id"]
        # Coincidencia parcial (ej. "jorge")
        pref_lower = preferida.lower()
        for v in voces:
            if pref_lower in v["id"].lower() or pref_lower in v["nombre"].lower():
                return v["id"]

    # Por defecto: Jorge Multilingüe (es-MX)
    for v in voces:
        if "jorge" in v["id"].lower():
            return v["id"]

    return voces[0]["id"] if voces else "es-MX-JorgeNeural"


async def _sintetizar_edge_async(
    texto: str,
    salida_mp3: Path,
    voz_id: str,
    velocidad: int = 0,
    cancelar_ev=None,
) -> None:
    """Invoca edge-tts asíncronamente."""
    import edge_tts

    # Convertir velocidad (-10 a 10) a porcentaje (+0%, +10%, etc.)
    pct = max(-50, min(100, velocidad * 5))
    rate_str = f"{pct:+d}%"

    communicate = edge_tts.Communicate(texto, voz_id, rate=rate_str)
    
    # Manejar cancelación durante el guardado
    with open(salida_mp3, "wb") as f:
        async for chunk in communicate.stream():
            if cancelar_ev is not None and cancelar_ev.is_set():
                raise ErrorVoz("Operación cancelada por el usuario.")
            if chunk["type"] == "audio":
                f.write(chunk["data"])


def _generar_con_edge(
    texto: str,
    salida_wav: Path,
    voz_id: str,
    velocidad: int = 0,
    al_log=None,
    cancelar_ev=None,
) -> bool:
    """Genera audio con Microsoft Neural TTS (Edge-TTS) y lo convierte a WAV.
    
    Devuelve True si tuvo éxito, False si falló (para permitir fallback a SAPI).
    """
    try:
        import edge_tts
    except ImportError:
        if al_log:
            al_log("Librería edge-tts no instalada; recurriendo a motor local.")
        return False

    dir_tmp = Path(tempfile.mkdtemp(prefix="voz_edge_"))
    salida_mp3 = dir_tmp / "audio_neural.mp3"
    try:
        if al_log:
            al_log(f"Generando voz con Microsoft Neural TTS: {voz_id}…")

        asyncio.run(
            _sintetizar_edge_async(texto, salida_mp3, voz_id, velocidad, cancelar_ev)
        )

        if cancelar_ev is not None and cancelar_ev.is_set():
            raise ErrorVoz("Operación cancelada.")

        if not salida_mp3.exists() or salida_mp3.stat().st_size == 0:
            raise ErrorVoz("Edge-TTS no produjo datos de audio.")

        if al_log:
            al_log("Convirtiendo audio neuronal a WAV estéreo 44.1 kHz…")

        # Convertir MP3 a WAV compatible con Vosk y línea de tiempo
        subprocess.run(
            [
                ruta_ffmpeg(), "-y", "-loglevel", "error",
                "-i", str(salida_mp3),
                "-ac", "2", "-ar", "44100",
                str(salida_wav.resolve())
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return True
    except ErrorVoz:
        raise
    except Exception as exc:
        if al_log:
            al_log(f"Aviso Neural TTS: {exc} (intentando con voz local de Windows…)")
        return False
    finally:
        shutil.rmtree(dir_tmp, ignore_errors=True)


def _generar_con_sapi(
    texto: str,
    salida_wav: Path,
    voz: str | None = None,
    velocidad: int = 0,
    al_log=None,
    cancelar_ev=None,
) -> str:
    """Genera audio usando Windows SpeechSynthesizer (SAPI5 local)."""
    dir_tmp = Path(tempfile.mkdtemp(prefix="voz_sapi_"))
    try:
        txt = dir_tmp / "texto.txt"
        txt.write_text(texto, encoding="utf-8-sig")
        ps = dir_tmp / "voz.ps1"

        linea_voz = f"try {{ $s.SelectVoice('{voz}') }} catch {{}}" if voz else ""
        if al_log:
            al_log(f"Voz local Windows: {voz or 'por defecto'}")

        ps.write_text(
            "Add-Type -AssemblyName System.Speech\n"
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
            + linea_voz + "\n"
            f"$s.Rate = {int(velocidad)}\n"
            f"$s.SetOutputToWaveFile('{salida_wav.resolve()}')\n"
            "$texto = Get-Content -Raw -Encoding UTF8 '"
            + str(txt.resolve()) + "'\n"
            "$s.Speak($texto)\n"
            "$s.Dispose()\n",
            encoding="utf-8-sig",
        )
        proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(ps.resolve())],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        try:
            proc.communicate(timeout=7200)
        except subprocess.TimeoutExpired:
            proc.kill()
            raise ErrorVoz("El TTS tardó demasiado (timeout).")
        finally:
            if cancelar_ev is not None and cancelar_ev.is_set() and proc.poll() is None:
                proc.terminate()

        if cancelar_ev is not None and cancelar_ev.is_set():
            raise ErrorVoz("Operación cancelada.")
        if not salida_wav.exists():
            stderr = (proc.stderr or "").strip()
            raise ErrorVoz("El TTS no generó el audio.\nDetalle: " + stderr[:300])

        return str(salida_wav)
    finally:
        shutil.rmtree(dir_tmp, ignore_errors=True)


def generar_voz(
    texto: str,
    salida_wav: str,
    voz: str | None = None,
    velocidad: int = 0,
    al_log=None,
    cancelar_ev=None,
) -> str:
    """Genera audio a partir del texto y devuelve la ruta del WAV.

    Prioridad:
    1. Si la voz es Neural (o predeterminada): usa Microsoft Edge-TTS con Jorge Multilingüe.
    2. Fallback transparente a SAPI si no hay conexión a internet o falla la red.
    """
    if not texto.strip():
        raise ErrorVoz("El texto está vacío.")

    ruta_wav = Path(salida_wav).resolve()
    ruta_wav.parent.mkdir(parents=True, exist_ok=True)

    voz_elegida = elegir_voz(voz) if voz else elegir_voz()

    # Si es voz Neural (o predeterminada Jorge)
    if "neural" in voz_elegida.lower() or voz_elegida.startswith("es-"):
        exito = _generar_con_edge(
            texto, ruta_wav, voz_elegida, velocidad, al_log=al_log, cancelar_ev=cancelar_ev
        )
        if exito and ruta_wav.exists():
            if al_log:
                al_log(f"Audio generado con éxito ({voz_elegida}): {ruta_wav.name}")
            return str(ruta_wav)

    # Fallback o selección explícita de voz SAPI local
    return _generar_con_sapi(
        texto, ruta_wav, voz=voz_elegida, velocidad=velocidad, al_log=al_log, cancelar_ev=cancelar_ev
    )
