"""Pruebas de integración: efectos por clip y subtítulos en la ventana principal."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QApplication

from ui.main_window import VentanaPrincipal

ORG, CFG = "VideoCapitulos", "TestEfectosSubsUI"


def main() -> None:
    app = QApplication([])
    # Empezar siempre con una sesión vacía (no arrastrar pruebas anteriores)
    QSettings(ORG, CFG).clear()
    QSettings(ORG, CFG + "2").clear()
    w = VentanaPrincipal(ORG, CFG)
    w2 = None
    try:
        w.imagenes = [r"C:\no\existe1.png", r"C:\no\existe2.png"]
        w._actualizar_timeline_y_monitor()
        assert len(w.monitor._segmentos) == 2
        assert w.monitor._segmentos[0]["efecto"]["tipo"] == "kenburns"

        # Efecto global
        w.cbo_efecto_ambito.setCurrentIndex(0)
        w.cbo_efecto.setCurrentIndex(w.cbo_efecto.findData("pulse"))
        w.spin_efecto_int.setValue(30)
        w.spin_efecto_vel.setValue(1.2)
        assert w.efecto_global.tipo == "pulse"
        assert abs(w.efecto_global.intensidad - 0.30) < 1e-9
        assert w.monitor._segmentos[0]["efecto"]["tipo"] == "pulse"

        # Override por clip
        w.cbo_efecto_ambito.setCurrentIndex(1)
        w._seleccionar_clip(1)
        w.cbo_efecto.setCurrentIndex(w.cbo_efecto.findData("shake"))
        assert w.efectos_clip[1]["tipo"] == "shake"
        assert w.efecto_global.tipo == "pulse"
        assert w.monitor._segmentos[1]["efecto"]["tipo"] == "shake"
        assert w.timeline._marcas[1]["efecto"].tipo == "shake"

        # Subtítulos automáticos desde la voz (sin pulsar nada más)
        assert w.chk_sub_auto.isChecked()
        w._palabras_detectadas([
            {"palabra": "hola", "inicio": 0.0, "fin": 0.4, "conf": 0.9},
            {"palabra": "mundo", "inicio": 0.4, "fin": 0.9, "conf": 0.9},
        ])
        assert len(w.subtitulos) == 1, w.subtitulos
        assert w.monitor._cues and w.monitor._cues[0]["texto"] == "hola mundo"
        assert "1 subtítulo" in w.lbl_subs_estado.text()
        # Al ser automáticos, una nueva transcripción los actualiza
        w._palabras_detectadas([
            {"palabra": "otra", "inicio": 0.0, "fin": 0.5, "conf": 0.9},
        ])
        assert w.subtitulos[0]["texto"] == "otra"
        # Si el usuario los edita, se conservan al volver a transcribir
        w.tabla_subs.item(0, 2).setText("editado a mano")
        w._palabras_detectadas([
            {"palabra": "tercera", "inicio": 0.0, "fin": 0.5, "conf": 0.9},
        ])
        assert w.subtitulos[0]["texto"] == "editado a mano"

        # Limpiar para probar la creación manual
        w.subtitulos = []
        w._refrescar_tabla_subtitulos()
        w._actualizar_subtitulos_monitor()

        # Subtítulos
        w.monitor.set_tiempo(1.0)
        w._anadir_subtitulo()
        assert len(w.subtitulos) == 1
        assert w.monitor._cues, "El monitor debe recibir los subtítulos"
        # Pintar con subtítulos y caja de fondo para validar el render en vivo
        w.chk_caja_sub.setChecked(True)
        w.monitor.pantalla.repaint()
        w.spin_tam_sub.setValue(7.0)
        assert abs(w.estilo_subtitulos.tamano - 7.0) < 1e-9

        # Persistencia del proyecto
        datos = w._empaquetar_proyecto()
        assert datos["efecto"]["tipo"] == "pulse"
        assert datos["efectos_clip"]["1"]["tipo"] == "shake"
        assert len(datos["subtitulos"]) == 1

        w2 = VentanaPrincipal(ORG, CFG + "2")
        w2._desempaquetar_proyecto(datos)
        assert w2.efecto_global.tipo == "pulse"
        assert w2.efectos_clip[1]["tipo"] == "shake"
        assert len(w2.subtitulos) == 1
        assert abs(w2.estilo_subtitulos.tamano - 7.0) < 1e-9
        assert w2.monitor._segmentos[1]["efecto"]["tipo"] == "shake"

        # Subtítulos automáticos de capítulo en modo Varios audios
        import ui.main_window as mw
        original = mw.duracion_audio
        mw.duracion_audio = lambda _ruta: 10.0
        try:
            w.subtitulos = []
            w._subs_auto = False
            w.palabras_transcripcion = []
            w.audios = [r"C:\fake\intro.mp3", r"C:\fake\capitulo1.mp3"]
            w.combo_modo.setCurrentIndex(0)
            w._actualizar_timeline_y_monitor()
            assert [c["texto"] for c in w.subtitulos] == ["intro", "capitulo1"]
            assert w.monitor._cues, "Los subtítulos de capítulo deben verse en el monitor"
        finally:
            mw.duracion_audio = original

        # Proyecto nuevo limpia efectos y subtítulos
        w2._proyecto_modificado = False
        w2._nuevo_proyecto()
        assert w2.efecto_global.tipo == "kenburns"
        assert w2.efectos_clip == {}
        assert w2.subtitulos == []

        print("OK: efectos por clip y subtítulos integrados en la ventana principal")
    finally:
        for ventana in (w2, w):
            if ventana is not None:
                ventana._proyecto_modificado = False
                ventana.close()
        QSettings(ORG, CFG).clear()
        QSettings(ORG, CFG + "2").clear()


if __name__ == "__main__":
    main()
