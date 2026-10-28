"""Generación, edición, importación/exportación y formateo de subtítulos.

Modelo de datos sencillo y reutilizable (sin GUI):

* ``EstiloSubtitulos``: tipografía, colores, posición y márgenes.
* Un subtítulo es un ``dict`` con ``inicio``, ``fin`` y ``texto`` (segundos).
* Se puede importar/exportar SRT y VTT, generar desde capítulos o desde las
  palabras con tiempos de la transcripción, y escribir un ASS con estilos
  exactos para quemarlo en el video con FFmpeg.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path


# ------------------------------------------------------------------ Estilo
@dataclass
class EstiloSubtitulos:
    """Estilo visual de los subtítulos (valores relativos al alto del video)."""

    fuente: str = "Segoe UI"
    tamano: float = 5.5            # % del alto del video
    negrita: bool = True
    color: str = "#FFFFFF"
    color_contorno: str = "#000000"
    contorno: float = 2.4          # grosor en px a 1080p
    sombra: float = 1.0            # sombra en px a 1080p
    caja: bool = False
    caja_color: str = "#000000"
    caja_opacidad: float = 0.55
    posicion: str = "abajo"        # arriba | centro | abajo
    margen: float = 4.0            # % del alto
    max_caracteres: int = 42

    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def desde_dict(cls, datos: dict | None) -> "EstiloSubtitulos":
        if not datos:
            return cls()
        nombres = {f.name for f in fields(cls)}
        limpio = {k: v for k, v in dict(datos).items() if k in nombres}
        if limpio.get("posicion") not in ("arriba", "centro", "abajo"):
            limpio["posicion"] = "abajo"
        return cls(**limpio)


# ------------------------------------------------------------------ Tiempo
def formato_tiempo_srt(segundos: float) -> str:
    """Convierte segundos flotantes a formato de tiempo SRT: HH:MM:SS,mmm."""
    segundos = max(0.0, float(segundos))
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = int(segundos % 60)
    milis = int(round((segundos - int(segundos)) * 1000))
    if milis >= 1000:
        milis = 999
    return f"{horas:02d}:{minutos:02d}:{segs:02d},{milis:03d}"


def formato_tiempo_ass(segundos: float) -> str:
    """Convierte segundos a formato ASS: H:MM:SS.cc (centésimas)."""
    segundos = max(0.0, float(segundos))
    horas = int(segundos // 3600)
    minutos = int((segundos % 3600) // 60)
    segs = int(segundos % 60)
    centesimas = int(round((segundos - int(segundos)) * 100))
    if centesimas >= 100:
        centesimas = 99
    return f"{horas}:{minutos:02d}:{segs:02d}.{centesimas:02d}"


def _parsear_tiempo(ts: str) -> float:
    """Parsea HH:MM:SS.mmm, MM:SS.mmm, HH:MM:SS,mmm o MM:SS,mmm."""
    limpio = ts.strip().replace(",", ".").split()[0]
    partes = limpio.split(":")
    if len(partes) == 3:
        h, m, s = partes
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(partes) == 2:
        m, s = partes
        return int(m) * 60 + float(s)
    raise ValueError(f"Tiempo inválido: {ts}")


def _normalizar_cue(inicio: float, fin: float, texto: str) -> dict | None:
    texto = re.sub(r"\s+", " ", str(texto or "")).strip()
    if not texto:
        return None
    inicio = max(0.0, float(inicio))
    fin = max(inicio + 0.15, float(fin))
    return {"inicio": round(inicio, 3), "fin": round(fin, 3), "texto": texto}


# ------------------------------------------------------------------ Lectura
def leer_srt(ruta: str | Path) -> list[dict]:
    """Lee un SRT y devuelve la lista de subtítulos normalizados."""
    texto = Path(ruta).read_text(encoding="utf-8-sig", errors="replace")
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    cues: list[dict] = []
    for bloque in re.split(r"\n\s*\n", texto.strip()):
        lineas = [l for l in bloque.split("\n") if l.strip()]
        if not lineas:
            continue
        idx = 1 if lineas[0].strip().isdigit() else 0
        if idx >= len(lineas) or "-->" not in lineas[idx]:
            continue
        izq, der = lineas[idx].split("-->", 1)
        try:
            ini = _parsear_tiempo(izq)
            fin = _parsear_tiempo(der)
        except ValueError:
            continue
        cue = _normalizar_cue(ini, fin, " ".join(lineas[idx + 1:]))
        if cue:
            cues.append(cue)
    return sorted(cues, key=lambda c: c["inicio"])


def leer_vtt(ruta: str | Path) -> list[dict]:
    """Lee un WebVTT y devuelve la lista de subtítulos normalizados."""
    texto = Path(ruta).read_text(encoding="utf-8-sig", errors="replace")
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    cues: list[dict] = []
    for bloque in re.split(r"\n\s*\n", texto.strip()):
        lineas = [l for l in bloque.split("\n") if l.strip()]
        if not lineas:
            continue
        if lineas[0].strip().upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        idx = 0
        if "-->" not in lineas[0] and len(lineas) > 1 and "-->" in lineas[1]:
            idx = 1
        if idx >= len(lineas) or "-->" not in lineas[idx]:
            continue
        izq, der = lineas[idx].split("-->", 1)
        try:
            ini = _parsear_tiempo(izq)
            fin = _parsear_tiempo(der)
        except ValueError:
            continue
        cuerpo = " ".join(lineas[idx + 1:])
        cuerpo = re.sub(r"<[^>]+>", "", cuerpo)  # quitar etiquetas de estilo
        cue = _normalizar_cue(ini, fin, cuerpo)
        if cue:
            cues.append(cue)
    return sorted(cues, key=lambda c: c["inicio"])


def leer_subtitulos(ruta: str | Path) -> list[dict]:
    """Importa .srt o .vtt; lanza ValueError si el formato no se reconoce."""
    p = Path(ruta)
    suf = p.suffix.lower()
    if suf == ".srt":
        return leer_srt(p)
    if suf == ".vtt":
        return leer_vtt(p)
    raise ValueError("Formato no soportado (usa .srt o .vtt).")


# ------------------------------------------------------------------ Escritura
def escribir_srt(cues: list[dict], ruta: str | Path) -> Path:
    """Escribe una lista de subtítulos como SRT."""
    destino = Path(ruta).resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)
    lineas: list[str] = []
    for i, cue in enumerate(cues, 1):
        texto = _normalizar_cue(cue.get("inicio", 0), cue.get("fin", 0), cue.get("texto", ""))
        if not texto:
            continue
        lineas.append(str(i))
        lineas.append(
            f"{formato_tiempo_srt(texto['inicio'])} --> {formato_tiempo_srt(texto['fin'])}"
        )
        lineas.append(texto["texto"])
        lineas.append("")
    destino.write_text("\n".join(lineas), encoding="utf-8")
    return destino


def exportar_srt(cues: list[dict], ruta: str | Path) -> Path:
    """Alias público de :func:`escribir_srt`."""
    return escribir_srt(cues, ruta)


def envolver_texto(texto: str, max_caracteres: int = 42) -> list[str]:
    """Divide el texto en líneas sin partir palabras."""
    limite = max(8, int(max_caracteres))
    lineas: list[str] = []
    for parrafo in str(texto or "").replace("\\N", "\n").split("\n"):
        palabras = parrafo.split()
        actual = ""
        for palabra in palabras:
            candidato = f"{actual} {palabra}".strip()
            if actual and len(candidato) > limite:
                lineas.append(actual)
                actual = palabra
            else:
                actual = candidato
        if actual:
            lineas.append(actual)
    return lineas or [""]


def _color_ass(color: str, opacidad: float = 1.0) -> str:
    """Convierte #RRGGBB a &HAABBGGRR (ASS usa BGRA y alfa invertido)."""
    h = str(color or "#FFFFFF").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        h = "FFFFFF"
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        r, g, b = 255, 255, 255
    alfa = int(round((1.0 - max(0.0, min(1.0, float(opacidad)))) * 255))
    return f"&H{alfa:02X}{b:02X}{g:02X}{r:02X}"


