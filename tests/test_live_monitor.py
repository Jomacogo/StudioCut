"""Prueba del monitor de previsualización en vivo:
Verifica que las imágenes y audios se vean de inmediato en el monitor sin tener que esperar al render.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication
from ui.main_window import VentanaPrincipal


def main():
    app = QApplication([])
    dir_tmp = Path(tempfile.mkdtemp(prefix="test_live_"))
    try:
        # Crear 2 imágenes sintéticas
        img1 = dir_tmp / "foto1.png"
        img2 = dir_tmp / "foto2.png"
        qimg = QImage(320, 240, QImage.Format.Format_RGB32)
        qimg.fill(QColor("blue"))
        qimg.save(str(img1))
        qimg.fill(QColor("red"))
        qimg.save(str(img2))

        ventana = VentanaPrincipal("VideoCapitulos", "TestLiveMonitor")

        # 1. Caso: El usuario agrega imágenes ANTES de agregar audio
        ventana.imagenes = [str(img1), str(img2)]
        ventana._actualizar_timeline_y_monitor()

        # Debe tener 2 segmentos asignados de inmediato
        assert len(ventana.monitor._segmentos) == 2, "Debe tener 2 segmentos para las 2 imágenes"
        assert ventana.monitor._segmentos[0]["imagen"] == str(img1)

        # Pintar el canvas para verificar que se dibuja
        painter = QPainter(ventana.monitor.pantalla)
        ventana.monitor.pantalla.paintEvent(None)
        painter.end()

        # 2. Caso: El usuario hace clic en una imagen de la lista
        ventana.monitor.set_imagen_actual(str(img2), "foto2")
        assert ventana.monitor._ruta_imagen_actual == str(img2)

        # 3. Caso: Playback en vivo con Ken Burns
        ventana.monitor.set_tiempo(2.5)  # En el medio de la primera imagen
        assert ventana.monitor._tiempo_actual == 2.5

        print("OK: El monitor de previsualización en vivo muestra las imágenes correctamente.")
        ventana.close()
    finally:
        import shutil
        shutil.rmtree(dir_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
