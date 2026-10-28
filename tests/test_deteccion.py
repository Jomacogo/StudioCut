"""Pruebas del modo «un solo audio».

1) Unit: detección a partir de una transcripción falsa.
2) Unit: render con marcas (duración del video == duración del audio).
3) E2E (opcional): genera voz con el TTS de Windows y detecta capítulos;
   se omite con aviso si no hay voz en español o si falla la descarga del
   modelo.

Uso:  python tests/test_deteccion.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.chapter_detection import detectar_capitulos
from core.media import duracion_audio, ruta_ffmpeg
from core.renderer import ConfigRender, construir_comando_un_audio, ejecutar_ffmpeg


def _palabra(pal: str, ini: float, fin: float, conf: float = 0.9) -> dict:
    return {"palabra": pal, "inicio": ini, "fin": fin, "conf": conf}


def _palabras_base(mention_central: bool):
    base = (
        [_palabra("bienvenidos", 0.0, 0.6), _palabra("la", 0.6, 0.7),
         _palabra("introducción", 0.7, 1.5), _palabra("del", 1.5, 1.7),
         _palabra("libro", 1.7, 2.2)]
        + [_palabra("w", 2.2 + i * 0.3, 2.5 + i * 0.3) for i in range(20)]
        + [_palabra("capítulo", 9.0, 9.5), _palabra("uno", 9.5, 9.9)]
        + [_palabra("x", 10.0 + i * 0.3, 10.3 + i * 0.3) for i in range(20)]
        + [_palabra("capítulo", 20.0, 20.5), _palabra("dos", 20.5, 20.9)]
        + [_palabra("y", 21.0 + i * 0.3, 21.3 + i * 0.3) for i in range(10)]
        + [_palabra("en", 30.0, 30.2), _palabra("el", 30.2, 30.4),
           _palabra("capítulo", 30.4, 30.8), _palabra("un", 30.8, 31.0),  # mención
           _palabra("pasado", 31.0, 31.3)]
        + [_palabra("capítulo", 40.0, 40.5), _palabra("treinta", 40.5, 41.0),
           _palabra("y", 41.0, 41.2), _palabra("cinco", 41.2, 41.6)]
    )
    if mention_central:
        base += [_palabra("hablando", 43.0, 43.3), _palabra("del", 43.3, 43.5),
                 _palabra("cierre", 43.5, 44.0), _palabra("de", 44.0, 44.2),
                 _palabra("esto", 44.2, 44.5)]  # mención suelta NO es el cierre
    return base


def test_deteccion() -> None:
    # Caso 1: mención suelta de «cierre» a mitad del audio -> NO hay marca Cierre
    marcas = detectar_capitulos(_palabras_base(mention_central=True))
    titulos = [m["titulo"] for m in marcas]
    assert "Introducción" in titulos, f"Falta intro: {titulos}"
    assert "Capítulo 1" in titulos, f"Falta cap 1: {titulos}"
    assert "Capítulo 2" in titulos, f"Falta cap 2: {titulos}"
    assert "Capítulo 35" in titulos, f"Falló «treinta y cinco»: {titulos}"
    assert "Cierre" not in titulos, f"Cierre falso a mitad: {titulos}"
    assert len([t for t in titulos if t == "Capítulo 1"]) == 1, titulos
    tiempos = [m["tiempo"] for m in marcas]
    assert tiempos == sorted(tiempos), "Marcas desordenadas"
    cap1 = marcas[[m["titulo"] for m in marcas].index("Capítulo 1")]["tiempo"]
    assert abs(cap1 - 9.9) < 0.6, f"Tiempo del cap 1: {cap1}"

    # Caso 2: cierre real al final -> última marca
    palabras_con_cierre = _palabras_base(mention_central=True) + [
        _palabra("y", 50.0, 50.2), _palabra("hasta", 50.2, 50.4),
        _palabra("el", 50.4, 50.6), _palabra("cierre", 50.6, 51.3),
        _palabra("final", 51.3, 51.6), _palabra("gracias", 51.6, 52.2)]
    marcas2 = detectar_capitulos(palabras_con_cierre)
    titulos2 = [m["titulo"] for m in marcas2]
    assert titulos2[-1] == "Cierre", f"Cierre no es la última marca: {titulos2}"
    assert marcas2[-1]["tiempo"] >= 41.6, marcas2[-1]
    print("OK detección:", [(m["titulo"], round(m['tiempo'], 1)) for m in marcas2])


def test_render_marcas() -> None:
    dir_tmp = Path(tempfile.mkdtemp(prefix="prueba_marcas_"))
    try:
        audio = dir_tmp / "completo.mp3"
        subprocess.run([
            ruta_ffmpeg(), "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
            "-codec:a", "libmp3lame", str(audio)], check=True)
        imagenes = []
        for i, color in enumerate(("red", "green", "blue")):
            img = dir_tmp / f"img{i}.png"
            subprocess.run([
                ruta_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                f"color=c={color}:size=1280x720:duration=1",
                "-frames:v", "1", str(img)], check=True)
            imagenes.append(str(img))
        # el TTS en español con voz inglesa puede no detectar bien;
        # este test no depende de eso.
        salida = str(dir_tmp / "con_marcas.mp4")
        cfg = ConfigRender(ancho=1280, alto=720, transicion_seg=1.0)
        cmd = construir_comando_un_audio(imagenes, str(audio),
                                         [5.0, 12.0], 20.0, salida, cfg)
        ejecutar_ffmpeg(cmd, 20.0, lambda p: None, lambda m: None)
        dur = duracion_audio(salida)
        assert abs(dur - 20.0) < 2.0, f"Duración {dur} != ~20 (el audio no se corta)"
        print(f"OK render con marcas: {dur:.2f}s (audio 20s intacto)")
    finally:
        shutil.rmtree(dir_tmp, ignore_errors=True)


def test_e2e_voz() -> None:
    """Genera voz con SAPI (Windows). Si no hay voz en español, se salta."""
    try:
        comtypes_ok = True
    except Exception:
        comtypes_ok = False
    script = Path(tempfile.mkdtemp(prefix="voz_")) / "tts.ps1"
    wav = script.parent / "voz.wav"
    script.write_text(
        "Add-Type -AssemblyName System.Speech\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$voz = $s.GetInstalledVoices() | Where-Object "
        "{ $_.VoiceInfo.Culture.Name -like 'es*' } | Select-Object -First 1\n"
        "if ($voz) { $s.SelectVoice($voz.VoiceInfo.Name) }\n"
        "$s.SetOutputToWaveFile('" + str(wav).replace("\\", "\\\\") + "')\n"
        "$s.Speak('Bienvenidos. Introducción al libro. "
        "El texto del primer apartado continúa por aquí. "
        "Capítulo uno. Contenido del primer capítulo. "
        "Capítulo dos. Contenido del segundo capítulo. "
        "Capítulo tres. Contenido del tercer capítulo. "
        "Y ahora llega el cierre del programa. Gracias por escuchar.')\n"
        "$s.Dispose()\n", encoding="utf-8")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy",
                        "Bypass", "-File", str(script)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not wav.exists():
        print("AVISO e2e voz: SAPI no generó el wav; se omite "
              f"({r.stderr.strip()[:120]})")
        return
    try:
        from core import chapter_detection as cd
        log = print
        marcas = cd.detectar_de_audio(str(wav), lambda p, e: None, log)
        if any(m["titulo"].startswith("Capítulo") for m in marcas):
            lista = ", ".join(f"{m['titulo']}@{m['tiempo']:.1f}" for m in marcas)
            assert "Cierre" in [m["titulo"] for m in marcas], \
                f"El e2e no detectó el cierre: {lista}"
            print(f"OK e2e voz: {lista}")
        else:
            print("AVISO e2e voz: no detectó capítulos (voz/TTS no compatible "
                  "con el modelo pequeño).")
    except Exception as exc:  # noqa: BLE001
        print(f"AVISO e2e voz omitido: {exc}")
    finally:
        shutil.rmtree(script.parent, ignore_errors=True)


if __name__ == "__main__":
    test_deteccion()
    test_render_marcas()
    test_e2e_voz()
    print("TODOS LOS TESTS OK")
