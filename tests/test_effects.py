"""Pruebas del motor de movimiento (vista previa y expresiones FFmpeg)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.effects import (
    DIRECCIONES, TIPOS_EFECTO, ConfigEfecto, expresion_zoompan, transformacion,
)


def test_tipos_registrados():
    for clave in ("none", "zoom_in", "zoom_out", "pan", "kenburns", "pulse",
                  "shake", "alternar"):
        assert clave in TIPOS_EFECTO, f"Falta el efecto {clave}"
    assert "diagonal" in DIRECCIONES
    print("OK: tipos de efecto registrados")


def test_serializacion():
    cfg = ConfigEfecto(tipo="pulse", intensidad=0.2, distancia=0.1,
                       velocidad=1.5, direccion="arriba", suavizado=False)
    copia = ConfigEfecto.desde_dict(cfg.a_dict())
    assert copia == cfg, "La ida y vuelta a dict debe conservar los parámetros"
    invalido = ConfigEfecto.desde_dict({"tipo": "inexistente", "direccion": "rarísima"})
    assert invalido.tipo == "kenburns" and invalido.direccion == "diagonal"
    print("OK: serialización y valores inválidos")


def test_zoom_in_y_out():
    cfg = ConfigEfecto(tipo="zoom_in", intensidad=0.2, distancia=0.0, suavizado=False)
    z0, px0, py0 = transformacion(cfg, 0.0, 5.0)
    z1, px1, py1 = transformacion(cfg, 1.0, 5.0)
    assert abs(z0 - 1.0) < 1e-6
    assert abs(z1 - 1.2) < 1e-6
    assert (px0, py0) == (0.5, 0.5) and (px1, py1) == (0.5, 0.5)

    cfg_out = ConfigEfecto(tipo="zoom_out", intensidad=0.2, distancia=0.0,
                           suavizado=False)
    zo0, _, _ = transformacion(cfg_out, 0.0, 5.0)
    zo1, _, _ = transformacion(cfg_out, 1.0, 5.0)
    assert abs(zo0 - 1.2) < 1e-6 and abs(zo1 - 1.0) < 1e-6
    print("OK: zoom de acercamiento y alejamiento")


def test_pulso_empieza_y_termina_en_1():
    cfg = ConfigEfecto(tipo="pulse", intensidad=0.3, velocidad=0.8)
    z_ini, _, _ = transformacion(cfg, 0.0, 5.0)
    z_fin, _, _ = transformacion(cfg, 1.0, 5.0)
    z_medio, _, _ = transformacion(cfg, 0.125, 5.0)
    assert abs(z_ini - 1.0) < 1e-6 and abs(z_fin - 1.0) < 1e-6
    assert z_medio > z_ini, "El pulso debe alcanzar su máximo a mitad del ciclo"
    print("OK: pulso coherente")


def test_paneo_recorre_el_encuadre():
    cfg = ConfigEfecto(tipo="kenburns", intensidad=0.1, distancia=1.0,
                       direccion="derecha", suavizado=False)
    _, px0, py0 = transformacion(cfg, 0.0, 4.0)
    _, px1, py1 = transformacion(cfg, 1.0, 4.0)
    assert px0 < 0.5 < px1, "El paneo hacia la derecha debe moverse de izquierda a derecha"
    assert abs(py0 - 0.5) < 1e-6 and abs(py1 - 0.5) < 1e-6
    print("OK: paneo lateral")


def test_ninguno_es_estatico():
    cfg = ConfigEfecto(tipo="none")
    for p in (0.0, 0.25, 0.5, 1.0):
        assert transformacion(cfg, p, 3.0) == (1.0, 0.5, 0.5)
    print("OK: efecto nulo estático")


def test_expresiones_ffmpeg():
    for tipo in TIPOS_EFECTO:
        cfg = ConfigEfecto(tipo=tipo)
        z, x, y = expresion_zoompan(cfg, frames=150, fps=30, indice_clip=1)
        assert z and x and y, f"Expresión vacía para {tipo}"
        assert "iw-iw/zoom" in x and "ih-ih/zoom" in y
        for expr in (z, x, y):
            assert "\\" not in expr, "Las expresiones no deben contener barras invertidas"
            assert expr.count("(") == expr.count(")"), f"Paréntesis desbalanceados en {expr}"
    print("OK: expresiones zoompan para todos los efectos")


if __name__ == "__main__":
    test_tipos_registrados()
    test_serializacion()
    test_zoom_in_y_out()
    test_pulso_empieza_y_termina_en_1()
    test_paneo_recorre_el_encuadre()
    test_ninguno_es_estatico()
    test_expresiones_ffmpeg()
    print("=== TODOS LOS TESTS DE EFECTOS PASARON ===")
