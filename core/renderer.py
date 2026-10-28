"""Renderizador: construye y ejecuta el comando ffmpeg.

Un segmento = (audio, imagen). Cada imagen cubre exactamente la duración de
su audio, con efecto Ken Burns (zoom in/out alternado) y transición
(xfade + acrossfade) en cada cambio de capítulo.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from core.effects import ConfigEfecto, expresion_zoompan
from core.media import ruta_ffmpeg
from core.subtitles import (
    EstiloSubtitulos, escribir_ass, preparar_filtro_ass,
    preparar_filtro_subtitulos,
)

FPS = 30

ESTILOS = ["fade", "slideleft", "circleopen", "wipeleft", "smoothup", "zoomin"]

# Patrones de subtítulos soportados
SUBTITLE_PATTERNS = ["srt", "vtt"]  # SubRip y WebVTT


class ErrorRender(Exception):
    pass


class ErrorSubtitle(Exception):
    pass


@dataclass
class ConfigRender:
    ancho: int = 1920
    alto: int = 1080
    fps: int = FPS
    transicion_seg: float = 1.0
    ken_burns: float = 0.12  # compatibilidad: intensidad histórica
    estilo_transicion: str = "fade"  # "fade" o "variado"
    subtitulo_path: str | None = None  # ruta opcional a archivo SRT/ASS/VTT
    efecto: ConfigEfecto = field(default_factory=ConfigEfecto)
    efectos_clip: dict[int, dict] = field(default_factory=dict)
    subtitulos: list[dict] = field(default_factory=list)
    estilo_subtitulos: EstiloSubtitulos = field(default_factory=EstiloSubtitulos)
    quemar_subtitulos: bool = True
    _archivo_subtitulo: str | None = field(default=None, repr=False, compare=False)

    # ------------------------------------------------------------ Auxiliares
    def efecto_de(self, indice: int) -> ConfigEfecto:
        """Efecto del clip `indice` (override por clip o global)."""
        datos = self.efectos_clip.get(indice)
        if datos is None:
            datos = self.efectos_clip.get(str(indice))  # JSON guarda claves str
        return ConfigEfecto.desde_dict(datos) if datos else self.efecto

    def preparar_archivo_subtitulos(self) -> str | None:
        """Escribe un ASS temporal desde los cues y devuelve su ruta."""
        if not self.quemar_subtitulos:
            return None
        if self.subtitulos:
            if self._archivo_subtitulo and Path(self._archivo_subtitulo).exists():
                return self._archivo_subtitulo
            fd, ruta = tempfile.mkstemp(prefix="studiocut_subs_", suffix=".ass")
            os.close(fd)
            try:
                escribir_ass(
                    self.subtitulos, ruta, self.estilo_subtitulos,
                    self.ancho, self.alto,
                )
            except Exception:
                try:
                    os.unlink(ruta)
                except OSError:
                    pass
                return None
            self._archivo_subtitulo = ruta
            return ruta
        if self.subtitulo_path and Path(self.subtitulo_path).exists():
            return self.subtitulo_path
        return None

    def limpiar_temporales(self) -> None:
        """Borra el ASS temporal generado para el render."""
        if self._archivo_subtitulo:
            try:
                os.unlink(self._archivo_subtitulo)
            except OSError:
                pass
            self._archivo_subtitulo = None


def _filtro_subtitulos(ruta: str | Path, cfg: ConfigRender) -> str:
    """Filtro `subtitles` según el formato (ASS propio o SRT/VTT externo)."""
    if Path(ruta).suffix.lower() == ".ass":
        return preparar_filtro_ass(ruta)
    return preparar_filtro_subtitulos(ruta, estilo=cfg.estilo_subtitulos)


def _cadena_imagen(indice: int, duracion: float, cfg: ConfigRender) -> str:
    """Cadena de escala + zoompan para una imagen con su efecto."""
    frames = max(int(round(duracion * cfg.fps)), 1)
    efecto = cfg.efecto_de(indice)
    z, x, y = expresion_zoompan(efecto, frames, cfg.fps, indice)
    w2, h2 = cfg.ancho * 2, cfg.alto * 2
    return (
        "[{i}:v]scale={w2}:{h2}:force_original_aspect_ratio=increase,"
        "crop={w2}:{h2},"
        "zoompan=z='{z}':x='{x}':y='{y}'"
        ":d={f}:s={ancho}x{alto}:fps={fps},setsar=1[v{i}]"
    ).format(
        i=indice, w2=w2, h2=h2, z=z, x=x, y=y, f=frames,
        ancho=cfg.ancho, alto=cfg.alto, fps=cfg.fps,
    )



def _estilografo(i: int, cfg: ConfigRender) -> str:
    if cfg.estilo_transicion == "fade":
        return "fade"
    return ESTILOS[(i - 1) % len(ESTILOS)]


def construir_filtro(
    duraciones: list[float], cfg: ConfigRender
) -> tuple[str, str, str, str]:
    """Devuelve (filter_complex, ref_video, ref_audio, ruta_total).

    Las salidas se representan como [vX] y [aX]; los videos intermedios
    del xfade como [xk] y los audios del acrossfade como [yk].
    `ruta_total` es la duración final esperada (suma - transciciones).
    """
    n = len(duraciones)
    t = cfg.transicion_seg
    if n == 0:
        raise ErrorRender("No hay segmentos para renderizar.")
    if n > 1 and t <= 0:
        raise ErrorRender("La duración de la transición debe ser > 0 con más de un audio.")

    vin: list[str] = []
    ain: list[str] = []
    for i, d in enumerate(duraciones):
        vin.append(_cadena_imagen(i, d, cfg))
        ain.append(
            f"[{i + n}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
            f"channel_layouts=stereo,apad=whole_dur={d:.3f}[a{i}]"
        )
    cadena = vin + ain

    if n == 1:
        ruta_total = duraciones[0]
        return ";\n".join(cadena), "[v0]", "[a0]", ruta_total

    offsets = []
    acum = 0.0
    for j in range(n):
        acum += duraciones[j]
        if j < n - 1:
            offsets.append(acum - (j + 1) * t)

    vx_prev = "[v0]"
    ax_prev = "[a0]"
    for j in range(n - 1):
        trans = _estilografo(j + 1, cfg)
        off_j = offsets[j]
        vx_str = f"[v{j + 1}]"
        ax_str = f"[a{j + 1}]"
        vx_out = f"[vx{j + 1}]"
        ax_out = f"[ax{j + 1}]"
        cadena.append(
            f"{vx_prev}{vx_str}xfade=transition={trans}"
            f":duration={t}:offset={off_j:.3f}{vx_out}"
        )
        cadena.append(
            f"{ax_prev}{ax_str}acrossfade=d={t}{ax_out}"
        )
        vx_prev = vx_out
        ax_prev = ax_out

    ruta_total = sum(duraciones) - t * (n - 1)
    return ";\n".join(cadena), vx_prev, ax_prev, ruta_total

def construir_comando_un_audio(
    imagen: list[str],
    audio: str,
    marcas: list[float],
    duracion_total: float,
    salida: str,
    cfg: ConfigRender,
    limitar_seg: float | None = None,
) -> list[str]:
    """Video de un único audio continuo con imágenes que cambian en las marcas.

    `marcas` son los tiempos (s) donde empieza cada capítulo; la primera
    imagen va desde 0. El audio corre ENTERO sin cortes: el audio total es
    `duracion_total` y el video dura lo mismo. En cada marca se aplica la
    transición (empieza en la marca y termina T segundos después).
    Siempre se usa 1 solo input de audio (índice N = número de imágenes).
    """
    if not imagen:
        raise ErrorRender("No hay imágenes para renderizar.")
    if not marcas:
        raise ErrorRender(
            "No hay marcas de capítulos: ejecuta «Detectar capítulos» o añádelas a mano."
        )
    D = min(duracion_total, limitar_seg) if limitar_seg else duracion_total
    limpias = [0.0] + sorted({m for m in marcas if 0.0 < m < D - 0.5})
    n = len(limpias)
    if len(imagen) < n:
        raise ErrorRender(
            f"Faltan imágenes: {n} marcas pero solo {len(imagen)} imágenes."
        )
    imagen = imagen[:n]

    t = cfg.transicion_seg if n > 1 else 0.0
    if n > 1:
        gap_min = min(limpias[i + 1] - limpias[i] for i in range(n - 1))
        if gap_min < t:
            t = max(gap_min, 0.2)

    # Duraciones de cada segmento tal que el video complete en D:
    #   D0 = m1 + T · Di (1..n-2) = m_(i+1) - m_i + T · Dúltimo = D - m_último
    dur_seg: list[float] = []
    for i in range(n):
        if n == 1:
            dur_seg.append(D)
        elif i == 0:
            dur_seg.append(limpias[1] + t)
        elif i < n - 1:
            dur_seg.append((limpias[i + 1] - limpias[i]) + t)
        else:
            dur_seg.append(D - limpias[-1])

    vin = [_cadena_imagen(i, d, cfg) for i, d in enumerate(dur_seg)]
    cadena = vin + [
        "[{n}:a]aformat=sample_fmts=fltp:sample_rates=48000:"
        "channel_layouts=stereo[aout]".format(n=n)
    ]

    if n == 1:
        vref, aref = "[v0]", "[aout]"
    else:
        vx_prev = "[v0]"
        for j in range(n - 1):
            trans = _estilografo(j + 1, cfg)
            vx_str = f"[v{j + 1}]"
            vx_out = f"[vx{j + 1}]"
            cadena.append(
                f"{vx_prev}{vx_str}xfade=transition={trans}"
                f":duration={t}:offset={limpias[j + 1]:.3f}{vx_out}"
            )
            vx_prev = vx_out
        vref, aref = vx_prev, "[aout]"

    # Quemar subtítulos (ASS generado desde los cues o archivo externo)
    ruta_subs = cfg.preparar_archivo_subtitulos()
    if ruta_subs:
        sub_filter = _filtro_subtitulos(ruta_subs, cfg)
        cadena.append(f"{vref}{sub_filter}[vsub]")
        vref = "[vsub]"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
           "-progress", "pipe:1", "-stats"]
    if not Path(audio).exists():
        raise ErrorRender(f"El audio no existe: {audio}")
    for p in imagen:
        cmd += ["-i", p]
    # el único audio va SIEMPRE al final de los inputs (índice n)
    cmd += ["-i", audio]
    if limitar_seg:
        cmd += ["-t", "{:.3f}".format(limitar_seg)]
    cmd += [
        "-filter_complex", ";\n".join(cadena),
        "-map", vref, "-map", aref,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        salida,
    ]
    return cmd


def construir_comando(
    imagenes: list[str],
    audios: list[str],
    duraciones: list[float],
    salida: str,
    cfg: ConfigRender,
    limitar_seg: float | None = None,
) -> list[str]:
    """Construye la línea completa ffmpeg como lista de argv.

    `limitar_seg` permite adelantar un render corto para prueba de estilo:
    recorta cada audio a esa duración y agrega -t a la salida.
    """
    if limitar_seg:
        duraciones = [min(d, limitar_seg) for d in duraciones]
    filtro, vref, aref, _total = construir_filtro(duraciones, cfg)

    # Quemar subtítulos (ASS generado desde los cues o archivo externo)
    ruta_subs = cfg.preparar_archivo_subtitulos()
    if ruta_subs:
        sub_filter = _filtro_subtitulos(ruta_subs, cfg)
        filtro = f"{filtro};\n{vref}{sub_filter}[vsub]"
        vref = "[vsub]"

    cmd = ["ffmpeg", "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
           "-progress", "pipe:1", "-stats"]
    
    for p in imagenes:
        cmd += ["-i", p]
    for p in audios:
        cmd += ["-i", p]
    if limitar_seg:
        cmd += ["-t", "{:.3f}".format(limitar_seg)]
    cmd += [
        "-filter_complex", filtro,
        "-map", vref, "-map", aref,
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        salida,
    ]
    return cmd


def ejecutar_ffmpeg(
    cmd: list[str],
    duracion_total: float,
    al_progreso,
    al_log,
    cancelar: threading.Event | None = None,
    limitar_seg: float | None = None,
) -> None:
    """Ejecuta ffmpeg reportando progreso; lanza ErrorRender si falla o se cancela."""
    total = min(duracion_total, limitar_seg) if limitar_seg else duracion_total
    ruta = ruta_ffmpeg()
    argv = [ruta] + cmd[1:]
    # stderr a archivo temporal: leerlo por pipe podría bloquear si llena el buffer.
    import tempfile
    fich_err = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=fich_err,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        for linea in proc.stdout:  # type: ignore[union-attr]
            if cancelar is not None and cancelar.is_set():
                proc.terminate()
                raise ErrorRender("Render cancelado por el usuario.")
            linea = linea.strip()
            if linea.startswith("out_time_ms="):
                try:
                    seg = float(linea.split("=", 1)[1]) / 1_000_000
                    pct = min(int(seg * 100 / max(total, 0.01)), 99)
                    al_progreso(pct)
                except ValueError:
                    pass
            elif linea and "=" not in linea:
                # las líneas progreso (out_time=, speed=...) son key=value; solo
                # pasamos al log lo demás que no parezca par clave=valor.
                al_log(linea)
                # También intentar detectar cambios de capítulo en los logs de ffmpeg
                if "chapter" in linea.lower() or "time=" in linea.lower():
                    al_log(f"[Segmento] {linea}")
        codigo = proc.wait()
        if codigo != 0:
            fich_err.seek(0)
            cola = "\n".join(fich_err.read().strip().splitlines()[-12:])
            raise ErrorRender("ffmpeg terminó con código {}.\n{}".format(codigo, cola))
        al_progreso(100)
    finally:
        if proc.poll() is None:  # por seguridad si hubo excepción
            proc.kill()
        fich_err.close()