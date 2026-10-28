"""Test de generación y renderizado de subtítulos."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from core.subtitles import (
    EstiloSubtitulos,
    cue_activo,
    cues_desde_palabras,
    escribir_ass,
    escribir_srt,
    formato_tiempo_srt,
    generar_srt_capitulos,
    generar_srt_transcripcion,
    leer_srt,
    leer_vtt,
    preparar_filtro_subtitulos,
)


def test_formato_srt():
    assert formato_tiempo_srt(0.0) == "00:00:00,000"
    assert formato_tiempo_srt(1.5) == "00:00:01,500"
    assert formato_tiempo_srt(65.234) == "00:01:05,234"
    assert formato_tiempo_srt(3661.0) == "01:01:01,000"
    print("OK: formato_tiempo_srt")


def test_generar_srt_capitulos():
    with tempfile.TemporaryDirectory() as tmp:
        srt = Path(tmp) / "capitulos.srt"
        marcas = [
            {"titulo": "Introducción", "tiempo": 0.0},
            {"titulo": "Capítulo 1", "tiempo": 10.0},
            {"titulo": "Capítulo 2", "tiempo": 25.0},
            {"titulo": "Cierre", "tiempo": 50.0},
        ]
        res = generar_srt_capitulos(marcas, duracion_total=60.0, ruta_salida=srt, duracion_cartel=4.0)
        assert res.exists()
        contenido = res.read_text(encoding="utf-8")
        assert "Introducción" in contenido
        assert "Capítulo 1" in contenido
        assert "Capítulo 2" in contenido
        assert "Cierre" in contenido
        assert "00:00:00,000 --> 00:00:04,000" in contenido
        assert "00:00:10,000 --> 00:00:14,000" in contenido
        print("OK: generar_srt_capitulos")


def test_generar_srt_transcripcion():
    with tempfile.TemporaryDirectory() as tmp:
        srt = Path(tmp) / "voz.srt"
        palabras = [
            {"palabra": "hola", "inicio": 0.5, "fin": 0.8},
            {"palabra": "amigos", "inicio": 0.9, "fin": 1.2},
            {"palabra": "de", "inicio": 1.3, "fin": 1.4},
            {"palabra": "youtube", "inicio": 1.5, "fin": 2.0},
        ]
        res = generar_srt_transcripcion(palabras, ruta_salida=srt)
        assert res.exists()
        contenido = res.read_text(encoding="utf-8")
        assert "hola amigos de youtube" in contenido
        print("OK: generar_srt_transcripcion")


def test_filtro_ffmpeg():
    filtro = preparar_filtro_subtitulos("C:/Videos/sub.srt")
    assert "subtitles=" in filtro
    assert "force_style=" in filtro
    print("OK: preparar_filtro_subtitulos")


def test_escribir_ass_con_estilo():
    with tempfile.TemporaryDirectory() as tmp:
        ruta = Path(tmp) / "salida.ass"
        estilo = EstiloSubtitulos(
            fuente="Arial", tamano=6.0, negrita=True, color="#FF0000",
            color_contorno="#00FF00", contorno=3.0, sombra=1.5,
            caja=True, caja_color="#0000FF", caja_opacidad=0.5,
            posicion="arriba", margen=6.0, max_caracteres=20,
        )
        cues = [
            {"inicio": 0.0, "fin": 2.0, "texto": "Una línea bastante larga para partir"},
            {"inicio": 3.0, "fin": 4.5, "texto": "Otra {con} llaves"},
        ]
        res = escribir_ass(cues, ruta, estilo, ancho=1280, alto=720)
        texto = res.read_text(encoding="utf-8")
        assert "PlayResX: 1280" in texto and "PlayResY: 720" in texto
        assert "Arial" in texto and "Style: Default" in texto
        assert texto.count("Dialogue:") == 2
        assert "Alignment" not in texto.split("[Events]")[1]
        # El tamaño se expresa en píxeles del video (6 % de 720 = 43 px)
        assert ",43," in texto
        # Las líneas largas se parten con \N
        assert "\\N" in texto
        print("OK: escribir_ass_con_estilo")


def test_cues_desde_palabras():
    palabras = [
        {"palabra": "uno", "inicio": 0.0, "fin": 0.4},
        {"palabra": "dos", "inicio": 0.4, "fin": 0.8},
        {"palabra": "tres", "inicio": 2.5, "fin": 2.9},
        {"palabra": "cuatro", "inicio": 3.0, "fin": 3.4},
    ]
    cues = cues_desde_palabras(palabras, max_palabras=10, max_segundos=5.0)
    assert len(cues) == 2, "El silencio de 1.7 s debe separar dos bloques"
    assert cues[0]["texto"] == "uno dos"
    assert cues[1]["texto"] == "tres cuatro"
    assert cues[0]["fin"] >= 0.8
    print("OK: cues_desde_palabras")


def test_cue_activo():
    cues = [
        {"inicio": 0.0, "fin": 1.0, "texto": "A"},
        {"inicio": 1.0, "fin": 2.0, "texto": "B"},
    ]
    assert cue_activo(cues, 0.5)["texto"] == "A"
    assert cue_activo(cues, 1.0)["texto"] == "B"
    assert cue_activo(cues, 2.5) is None
    assert cue_activo([], 1.0) is None
    print("OK: cue_activo")


def test_ida_y_vuelta_srt_vtt():
    with tempfile.TemporaryDirectory() as tmp:
        cues = [
            {"inicio": 0.25, "fin": 1.75, "texto": "Hola mundo"},
            {"inicio": 2.0, "fin": 3.5, "texto": "Segunda línea"},
        ]
        srt = escribir_srt(cues, Path(tmp) / "a.srt")
        leidos = leer_srt(srt)
        assert [c["texto"] for c in leidos] == ["Hola mundo", "Segunda línea"]
        assert abs(leidos[0]["inicio"] - 0.25) < 0.002
        assert abs(leidos[1]["fin"] - 3.5) < 0.002

        vtt = Path(tmp) / "a.vtt"
        vtt.write_text(
            "WEBVTT\n\n00:01.000 --> 00:02.000\n<b>Hola</b> mundo\n",
            encoding="utf-8",
        )
        leidos_vtt = leer_vtt(vtt)
        assert leidos_vtt == [{"inicio": 1.0, "fin": 2.0, "texto": "Hola mundo"}]
        print("OK: ida y vuelta SRT/VTT")


if __name__ == "__main__":
    test_formato_srt()
    test_generar_srt_capitulos()
    test_generar_srt_transcripcion()
    test_filtro_ffmpeg()
    test_escribir_ass_con_estilo()
    test_cues_desde_palabras()
    test_cue_activo()
    test_ida_y_vuelta_srt_vtt()
    print("=== TODOS LOS TESTS DE SUBTITULOS PASARON ===")
