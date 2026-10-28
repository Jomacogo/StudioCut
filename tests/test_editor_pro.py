"""Prueba completa de la nueva interfaz, motor de previsualización en vivo y proyectos .stcut."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication
from ui.main_window import VentanaPrincipal
from core.project import guardar_proyecto, cargar_proyecto, EXTENSION_PROYECTO


def main():
    app = QApplication([])
    ventana = VentanaPrincipal("VideoCapitulos", "TestEditorPro")
    
    # 1. Verificar carga de componentes NLE
    assert hasattr(ventana, "timeline"), "Falta el widget timeline"
    assert hasattr(ventana, "monitor"), "Falta el widget monitor"
    assert hasattr(ventana, "split_vertical"), "Falta split_vertical"
    assert hasattr(ventana, "split_horizontal"), "Falta split_horizontal"

    # 2. Probar cambio de modos
    for i in range(3):
        ventana.combo_modo.setCurrentIndex(i)
        ventana._aplicar_modo()

    # 3. Probar datos de timeline y marcas
    marcas_test = [
        {"titulo": "Introducción", "tiempo": 0.0},
        {"titulo": "Capítulo 1", "tiempo": 12.5},
        {"titulo": "Capítulo 2", "tiempo": 34.0},
        {"titulo": "Cierre", "tiempo": 58.0},
    ]
    ventana.marcas = marcas_test
    ventana.combo_modo.setCurrentIndex(1)  # Modo single audio
    ventana._refrescar_marcas()
    assert ventana.tabla_marcas.rowCount() == 4, "Debe tener 4 marcas"

    # 4. Probar motor de previsualización en vivo (playback engine)
    ventana.monitor.toggle_play()
    assert ventana.monitor._reproduciendo == True, "El monitor debe estar reproduciendo"
    ventana.monitor.toggle_play()
    assert ventana.monitor._reproduciendo == False, "El monitor debe pausar"

    # 5. Probar guardado y carga de proyecto (.stcut)
    dir_tmp = Path(tempfile.mkdtemp(prefix="test_proj_"))
    try:
        ruta_p = dir_tmp / f"mi_video_proyecto{EXTENSION_PROYECTO}"
        datos_emp = ventana._empaquetar_proyecto()
        guardar_proyecto(ruta_p, datos_emp)
        assert ruta_p.exists(), "El archivo .stcut debe haberse creado"

        datos_rec = cargar_proyecto(ruta_p)
        assert len(datos_rec["marcas"]) == 4, "Debe conservar las 4 marcas guardadas"
        assert datos_rec["modo"] == 1, "Debe conservar el modo de audio único"

        # Cargar en una ventana nueva
        ventana._desempaquetar_proyecto(datos_rec)
        assert len(ventana.marcas) == 4
        print("OK: Motor en vivo y formato de proyectos .stcut pasaron todas las pruebas.")
    finally:
        import shutil
        shutil.rmtree(dir_tmp, ignore_errors=True)
        ventana.close()


if __name__ == "__main__":
    main()
