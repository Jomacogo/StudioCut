"""
Creador de videos por capítulos para YouTube.

Combina audios (intro, capítulo 1, 2...) con imágenes; en el punto exacto
donde cambia el audio (el capítulo) la imagen cambia con una transición,
cada imagen tiene efecto Ken Burns durante su tramo, y opcionalmente
sube el video final a YouTube.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from ui.main_window import VentanaPrincipal

BASE = Path(__file__).resolve().parent


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Creador de Videos por Capítulos")
    app.setStyle("Fusion")
    qss = BASE / "styles.qss"
    if qss.exists():
        app.setStyleSheet(qss.read_text(encoding="utf-8"))
    ventana = VentanaPrincipal()
    ventana.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
