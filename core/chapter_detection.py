"""Detección inteligente de capítulos dentro de UN solo audio por reconocimiento de voz.

Soporta configuración flexible y estricta por el usuario:
* Palabras clave de inicio (Intro / Prólogo) -> una sola ocurrencia al inicio
* Modos de detección para capítulos intermedios:
  - "combinado": Palabra clave + Número ascendente (ej: "capítulo 1", "capítulo número 2")
  - "solo_palabra": Solo la palabra clave con gap de tiempo
  - "solo_numero": Solo números secuenciales (1, 2, 3...)
  - "flexible": Intenta combinar palabra + número; si no hay número, cuenta palabra clave con gap
* Palabras clave de cierre (Outro / Despedida) -> una sola ocurrencia al final
* Palabras puente ("número", "el", "la", "de", "del")
* Umbral de confianza configurable
* Gap mínimo de duración entre capítulos para evitar falsos positivos
* Límite máximo de capítulos opcional
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
import urllib.request
import wave
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from core.media import ErrorMedia, ruta_ffmpeg

MODELO_URL = "https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip"
MODELO_DIR = Path(__file__).resolve().parent / ".config"
RUTA_MODELO = MODELO_DIR / "vosk-model-small-es-0.42"

NUMEROS: dict[str, int] = {
    "cero": 0, "un": 1, "una": 1, "uno": 1, "primer": 1, "primero": 1, "primera": 1,
    "dos": 2, "segundo": 2, "segunda": 2,
    "tres": 3, "tercer": 3, "tercero": 3, "tercera": 3,
    "cuatro": 4, "cuarto": 4, "cuarta": 4,
    "cinco": 5, "quinto": 5, "quinta": 5,
    "seis": 6, "sexto": 6, "sexta": 6,
    "siete": 7, "septimo": 7, "septima": 7,
    "ocho": 8, "octavo": 8, "octava": 8,
    "nueve": 9, "noveno": 9, "novena": 9,
    "diez": 10, "decimo": 10, "decima": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciseis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19,
    "veinte": 20, "veintiuno": 21, "veintidos": 22, "veintitres": 23,
    "veinticuatro": 24, "veinticinco": 25, "veintiseis": 26, "veintisiete": 27,
    "veintiocho": 28, "veintinueve": 29, "treinta": 30, "cuarenta": 40,
    "cincuenta": 50, "sesenta": 60, "setenta": 70, "ochenta": 80,
    "noventa": 90, "ciento": 100, "cien": 100,
}


@dataclass
class ConfigDeteccion:
    """Configuración de palabras y modo de detección de capítulos."""
    palabras_intro: list[str] = field(default_factory=lambda: [
        "intro", "introduc", "prologo", "prefacio", "presentacion", "bienvenid", "inicio"
    ])
    palabras_cierre: list[str] = field(default_factory=lambda: [
        "cierre", "outro", "despedida", "conclusion", "final", "termina"
    ])
    modo_capitulos: str = "combinado"  # "combinado" | "solo_palabra" | "solo_numero" | "flexible"
    palabras_capitulo: list[str] = field(default_factory=lambda: [
        "capitulo", "seccion", "parte", "paso", "tema", "punto", "episodio", "bloque"
    ])
    palabras_puente: list[str] = field(default_factory=lambda: [
        "numero", "nro", "no", "el", "la", "de", "del", "es"
    ])
    confianza_minima: float = 0.20
    min_duracion_capitulo: float = 5.0   # Mínimo de segundos entre capítulos consecutivos
    max_capitulos: int = 0               # 0 = sin límite; o N capítulos máximos


def _norma(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return re.sub(r"[^\w]", "", t.lower())


def _coincide_keyword(token: str, keywords_norm: list[str]) -> bool:
    for kw in keywords_norm:
        if len(kw) >= 4:
            if token == kw or token.startswith(kw):
                return True
        else:
            if token == kw:
                return True
    return False


def _cumple_gap(t_marca: float, ultimo_cap_t: float, ultimo_cap_num: int, min_duracion: float) -> bool:
    """Verifica si ha pasado suficiente tiempo para aceptar un nuevo capítulo.
    
    Para el primer capítulo (ultimo_cap_num == 0), basta con estar 3s después
    del inicio o de la intro.
    Para los capítulos siguientes, exige al menos `min_duracion` segundos.
    """
    if ultimo_cap_num == 0:
        return (t_marca - max(0.0, ultimo_cap_t)) >= 3.0
    return (t_marca - ultimo_cap_t) >= min_duracion


def modelo_instalado() -> bool:
    return RUTA_MODELO.exists() and any(RUTA_MODELO.iterdir())


def asegurar_modelo(al_progreso, al_log) -> Path:
    """Descarga y descomprime el modelo Vosk si falta. Devuelve la ruta."""
    if modelo_instalado():
        return RUTA_MODELO
    MODELO_DIR.mkdir(parents=True, exist_ok=True)
    zip_ruta = MODELO_DIR / "vosk-es.zip"
    if not zip_ruta.exists():
        al_log("Descargando modelo de voz en español (~49 MB, solo la primera vez)…")

        def _progreso(bloques, bloque, total):
            if total > 0:
                al_progreso(int(bloques * bloque * 100 / total))

        urllib.request.urlretrieve(MODELO_URL, zip_ruta, reporthook=_progreso)
    al_log("Descomprimiendo modelo…")
    with zipfile.ZipFile(zip_ruta) as z:
        z.extractall(MODELO_DIR)
    zip_ruta.unlink()
    if not modelo_instalado():
        raise ErrorMedia("El modelo descargado no es válido; bórralo e intenta de nuevo.")
    return RUTA_MODELO


# ------------------------------------------------------------- Transcripción
def convertir_wav(audio: str, dir_tmp: Path) -> Path:
    """Audio a WAV 16 kHz mono, requisito de Vosk."""
    salida = dir_tmp / "audio16k.wav"
    subprocess.run(
        [ruta_ffmpeg(), "-y", "-loglevel", "error", "-i", audio,
         "-ac", "1", "-ar", "16000", str(salida)],
        check=True, capture_output=True, text=True,
    )
    return salida


def transcribir(wav: Path, modelo_ruta: Path, al_progreso, al_log) -> list[dict]:
    """Devuelve [{palabra, inicio, fin, conf}] reconocidas del WAV."""
    try:
        from vosk import KaldiRecognizer, Model, SetLogLevel
    except ImportError as exc:
        raise ErrorMedia(
            "Falta la librería de reconocimiento de voz.\n"
            "Instálala con:  pip install vosk"
        ) from exc
    SetLogLevel(-1)
    al_log("Cargando modelo de voz…")
    modelo = Model(str(modelo_ruta))
    rec = KaldiRecognizer(modelo, 16000)
    rec.SetWords(True)
    palabras: list[dict] = []
    total = wav.stat().st_size
    leido = 0
    with wave.open(str(wav), "rb") as w:
        while True:
            data = w.readframes(8000)
            if not data:
                break
            leido += len(data)
            al_progreso(int(leido * 90 / max(total, 1)))
            if rec.AcceptWaveform(data):
                palabras += _palabras_de(rec.Result())
        palabras += _palabras_de(rec.FinalResult())
    al_log(f"Transcripción completada: {len(palabras)} palabras detectadas.")
    return palabras


def _palabras_de(json_texto: str) -> list[dict]:
    try:
        dato = json.loads(json_texto)
    except json.JSONDecodeError:
        return []
    return [
        {"palabra": p.get("word", ""), "inicio": float(p.get("start", 0.0)),
         "fin": float(p.get("end", 0.0)), "conf": float(p.get("conf", 0.0))}
        for p in dato.get("result", [])
    ]


# --------------------------------------------------------------- Detección
def _numero_de(tokens: list[dict], i: int) -> tuple[int | None, int]:
    """Número en tokens[i]; devuelve (número, índice del último token usado)."""
    if i >= len(tokens):
        return None, i - 1
    t = tokens[i]["t"]
    if t.isdigit():
        return int(t), i
    n = NUMEROS.get(t)
    if n is not None:
        if n >= 30 and n % 10 == 0 and i + 2 < len(tokens) and tokens[i + 1]["t"] == "y":
            unidad = NUMEROS.get(tokens[i + 2]["t"])
            if unidad is not None and unidad < 10:
                return n + unidad, i + 2
        return n, i
    return None, i - 1


def _buscar_numero_adelante(tokens: list[dict], i: int, palabras_puente: list[str]) -> tuple[int | None, int]:
    """Busca un número a partir de i, saltando palabras puente como 'número', 'el', 'de'."""
    idx = i
    saltos = 0
    while idx < len(tokens) and saltos < 3:
        t = tokens[idx]["t"]
        if any(t == _norma(p) for p in palabras_puente):
            idx += 1
            saltos += 1
        else:
            break

    if idx >= len(tokens):
        return None, i - 1

    return _numero_de(tokens, idx)


def detectar_capitulos(palabras: list[dict], cfg: ConfigDeteccion | None = None) -> list[dict]:
    """Extrae marcas [{titulo, tiempo}] a partir de la transcripción y configuración.
    
    Aplica reglas estrictas para evitar falsos positivos:
    - Introducción única al principio.
    - Capítulos con números estrictamente ascendentes y separación temporal mínima.
    - Cierre único hacia el final del audio.
    """
    if cfg is None:
        cfg = ConfigDeteccion()

    norm_intros = [_norma(x) for x in cfg.palabras_intro if _norma(x)]
    norm_cierres = [_norma(x) for x in cfg.palabras_cierre if _norma(x)]
    norm_caps = [_norma(x) for x in cfg.palabras_capitulo if _norma(x)]

    tokens = []
    for p in palabras:
        if p.get("conf", 1.0) >= cfg.confianza_minima:
            t = _norma(p["palabra"])
            if t:
                tokens.append({"t": t, "ini": p["inicio"], "fin": p["fin"]})

    marcas: list[dict] = []
    tiene_intro = False
    ultimo_cap_num = 0
    ultimo_cap_t = -9999.0
    candidatos_cierre: list[dict] = []
    i = 0

    while i < len(tokens):
        t = tokens[i]["t"]
        t_ini = tokens[i]["ini"]
        t_fin = tokens[i]["fin"]

        # 1. Detección de Intro / Inicio (solo una vez)
        if not tiene_intro and _coincide_keyword(t, norm_intros):
            marcas.append({
                "titulo": "Introducción",
                "tiempo": max(0.0, t_ini),
            })
            tiene_intro = True
            ultimo_cap_t = max(ultimo_cap_t, t_ini)
            i += 1
            continue

        # 2. Candidato de Cierre / Final (acumular para validar al final)
        if _coincide_keyword(t, norm_cierres):
            candidatos_cierre.append({"tiempo": t_fin, "idx": i})
            i += 1
            continue

        # Si ya se alcanzó el límite máximo de capítulos configurado, no buscar más capítulos
        if cfg.max_capitulos > 0 and ultimo_cap_num >= cfg.max_capitulos:
            i += 1
            continue

        # 3. Detección de Capítulos Intermedios según Modo
        coincide_palabra_cap = _coincide_keyword(t, norm_caps)

        if cfg.modo_capitulos == "combinado":
            if coincide_palabra_cap:
                numero, fin_idx = _buscar_numero_adelante(tokens, i + 1, cfg.palabras_puente)
                if numero is not None and 0 < numero <= 200 and numero > ultimo_cap_num:
                    t_marca = tokens[fin_idx]["fin"]
                    if _cumple_gap(t_marca, ultimo_cap_t, ultimo_cap_num, cfg.min_duracion_capitulo):
                        ultimo_cap_num = numero
                        ultimo_cap_t = t_marca
                        marcas.append({
                            "titulo": f"Capítulo {numero}",
                            "tiempo": max(0.0, t_marca),
                        })
                        i = fin_idx + 1
                        continue

        elif cfg.modo_capitulos == "solo_numero":
            numero, fin_idx = _numero_de(tokens, i)
            if numero is not None and 0 < numero <= 200 and numero == ultimo_cap_num + 1:
                t_marca = tokens[fin_idx]["fin"]
                if _cumple_gap(t_marca, ultimo_cap_t, ultimo_cap_num, cfg.min_duracion_capitulo):
                    ultimo_cap_num = numero
                    ultimo_cap_t = t_marca
                    marcas.append({
                        "titulo": f"Capítulo {numero}",
                        "tiempo": max(0.0, t_marca),
                    })
                    i = fin_idx + 1
                    continue

        elif cfg.modo_capitulos == "solo_palabra":
            if coincide_palabra_cap:
                t_marca = t_fin
                if _cumple_gap(t_marca, ultimo_cap_t, ultimo_cap_num, cfg.min_duracion_capitulo):
                    ultimo_cap_num += 1
                    ultimo_cap_t = t_marca
                    marcas.append({
                        "titulo": f"Capítulo {ultimo_cap_num}",
                        "tiempo": max(0.0, t_marca),
                    })
                    i += 1
                    continue

        else:  # "flexible"
            if coincide_palabra_cap:
                numero, fin_idx = _buscar_numero_adelante(tokens, i + 1, cfg.palabras_puente)
                if numero is not None and 0 < numero <= 200 and numero > ultimo_cap_num:
                    t_marca = tokens[fin_idx]["fin"]
                    if _cumple_gap(t_marca, ultimo_cap_t, ultimo_cap_num, cfg.min_duracion_capitulo):
                        ultimo_cap_num = numero
                        ultimo_cap_t = t_marca
                        marcas.append({
                            "titulo": f"Capítulo {numero}",
                            "tiempo": max(0.0, t_marca),
                        })
                        i = fin_idx + 1
                        continue
                else:
                    t_marca = t_fin
                    if _cumple_gap(t_marca, ultimo_cap_t, ultimo_cap_num, cfg.min_duracion_capitulo):
                        ultimo_cap_num += 1
                        ultimo_cap_t = t_marca
                        marcas.append({
                            "titulo": f"Capítulo {ultimo_cap_num}",
                            "tiempo": max(0.0, t_marca),
                        })
                        i += 1
                        continue
            else:
                numero, fin_idx = _numero_de(tokens, i)
                if numero is not None and numero == ultimo_cap_num + 1 and ultimo_cap_num > 0:
                    t_marca = tokens[fin_idx]["fin"]
                    if _cumple_gap(t_marca, ultimo_cap_t, ultimo_cap_num, cfg.min_duracion_capitulo):
                        ultimo_cap_num = numero
                        ultimo_cap_t = t_marca
                        marcas.append({
                            "titulo": f"Capítulo {numero}",
                            "tiempo": max(0.0, t_marca),
                        })
                        i = fin_idx + 1
                        continue

        i += 1

    # 4. Evaluación de Cierre (solo tomar el más apropiado posterior al último capítulo)
    if candidatos_cierre:
        validos = [c for c in candidatos_cierre if c["tiempo"] >= ultimo_cap_t + 5.0]
        if validos:
            mejor_cierre = validos[-1]
            marcas.append({
                "titulo": "Cierre",
                "tiempo": max(0.0, mejor_cierre["tiempo"]),
            })

    # Normalizar intro al inicio si está cerca de 0
    for m in marcas:
        if m["titulo"] == "Introducción" and m["tiempo"] < 2.0:
            m["tiempo"] = 0.0

    marcas.sort(key=lambda m: m["tiempo"])

    # Filtrar marcas duplicadas o excesivamente cercanas (< 3.0 segundos)
    limpias: list[dict] = []
    for m in marcas:
        if limpias and (m["tiempo"] - limpias[-1]["tiempo"]) < 3.0:
            continue
        limpias.append(m)

    return limpias


def detectar_de_audio(audio: str, al_progreso, al_log, cfg: ConfigDeteccion | None = None,
                      al_palabras=None) -> list[dict]:
    """Pipeline completo: audio -> marcas [{titulo, tiempo}].

    Si se pasa `al_palabras`, recibe también la transcripción completa
    ([{palabra, inicio, fin, conf}]) para generar subtítulos.
    """
    if cfg is None:
        cfg = ConfigDeteccion()

    modelo = asegurar_modelo(
        al_progreso=lambda p: al_progreso(p, "descarga"), al_log=al_log)
    dir_tmp = Path(tempfile.mkdtemp(prefix="capitulos_"))
    try:
        al_log("Convirtiendo audio a 16 kHz mono…")
        al_progreso(5, "convertir")
        wav = convertir_wav(audio, dir_tmp)
        palabras = transcribir(
            wav, modelo,
            lambda p: al_progreso(p, "transcripcion"), al_log)
        if al_palabras is not None:
            al_palabras(palabras)
        marcas = detectar_capitulos(palabras, cfg)
        al_log(f"Capítulos detectados: {len(marcas)}")
        for m in marcas:
            mm, ss = divmod(m["tiempo"], 60)
            al_log(f"  [Capítulo] {m['titulo']} -> {int(mm)}:{ss:04.1f}")
        if not marcas:
            al_log("No hubo coincidencias claras con las palabras configuradas; prueba ajustar las palabras clave o el modo.")
        return marcas
    finally:
        shutil.rmtree(dir_tmp, ignore_errors=True)
