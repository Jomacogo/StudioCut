"""Prueba e2e del modo «texto → voz»: TTS de Windows + detección de capítulos.

Uso:  python tests/test_voz.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import voz_tts
from core import chapter_detection as cd

GUION = (
    "Bienvenidos. Introducción al programa. Empezamos con las noticias.\n\n"
    "Capítulo uno. El contenido del primer capítulo habla de almacenes.\n\n"
    "Capítulo dos. El segundo capítulo aborda otros temas interesantes.\n\n"
    "Capítulo tres. El tercer capítulo cierra el análisis de la serie.\n\n"
    "Y así llegamos al cierre del programa de hoy. Gracias por escuchar."
)


def main() -> None:
    dir_tmp = Path(tempfile.mkdtemp(prefix="prueba_voz_"))
    try:
        voces = voz_tts.listar_voces()
        print("Voces es- instaladas:", [v["nombre"] for v in voces])
        electa = voz_tts.elegir_voz()
        print("Voz elegida:", electa or "(por defecto del sistema)")

        wav = str(dir_tmp / "voz.wav")
        voz_tts.generar_voz(
            GUION, wav, voz=electa, velocidad=0, al_log=print)
        marcas = cd.detectar_de_audio(wav, lambda p, e: None, print)
        titulos = [m["titulo"] for m in marcas]
        assert any(t.startswith("Capítulo 1") for t in titulos), titulos
        assert any(t.startswith("Capítulo ") for t in titulos[:2]), titulos
        assert "Cierre" in titulos, f"El cierre no se detectó: {titulos}"
        assert titulos[-1] == "Cierre", f"El cierre no quedó al final: {titulos}"
        print("OK e2e voz->detección:", [(m["titulo"], round(m["tiempo"], 1)) for m in marcas])
    finally:
        import shutil
        shutil.rmtree(dir_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
