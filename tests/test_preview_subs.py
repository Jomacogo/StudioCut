"""Regresión: los subtítulos se ven en el monitor sin tapar el fotograma.

Antes el pincel blanco del subtítulo se filtraba al borde del marco y
rellenaba todo el vídeo (cuadro blanco/negro que ocultaba imagen y texto).
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from PyQt6.QtCore import QRectF, QSettings
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtWidgets import QApplication

from core.subtitles import EstiloSubtitulos
from ui.main_window import VentanaPrincipal

ORG, CFG = "VideoCapitulos", "TestPreviewSubs"


def imagen_de_prueba(destino: Path) -> str:
    img = QImage(1280, 720, QImage.Format.Format_RGB32)
    p = QPainter(img)
    p.fillRect(0, 0, 1280, 720, QColor("#3b82f6"))
    p.fillRect(0, 0, 640, 360, QColor("#f97316"))
    p.fillRect(640, 360, 640, 360, QColor("#22c55e"))
    p.end()
    img.save(str(destino))
    return str(destino)


def rect_pantalla(w, h) -> QRectF:
    aspect = 16.0 / 9.0
    w_obj = w - 16
    h_obj = w_obj / aspect
    if h_obj > h - 16:
        h_obj = h - 16
        w_obj = h_obj * aspect
    return QRectF((w - w_obj) / 2.0, (h - h_obj) / 2.0, w_obj, h_obj)


def blancos(img: QImage) -> int:
    total = 0
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            c = img.pixelColor(x, y)
            if c.red() > 240 and c.green() > 240 and c.blue() > 240:
                total += 1
    return total


def capturar(w) -> QImage:
    w.monitor.set_tiempo(1.0)
    QApplication.processEvents()
    w.monitor.pantalla.repaint()
    QApplication.processEvents()
    return w.monitor.pantalla.grab().toImage()


def main() -> None:
    QSettings(ORG, CFG).clear()
    app = QApplication([])
    w = VentanaPrincipal(ORG, CFG)
    w.resize(1280, 850)
    w.show()
    app.processEvents()

    tmp = Path(tempfile.mkdtemp(prefix="preview_subs_"))
    ruta = imagen_de_prueba(tmp / "fondo.png")
    w.imagenes = [ruta]
    w.combo_modo.setCurrentIndex(0)
    w._actualizar_timeline_y_monitor()

    w.subtitulos = [{"inicio": 0.0, "fin": 5.0, "texto": "SUBTITULO VISIBLE"}]
    w.estilo_subtitulos = EstiloSubtitulos(
        tamano=8.0, contorno=1.0, caja=False, posicion="centro", color="#FFFFFF")
    w._refrescar_tabla_subtitulos()
    w._actualizar_subtitulos_monitor()

    img = capturar(w)
    con = blancos(img)

    pantalla = w.monitor.pantalla
    rect = rect_pantalla(pantalla.width(), pantalla.height())
    centro = img.pixelColor(
        int(rect.x() + rect.width() * 0.25),
        int(rect.y() + rect.height() * 0.25),
    )
    assert centro.name() != "#ffffff", "El subtítulo cubrió todo el vídeo con blanco"
    assert centro.lightness() > 60, f"El vídeo quedó tapado: {centro.name()}"

    w.chk_sub_mostrar.setChecked(False)
    sin = blancos(capturar(w))

    print("pixeles blancos con subtitulo:", con, "sin subtitulo:", sin,
          "centro:", centro.name())
    assert con > sin + 20, "Los subtítulos no se ven en el monitor"

    # Con caja de fondo tampoco debe cubrir el fotograma completo
    w.chk_sub_mostrar.setChecked(True)
    w.estilo_subtitulos = EstiloSubtitulos(
        tamano=8.0, contorno=1.0, caja=True, caja_opacidad=1.0,
        posicion="centro", color="#FFFFFF", caja_color="#000000")
    w._actualizar_subtitulos_monitor()
    img_caja = capturar(w)
    centro_caja = img_caja.pixelColor(
        int(rect.x() + rect.width() * 0.25),
        int(rect.y() + rect.height() * 0.25),
    )
    assert centro_caja.name() != "#000000", "La caja del subtítulo cubrió el vídeo"
    assert centro_caja.lightness() > 60, f"El vídeo quedó tapado: {centro_caja.name()}"

    print("OK: los subtítulos se dibujan sin tapar el vídeo")
    w._proyecto_modificado = False
    w.close()
    QSettings(ORG, CFG).clear()


if __name__ == "__main__":
    main()
