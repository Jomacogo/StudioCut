"""Motor de movimiento para las imágenes (Ken Burns, zoom, paneo, pulso…).

Este módulo es la **única fuente de verdad** del movimiento: tanto el monitor
en vivo (QPainter) como el renderizador (zoompan de FFmpeg) calculan el mismo
estado a partir de `ConfigEfecto` y del progreso dentro del clip.

La transformación se expresa de forma normalizada:

* ``zoom``: factor de escala (1.0 = imagen completa).
* ``px`` / ``py``: posición del encuadre dentro de la imagen, de 0 a 1
  (0 = borde izquierdo/superior, 0.5 = centro, 1 = borde derecho/inferior).

Para FFmpeg se traduce a expresiones ``zoompan``:

    x = (iw - iw/zoom) * px
    y = (ih - ih/zoom) * py
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields

# Tipos de efecto disponibles en la interfaz (clave -> etiqueta)
TIPOS_EFECTO: dict[str, str] = {
    "none": "Sin movimiento",
    "zoom_in": "Zoom acercándose",
    "zoom_out": "Zoom alejándose",
    "pan": "Paneo lateral",
    "kenburns": "Ken Burns (zoom + recorrido)",
    "pulse": "Pulso (respiración)",
    "shake": "Sacudida",
    "alternar": "Alternar zoom in/out",
}

# Direcciones del recorrido
DIRECCIONES: dict[str, str] = {
    "centro": "Centro",
    "derecha": "Hacia la derecha",
    "izquierda": "Hacia la izquierda",
    "arriba": "Hacia arriba",
    "abajo": "Hacia abajo",
    "diagonal": "Diagonal",
}


def _limitar(valor: float, minimo: float = 0.0, maximo: float = 1.0) -> float:
    return max(minimo, min(maximo, float(valor)))


def _suavizar(p: float) -> float:
    """Curva ease-in-out cúbica (acelera y desacelera)."""
    p = _limitar(p)
    return p * p * (3.0 - 2.0 * p)


@dataclass
class ConfigEfecto:
    """Parámetros editables de un efecto de movimiento."""

    tipo: str = "kenburns"
    intensidad: float = 0.14          # 0..0.50 (14 % de zoom)
    distancia: float = 0.35           # 0..1 (recorrido del encuadre)
    velocidad: float = 0.6            # ciclos/segundo en pulso y sacudida
    direccion: str = "diagonal"       # ver DIRECCIONES
    suavizado: bool = True

    def a_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def desde_dict(cls, datos: dict | None) -> "ConfigEfecto":
        if not datos:
            return cls()
        nombres = {f.name for f in fields(cls)}
        limpio = {k: v for k, v in dict(datos).items() if k in nombres}
        if "tipo" in limpio and limpio["tipo"] not in TIPOS_EFECTO:
            limpio["tipo"] = "kenburns"
        if "direccion" in limpio and limpio["direccion"] not in DIRECCIONES:
            limpio["direccion"] = "diagonal"
        return cls(**limpio)

    # ------------------------------------------------------------------ Texto
    def nombre(self) -> str:
        return TIPOS_EFECTO.get(self.tipo, self.tipo)

    def resumen(self) -> str:
        """Etiqueta corta para la timeline y la interfaz."""
        if self.tipo == "none":
            return "Sin movimiento"
        partes = [self.nombre()]
        if self.tipo in ("pulse", "shake"):
            partes.append(f"{self.intensidad * 100:.0f}% · {self.velocidad:.2f} Hz")
        else:
            partes.append(f"{self.intensidad * 100:.0f}%")
            if self.tipo in ("pan", "kenburns") or (
                self.distancia > 0.001 and self.direccion != "centro"
            ):
                partes.append(f"recorrido {self.distancia * 100:.0f}%")
        return " · ".join(partes)


def _recorrido(p: float, distancia: float, direccion: str) -> tuple[float, float]:
    """Devuelve (px, py) normalizados para un recorrido de 0 a `distancia`."""
    px = py = 0.5
    if direccion == "centro" or distancia <= 0.0:
        return px, py

    amplitud = 0.5 * _limitar(distancia)
    delta = amplitud * (2.0 * p - 1.0)  # de -amplitud a +amplitud

    if direccion in ("derecha", "izquierda", "diagonal"):
        px = 0.5 + (delta if direccion != "izquierda" else -delta)
    if direccion in ("abajo", "arriba", "diagonal"):
        py = 0.5 + (delta if direccion != "arriba" else -delta)
    if direccion == "diagonal":
        # Diagonal de arriba-izquierda a abajo-derecha
        px = 0.5 + delta
        py = 0.5 + delta

    return _limitar(px), _limitar(py)


def transformacion(
    cfg: ConfigEfecto,
    progreso: float,
    duracion: float,
    indice_clip: int = 0,
) -> tuple[float, float, float]:
    """Estado del encuadre en un instante del clip.

    Args:
        cfg: parámetros del efecto.
        progreso: 0.0 al inicio del clip, 1.0 al final.
        duracion: duración del clip en segundos (para pulso/sacudida).
        indice_clip: índice del clip (para alternar).

    Returns:
        ``(zoom, px, py)``.
    """
    p = _limitar(progreso)
    e = _suavizar(p) if cfg.suavizado else p
    dur = max(0.05, float(duracion))
    intensidad = _limitar(cfg.intensidad, 0.0, 0.5)
    distancia = _limitar(cfg.distancia)
    direccion = cfg.direccion if cfg.direccion in DIRECCIONES else "diagonal"

    zoom = 1.0
    px = py = 0.5

    if cfg.tipo == "none":
        return 1.0, 0.5, 0.5

    if cfg.tipo == "zoom_in":
        zoom = 1.0 + intensidad * e
        px, py = _recorrido(e, distancia, direccion)
    elif cfg.tipo == "zoom_out":
        zoom = 1.0 + intensidad * (1.0 - e)
        px, py = _recorrido(e, distancia, direccion)
    elif cfg.tipo == "kenburns":
        zoom = 1.0 + intensidad * e
        px, py = _recorrido(e, distancia, direccion)
    elif cfg.tipo == "pan":
        zoom = 1.0 + max(intensidad, 0.02) + 0.02
        px, py = _recorrido(e, distancia, "derecha" if direccion == "centro" else direccion)
    elif cfg.tipo == "pulse":
        # Número entero de ciclos para que empiece y termine en zoom 1.0
        ciclos = max(1, int(round(max(0.05, cfg.velocidad) * dur)))
        zoom = 1.0 + intensidad * (0.5 - 0.5 * math.cos(2.0 * math.pi * ciclos * p))
    elif cfg.tipo == "shake":
        t = p * dur
        frec = max(0.05, cfg.velocidad)
        zoom = 1.0 + intensidad * 0.20
        amplitud = min(0.22, intensidad * 0.45)
        px = 0.5 + amplitud * math.sin(2.0 * math.pi * frec * t)
        py = 0.5 + amplitud * math.cos(2.0 * math.pi * frec * 1.37 * t)
        px, py = _limitar(px), _limitar(py)
    elif cfg.tipo == "alternar":
        if indice_clip % 2 == 0:
            zoom = 1.0 + intensidad * e
        else:
            zoom = 1.0 + intensidad * (1.0 - e)
    else:
        zoom = 1.0

    return max(1.0, zoom), px, py


# --------------------------------------------------------------- FFmpeg
def _expresion_progreso(frames: int, suavizado: bool) -> str:
    frames = max(1, int(frames))
    p = f"min(on/{frames},1)"
    if not suavizado:
        return p
    # ease-in-out: 3p² - 2p³
    return f"(3*pow({p},2)-2*pow({p},3))"


def _expresion_recorrido(progreso: str, distancia: float, direccion: str,
                         eje: str) -> str:
    amplitud = 0.5 * _limitar(distancia)
    if amplitud <= 0.0 or direccion == "centro":
        return "0.5"

    signo = 1.0
    if direccion == "izquierda" and eje == "x":
        signo = -1.0
    if direccion == "arriba" and eje == "y":
        signo = -1.0

    if direccion == "diagonal":
        signo = 1.0

    # 0.5 + signo * amplitud * (2p - 1)
    return f"(0.5{'+' if signo > 0 else '-'}{amplitud:.6f}*(2*{progreso}-1))"


def expresion_zoompan(
    cfg: ConfigEfecto,
    frames: int,
    fps: int,
    indice_clip: int = 0,
) -> tuple[str, str, str]:
    """Devuelve las expresiones ``(z, x, y)`` para el filtro zoompan."""
    frames = max(1, int(frames))
    dur = frames / max(1, int(fps))
    intensidad = _limitar(cfg.intensidad, 0.0, 0.5)
    distancia = _limitar(cfg.distancia)
    direccion = cfg.direccion if cfg.direccion in DIRECCIONES else "diagonal"
    p = _expresion_progreso(frames, cfg.suavizado)

    zoom_expr = "1"
    px_expr = "0.5"
    py_expr = "0.5"
    recorrido = True

    if cfg.tipo == "none":
        recorrido = False
    elif cfg.tipo == "zoom_in":
        zoom_expr = f"(1+{intensidad:.6f}*{p})"
    elif cfg.tipo == "zoom_out":
        zoom_expr = f"(1+{intensidad:.6f}*(1-{p}))"
    elif cfg.tipo == "kenburns":
        zoom_expr = f"(1+{intensidad:.6f}*{p})"
    elif cfg.tipo == "pan":
        base = max(intensidad, 0.02) + 0.02
        zoom_expr = f"{1.0 + base:.6f}"
        if direccion == "centro":
            direccion = "derecha"
    elif cfg.tipo == "pulse":
        ciclos = max(1, int(round(max(0.05, cfg.velocidad) * dur)))
        zoom_expr = (
            f"(1+{intensidad:.6f}*(0.5-0.5*cos(2*PI*{ciclos}*on/{frames})))"
        )
        recorrido = False
    elif cfg.tipo == "shake":
        frec = max(0.05, cfg.velocidad)
        amp = min(0.22, intensidad * 0.45)
        zoom_expr = f"{1.0 + intensidad * 0.20:.6f}"
        px_expr = (
            f"(0.5+{amp:.6f}*sin(2*PI*{frec * dur:.6f}*on/{frames}))"
        )
        py_expr = (
            f"(0.5+{amp:.6f}*cos(2*PI*{frec * dur * 1.37:.6f}*on/{frames}))"
        )
        recorrido = False
    elif cfg.tipo == "alternar":
        if indice_clip % 2 == 0:
            zoom_expr = f"(1+{intensidad:.6f}*{p})"
        else:
            zoom_expr = f"(1+{intensidad:.6f}*(1-{p}))"

    if recorrido:
        px_expr = _expresion_recorrido(p, distancia, direccion, "x")
        py_expr = _expresion_recorrido(p, distancia, direccion, "y")

    x_expr = f"(iw-iw/zoom)*{px_expr}"
    y_expr = f"(ih-ih/zoom)*{py_expr}"
    return zoom_expr, x_expr, y_expr