def escribir_ass(
    cues: list[dict],
    ruta: str | Path,
    estilo: EstiloSubtitulos | None = None,
    ancho: int = 1920,
    alto: int = 1080,
) -> Path:
    """Escribe un ASS con estilos exactos en píxeles del video de salida."""
    estilo = estilo or EstiloSubtitulos()
    destino = Path(ruta).resolve()
    destino.parent.mkdir(parents=True, exist_ok=True)

    ancho = max(320, int(ancho))
    alto = max(240, int(alto))
    fuente = (estilo.fuente or "Segoe UI").replace(",", " ")
    tamano_px = max(10, int(round(alto * max(1.0, estilo.tamano) / 100.0)))
    borde_px = max(0.0, estilo.contorno * alto / 1080.0)
    sombra_px = max(0.0, estilo.sombra * alto / 1080.0)
    alineacion = {"arriba": 8, "centro": 5, "abajo": 2}.get(estilo.posicion, 2)
    borde_estilo = 3 if estilo.caja else 1
    margen_v = max(0, int(round(alto * max(0.0, estilo.margen) / 100.0)))
    margen_h = max(16, int(round(ancho * 0.05)))

    color_primario = _color_ass(estilo.color, 1.0)
    color_contorno = _color_ass(estilo.color_contorno, 1.0)
    color_caja = _color_ass(estilo.caja_color, estilo.caja_opacidad)

    cabecera = [
        "[Script Info]",
        "; Generado por Studio Cut",
        "ScriptType: v4.00+",
        f"PlayResX: {ancho}",
        f"PlayResY: {alto}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "YCbCr Matrix: TV.709",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, "
        "ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding",
        (
            f"Style: Default,{fuente},{tamano_px},{color_primario},&H000000FF,"
            f"{color_contorno},{color_caja},{'-1' if estilo.negrita else '0'},"
            f"0,0,0,100,100,0,0,{borde_estilo},{borde_px:.2f},{sombra_px:.2f},"
            f"{alineacion},{margen_h},{margen_h},{margen_v},1"
        ),
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    eventos: list[str] = []
    for cue in sorted(cues, key=lambda c: float(c.get("inicio", 0.0))):
        normal = _normalizar_cue(cue.get("inicio", 0), cue.get("fin", 0), cue.get("texto", ""))
        if not normal:
            continue
        lineas = envolver_texto(normal["texto"], estilo.max_caracteres)
        texto_ass = "\\N".join(lineas)
        texto_ass = texto_ass.replace("{", "\\{").replace("}", "\\}")
        eventos.append(
            f"Dialogue: 0,{formato_tiempo_ass(normal['inicio'])},"
            f"{formato_tiempo_ass(normal['fin'])},Default,,0,0,0,,{texto_ass}"
        )

    destino.write_text("\n".join(cabecera + eventos) + "\n", encoding="utf-8")
    return destino


# ------------------------------------------------------- Generar subtítulos
def cues_desde_capitulos(
    marcas: list[dict],
    duracion_total: float,
    duracion_cartel: float = 4.0,
) -> list[dict]:
    """Convierte las marcas de capítulo en subtítulos con su título."""
    cues: list[dict] = []
    marcas_ordenadas = sorted(marcas, key=lambda m: float(m.get("tiempo", 0.0)))
    for i, marca in enumerate(marcas_ordenadas):
        t_ini = max(0.0, float(marca.get("tiempo", 0.0)))
        if duracion_total > 0 and t_ini >= duracion_total:
            continue
        if i + 1 < len(marcas_ordenadas):
            sig_t = float(marcas_ordenadas[i + 1].get("tiempo", t_ini + duracion_cartel))
            t_fin = min(t_ini + duracion_cartel, max(t_ini + 0.5, sig_t - 0.2))
        else:
            t_fin = t_ini + duracion_cartel
            if duracion_total > 0:
                t_fin = min(t_fin, duracion_total)
        titulo = str(marca.get("titulo") or f"Capítulo {i + 1}").strip()
        cue = _normalizar_cue(t_ini, max(t_ini + 0.5, t_fin), titulo)
        if cue:
            cues.append(cue)
    return cues


def cues_desde_palabras(
    palabras: list[dict],
    max_palabras: int = 7,
    max_segundos: float = 3.5,
    max_caracteres: int = 42,
) -> list[dict]:
    """Agrupa palabras con tiempos en subtítulos legibles."""
    cues: list[dict] = []
    bloque: list[dict] = []

    def guardar() -> None:
        nonlocal bloque
        if not bloque:
            return
        t_ini = float(bloque[0].get("inicio", 0.0))
        t_fin = max(float(bloque[-1].get("fin", t_ini)), t_ini + 0.5)
        texto = " ".join(str(p.get("palabra", "")).strip() for p in bloque).strip()
        normal = _normalizar_cue(t_ini, t_fin, texto)
        if normal:
            cues.append(normal)
        bloque = []

    for palabra in palabras:
        if not str(palabra.get("palabra", "")).strip():
            continue
        if not bloque:
            bloque.append(palabra)
            continue

        t_ini_bloque = float(bloque[0].get("inicio", 0.0))
        t_fin_ultima = float(bloque[-1].get("fin", t_ini_bloque))
        gap = float(palabra.get("inicio", 0.0)) - t_fin_ultima
        texto_previo = " ".join(str(p.get("palabra", "")) for p in bloque)
        texto_nuevo = f"{texto_previo} {palabra.get('palabra', '')}".strip()

        if (
            len(bloque) >= max_palabras
            or float(palabra.get("fin", 0.0)) - t_ini_bloque >= max_segundos
            or gap > 0.7
            or len(texto_nuevo) > max(10, max_caracteres)
        ):
            guardar()

        bloque.append(palabra)

    guardar()
    return cues


# --------------------------------------------------------- Compatibilidad
def generar_srt_capitulos(
    marcas: list[dict],
    duracion_total: float,
    ruta_salida: str | Path,
    duracion_cartel: float = 4.0,
) -> Path:
    """Genera un SRT con los títulos de los capítulos (API histórica)."""
    return escribir_srt(
        cues_desde_capitulos(marcas, duracion_total, duracion_cartel), ruta_salida
    )


def generar_srt_transcripcion(
    palabras: list[dict],
    ruta_salida: str | Path,
    max_palabras: int = 7,
    max_segundos: float = 3.5,
) -> Path:
    """Genera un SRT agrupando palabras de la transcripción (API histórica)."""
    return escribir_srt(
        cues_desde_palabras(palabras, max_palabras, max_segundos), ruta_salida
    )


# ------------------------------------------------------------- FFmpeg
def _escapar_ruta(ruta: str | Path) -> str:
    """Ruta segura para el filtro subtitles (Windows incluido)."""
    p = Path(ruta).resolve().as_posix()
    return p.replace("'", "\\'").replace(":", "\\:")


def preparar_filtro_subtitulos(
    ruta_srt: str | Path,
    tamano_fuente: int = 22,
    color_primario: str = "&H00FFFFFF",
    color_borde: str = "&H00000000",
    estilo: EstiloSubtitulos | None = None,
) -> str:
    """Devuelve el filtro ``subtitles`` para un SRT (compatibilidad)."""
    if estilo is not None:
        tamano_fuente = max(10, int(round(estilo.tamano * 2.88)))
        color_primario = _color_ass(estilo.color)
        color_borde = _color_ass(estilo.color_contorno)
        alineacion = {"arriba": 8, "centro": 5, "abajo": 2}.get(estilo.posicion, 2)
        fuerza = (
            f"FontName={estilo.fuente},FontSize={tamano_fuente},"
            f"PrimaryColour={color_primario},OutlineColour={color_borde},"
            f"BorderStyle={'3' if estilo.caja else '1'},"
            f"Outline={estilo.contorno:.2f},Shadow={estilo.sombra:.2f},"
            f"Alignment={alineacion},MarginV={int(estilo.margen)}"
        )
    else:
        fuerza = (
            f"FontSize={tamano_fuente},"
            f"PrimaryColour={color_primario},"
            f"OutlineColour={color_borde},"
            "BorderStyle=3,"
            "Outline=2,"
            "Shadow=1,"
            "MarginV=25"
        )
    return f"subtitles='{_escapar_ruta(ruta_srt)}':force_style='{fuerza}'"


def preparar_filtro_ass(ruta_ass: str | Path) -> str:
    """Filtro ``subtitles`` para un ASS propio (el estilo va dentro del ASS)."""
    return f"subtitles='{_escapar_ruta(ruta_ass)}'"


# ------------------------------------------------------------- Consulta
def cue_activo(cues: list[dict], tiempo: float) -> dict | None:
    """Devuelve el subtítulo visible en ``tiempo`` (o None)."""
    if not cues:
        return None
    ordenados = sorted(cues, key=lambda c: float(c.get("inicio", 0.0)))
    inicios = [float(c.get("inicio", 0.0)) for c in ordenados]
    pos = bisect.bisect_right(inicios, float(tiempo)) - 1
    if pos < 0:
        return None
    # Buscar hacia atrás por si hay solapamientos
    for i in range(pos, max(-1, pos - 8), -1):
        cue = ordenados[i]
        if float(cue.get("inicio", 0.0)) <= tiempo < float(cue.get("fin", 0.0)):
            return cue
    return None
