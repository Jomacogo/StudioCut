"""Render corto real con cada efecto y subtítulos quemados (requiere FFmpeg)."""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.effects import TIPOS_EFECTO, ConfigEfecto
from core.renderer import (
    ConfigRender, construir_comando_un_audio, ejecutar_ffmpeg,
)
from core.subtitles import EstiloSubtitulos

DURACION = 4.0
MARCAS = [0.0, 2.0]
CUE = {"inicio": 0.2, "fin": 2.0, "texto": "Subtítulo de prueba con efecto"}


def _ffmpeg_disponible() -> bool:
    return shutil.which("ffmpeg") is not None


def _crear_medios(dir_tmp: Path) -> tuple[str, list[str]]:
    audio = dir_tmp / "audio.mp3"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={DURACION}",
        "-codec:a", "libmp3lame", str(audio),
    ], check=True, capture_output=True)
    imagenes = []
    for i, color in enumerate(("red", "green")):
        ruta = dir_tmp / f"img{i}.png"
        subprocess.run([
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
            "-i", f"color=c={color}:size=800x450:duration=1",
            "-frames:v", "1", str(ruta),
        ], check=True, capture_output=True)
        imagenes.append(str(ruta))
    return str(audio), imagenes


def main() -> None:
    if not _ffmpeg_disponible():
        print("SKIP: FFmpeg no está disponible")
        return
    dir_tmp = Path(tempfile.mkdtemp(prefix="prueba_efectos_"))
    try:
        audio, imagenes = _crear_medios(dir_tmp)
        for tipo in TIPOS_EFECTO:
            cfg = ConfigRender(
                ancho=640, alto=360, fps=25, transicion_seg=0.4,
                efecto=ConfigEfecto(tipo=tipo, intensidad=0.15, distancia=0.3),
                # El segundo clip usa un efecto propio distinto al global
                efectos_clip={1: ConfigEfecto(tipo="pulse", intensidad=0.2,
                                              velocidad=1.0).a_dict()},
                subtitulos=[dict(CUE)],
                estilo_subtitulos=EstiloSubtitulos(tamano=8.0, contorno=2.0),
                quemar_subtitulos=True,
            )
            salida = dir_tmp / f"efecto_{tipo}.mp4"
            cmd = construir_comando_un_audio(
                imagenes, audio, MARCAS, DURACION, str(salida), cfg)
            ejecutar_ffmpeg(cmd, DURACION, lambda p: None, lambda m: None)
            cfg.limpiar_temporales()
            assert salida.exists() and salida.stat().st_size > 0, f"Render vacío: {tipo}"
            print(f"OK: efecto {tipo} renderizado")
        print("=== RENDER DE TODOS LOS EFECTOS + SUBTÍTULOS OK ===")
    finally:
        shutil.rmtree(dir_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
