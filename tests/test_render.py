"""Prueba E2E del renderizador: genera medios sintéticos, renderiza y verifica.

Uso:  python tests/test_render.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.media import duracion_audio
from core.renderer import ConfigRender, construir_comando, ejecutar_ffmpeg


def _ejecutar(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def crear_medios(dir_tmp: Path) -> tuple[list[str], list[str]]:
    audios, imagenes = [], []
    for i in range(3):  # intro, capitulo1, capitulo2
        nombre = "intro" if i == 0 else f"capitulo{i}"
        audio = dir_tmp / f"{nombre}.mp3"
        imagen = dir_tmp / f"{nombre}.png"
        if audio.exists():
            audio.unlink()
        if imagen.exists():
            imagen.unlink()
        _ejecutar([
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i",
            f"sine=frequency={330 + 110 * i}:duration={6 + 2 * i}",
            "-codec:a", "libmp3lame", str(audio),
        ])
        _ejecutar([
            "ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
            f"color=c={['red', 'green', 'blue'][i]}:size=1280x720:duration=1",
            "-frames:v", "1", str(imagen),
        ])
        audios.append(str(audio))
        imagenes.append(str(imagen))
    return audios, imagenes


def main() -> None:
    dir_tmp = Path(tempfile.mkdtemp(prefix="prueba_render_"))
    try:
        audios, imagenes = crear_medios(dir_tmp)
        duraciones = [duracion_audio(a) for a in audios]
        print("Duraciones:", [round(d, 2) for d in duraciones])

        cfg = ConfigRender(ancho=1920, alto=1080, transicion_seg=1.0)
        salida = str(dir_tmp / "video_final.mp4")
        cmd = construir_comando(imagenes, audios, duraciones, salida, cfg)
        ejecutar_ffmpeg(cmd, sum(duraciones) - cfg.transicion_seg * 2,
                        lambda p: print(f"  progreso {p}%") if p % 25 == 0 else None,
                        lambda m: print("  [ffmpeg]", m))
        dur_final = duracion_audio(salida)
        esperada = sum(duraciones) - 2 * cfg.transicion_seg
        print(f"Duracion final: {dur_final:.2f}s (esperada ~ {esperada:.2f}s)")
        assert abs(dur_final - esperada) < 3.0, "Duración inesperada"
        tamano = Path(salida).stat().st_size
        print(f"OK: {salida} ({tamano / 1024:.0f} KiB)")
    finally:
        shutil.rmtree(dir_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
