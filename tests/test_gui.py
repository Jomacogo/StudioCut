"""Smoke test GUI offscreen: ventana, estado y emparejamiento.

Uso:  QT_QPA_PLATFORM=offscreen python tests/test_gui.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication

import test_render as generador


def main() -> None:
    from ui.main_window import VentanaPrincipal

    dir_tmp = Path(tempfile.mkdtemp(prefix="prueba_gui_"))
    try:
        audios, imagenes = generador.crear_medios(dir_tmp)
        app = QApplication([])
        ventana = VentanaPrincipal("VideoCapitulos", "TestGUI")
        ventana.audios = audios
        ventana._reordenar()
        ventana.imagenes = imagenes
        for r in imagenes:
            from PyQt6.QtGui import QIcon
            from PyQt6.QtWidgets import QListWidgetItem
            ventana.lista_imagenes.addItem(QListWidgetItem(QIcon(r), Path(r).name))
        ventana._refrescar_tabla()
        assert ventana.tabla.rowCount() == 3, "Deben verse 3 filas"
        # El orden de audios debe poner "intro" primero
        primer_item = ventana.tabla.item(0, 0).text()
        assert "intro" in primer_item.lower(), f"Orden incorrecto: {primer_item}"
        # Cada combo debe tener imagen asignada
        for fila in range(3):
            combo = ventana.tabla.cellWidget(fila, 1)
            assert combo.currentData(), f"Fila {fila + 1} sin imagen"
        print("OK GUI smoke: 3 filas, intro primero, imágenes emparejadas")
        ventana.close()
    finally:
        generador.shutil.rmtree(dir_tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
