"""Ventana principal: Editor de video profesional por capítulos.

Interfaz estilo NLE (Non-Linear Editor) con:
- Previsualización en vivo (audio + Ken Burns + transiciones en tiempo real)
- Gestión de proyectos propios (.stcut) estilo Premiere / DaVinci
- Media Bin, Program Monitor, Inspector de ajustes y Línea de Tiempo interactiva
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QMimeData, QPoint, QSettings, QSize, Qt, QUrl
from PyQt6.QtGui import (
    QAction, QColor, QDesktopServices, QDrag, QFont, QIcon, QKeySequence, QPixmap,
    QShortcut,
)
from PyQt6.QtWidgets import (
    QAbstractItemView, QCheckBox, QColorDialog, QComboBox, QDoubleSpinBox,
    QFileDialog, QFontComboBox, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSizePolicy, QSlider, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QTabWidget, QVBoxLayout, QWidget,
)

from core import ordering
from core.chapter_detection import ConfigDeteccion
from core.effects import DIRECCIONES, TIPOS_EFECTO, ConfigEfecto
from core.ia.guion import marcas_estimadas
from core.ia.pipeline import ConfigIA
from core.media import duracion_audio, ErrorMedia
from core.project import (
    cargar_proyecto, guardar_proyecto, EXTENSION_PROYECTO, FILTRO_PROYECTO, ErrorProyecto
)
from core.renderer import ConfigRender
from core.subtitles import (
    EstiloSubtitulos, cues_desde_capitulos, cues_desde_palabras, escribir_srt,
    leer_subtitulos,
)
from ui.trabajador import DetectorCapitulos, Trabajador, TrabajadorVoz
from ui.widgets.monitor import MonitorWidget
from ui.widgets.panel_ia import PanelIA
from ui.widgets.timeline import LineaDeTiempoWidget

AUDIO_EXT = "*.mp3 *.m4a *.aac *.wav *.flac *.ogg *.opus *.wma"
IMAGEN_EXT = "*.png *.jpg *.jpeg *.webp *.bmp"

CARPETA_CFG = Path(__file__).resolve().parent / ".config"
CARPETA_CFG.mkdir(exist_ok=True)
RUTA_TOKEN = CARPETA_CFG / "youtube_token.json"

MODO_MULTI = "Varios audios (uno por capítulo)"
MODO_SINGLE = "Un solo audio completo (detectar capítulos)"
MODO_TTS = "Texto → voz (Jorge Multilingüe es-MX y voces IA)"


class ListaMediosDraggable(QListWidget):
    """Lista de medios con soporte para arrastrar a la línea de tiempo y soltar archivos externos."""
    def __init__(self, tipo_medio: str = "imagen", parent=None) -> None:
        super().__init__(parent)
        self.tipo_medio = tipo_medio
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)

    def startDrag(self, supportedActions) -> None:
        item = self.currentItem()
        if not item:
            return
        ruta = item.data(Qt.ItemDataRole.UserRole) or item.toolTip() or ""
        if not ruta:
            return
        mime = QMimeData()
        mime.setText(ruta)
        mime.setUrls([QUrl.fromLocalFile(ruta)])
        drag = QDrag(self)
        drag.setMimeData(mime)
        pix = item.icon().pixmap(56, 42)
        if not pix.isNull():
            drag.setPixmap(pix)
            drag.setHotSpot(QPoint(28, 21))
        drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            rutas = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            if rutas:
                win = self.window()
                if hasattr(win, "_importar_rutas_arrastradas"):
                    win._importar_rutas_arrastradas(rutas, self.tipo_medio)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class TablaMarcasDroppable(QTableWidget):
    """Tabla de capítulos que acepta soltar imágenes sobre una fila."""
    def __init__(self, rows: int, cols: int, parent=None) -> None:
        super().__init__(rows, cols, parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        ruta = ""
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.isLocalFile():
                    ruta = u.toLocalFile()
                    break
        elif event.mimeData().hasText():
            t = event.mimeData().text().strip().strip('"').strip("'")
            if Path(t).exists():
                ruta = t

        if ruta:
            pos = event.position().toPoint()
            fila = self.rowAt(pos.y())
            if fila >= 0:
                win = self.window()
                if hasattr(win, "_asignar_imagen_a_capitulo"):
                    win._asignar_imagen_a_capitulo(fila, ruta)
                event.acceptProposedAction()
                return

        super().dropEvent(event)


class VentanaPrincipal(QMainWindow):
    def __init__(self, org: str = "VideoCapitulos", app_cfg: str = "App") -> None:
        super().__init__()
        self.setWindowTitle("Studio Cut · Editor de Video por Capítulos")
        self.resize(1280, 850)
        self.setMinimumSize(980, 680)

        self._ajustes = QSettings(org, app_cfg)
        self.audios: list[str] = []
        self.imagenes: list[str] = []
        self.marcas: list[dict] = []
        self.img_manual: dict[int, str] = {}
        self._ultimo_resultado = ""
        self._trabajador: Trabajador | DetectorCapitulos | TrabajadorVoz | None = None
        self._t0_deteccion = 0.0
        self._subida_pendiente = False
        self._abrir_al_terminar = False
        self._pendiente_ruta = ""
        self._voces_guardadas = ""

        # Movimiento / efectos
        self.efecto_global = ConfigEfecto()
        self.efectos_clip: dict[int, dict] = {}
        self._clip_actual: int | None = 0
        self._cargando_efecto = False

        # Subtítulos
        self.subtitulos: list[dict] = []
        self.estilo_subtitulos = EstiloSubtitulos()
        self.palabras_transcripcion: list[dict] = []
        self._cargando_subs = False
        self._subs_auto = False
        self._color_sub = "#FFFFFF"
        self._color_borde_sub = "#000000"
        self._color_caja_sub = "#000000"

        # Inteligencia artificial
        self._ia_config = ConfigIA()
        self._ia_marcas_pendientes: list[dict] = []

        # Gestión de proyecto propio (.stcut)
        self._ruta_proyecto: Path | None = None
        self._proyecto_modificado: bool = False

        self._construir_ui()
        self._configurar_atajos()
        self._cargar_sesion()

    # ------------------------------------------------------------------ UI
    def _construir_ui(self) -> None:
        raiz = QWidget()
        raiz.setObjectName("CentralRoot")
        self.setCentralWidget(raiz)
        layout_principal = QVBoxLayout(raiz)
        layout_principal.setContentsMargins(10, 10, 10, 10)
        layout_principal.setSpacing(8)

        # 1. Barra Superior / Header de la Suite
        layout_principal.addLayout(self._construir_header())

        # 2. Splitter Vertical Principal (Superior: Paneles / Inferior: Timeline)
        self.split_vertical = QSplitter(Qt.Orientation.Vertical)

        # Zona Superior: Splitter Horizontal (Media Bin | Monitor | Inspector)
        self.split_horizontal = QSplitter(Qt.Orientation.Horizontal)
        self.split_horizontal.addWidget(self._construir_panel_medios())
        self.split_horizontal.addWidget(self._construir_panel_monitor())
        self.split_horizontal.addWidget(self._construir_panel_inspector())
        self.split_horizontal.setSizes([310, 580, 390])

        self.split_vertical.addWidget(self.split_horizontal)

        # Zona Inferior: Timeline y Panel de Ajuste Fino
        self.split_vertical.addWidget(self._construir_panel_timeline())
        self.split_vertical.setSizes([490, 310])

        layout_principal.addWidget(self.split_vertical, 1)

        # 3. Barra de Estado, Progreso y Consola
        layout_principal.addWidget(self._construir_barra_inferior())

    # ------------------------------------------------------------- Header
    def _construir_header(self) -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setContentsMargins(4, 2, 4, 4)
        lay.setSpacing(12)

        # Logo / Título
        lbl_logo = QLabel("🎬 STUDIO CUT")
        lbl_logo.setStyleSheet(
            "font-size: 15px; font-weight: 800; color: #38bdf8; letter-spacing: 1px;"
        )
        self.lbl_nombre_proyecto = QLabel("📁 Sin título.stcut")
        self.lbl_nombre_proyecto.setStyleSheet(
            "font-size: 11px; color: #94a3b8; font-weight: 600; background: #161924; "
            "padding: 2px 8px; border-radius: 4px; border: 1px solid #232838;"
        )

        col_tit = QVBoxLayout()
        col_tit.setSpacing(2)
        col_tit.addWidget(lbl_logo)
        lay.addLayout(col_tit)

        lay.addSpacing(6)

        # --- WIDGET ELEGANTE DE PROYECTO (.stcut) ---
        self.frame_proyecto = QFrame()
        self.frame_proyecto.setStyleSheet(
            "QFrame { background: #161924; border: 1px solid #282f44; border-radius: 6px; padding: 2px 4px; }"
        )
        lay_fp = QHBoxLayout(self.frame_proyecto)
        lay_fp.setContentsMargins(4, 2, 4, 2)
        lay_fp.setSpacing(6)

        self.btn_abrir_proyecto_rapido = QPushButton("📂 Seleccionar Proyecto (.stcut)")
        self.btn_abrir_proyecto_rapido.setToolTip("Abrir o seleccionar archivo de proyecto Studio Cut (.stcut) [Ctrl+O]")
        self.btn_abrir_proyecto_rapido.setStyleSheet(
            "QPushButton { background: transparent; border: none; color: #e2e8f0; font-weight: 600; font-size: 11px; padding: 4px 6px; text-align: left; } "
            "QPushButton:hover { color: #38bdf8; background: #1e2436; border-radius: 4px; }"
        )
        self.btn_abrir_proyecto_rapido.clicked.connect(self._abrir_proyecto)
        self.lbl_nombre_proyecto = self.btn_abrir_proyecto_rapido

        self.lbl_badge_guardado = QLabel("Sin guardar")
        self.lbl_badge_guardado.setStyleSheet(
            "color: #f59e0b; font-size: 10px; font-weight: bold; background: #261f14; "
            "border: 1px solid #78350f; border-radius: 3px; padding: 1px 5px;"
        )

        self.btn_guardar_rapido = QPushButton("💾 Guardar")
        self.btn_guardar_rapido.setToolTip("Guardar cambios en este mismo archivo [Ctrl+S]")
        self.btn_guardar_rapido.setStyleSheet(
            "QPushButton { background: #0284c7; color: white; font-weight: bold; font-size: 11px; "
            "border-radius: 4px; padding: 4px 10px; } "
            "QPushButton:hover { background: #0369a1; }"
        )
        self.btn_guardar_rapido.clicked.connect(self._guardar_proyecto)

        self.btn_menu_proyecto = QPushButton("▼")
        self.btn_menu_proyecto.setFixedWidth(24)
        self.btn_menu_proyecto.setStyleSheet(
            "QPushButton { background: #232838; color: #94a3b8; border: none; border-radius: 4px; font-size: 9px; padding: 4px; } "
            "QPushButton:hover { background: #333a4e; color: white; }"
        )
        menu_proj = QMenu(self)
        act_nuevo = menu_proj.addAction("📄 Nuevo Proyecto")
        act_nuevo.setShortcut(QKeySequence("Ctrl+N"))
        act_nuevo.triggered.connect(self._nuevo_proyecto)

        act_abrir = menu_proj.addAction("📂 Abrir Proyecto...")
        act_abrir.setShortcut(QKeySequence("Ctrl+O"))
        act_abrir.triggered.connect(self._abrir_proyecto)

        menu_proj.addSeparator()

        act_guardar = menu_proj.addAction("💾 Guardar (en el mismo archivo)")
        act_guardar.setShortcut(QKeySequence("Ctrl+S"))
        act_guardar.triggered.connect(self._guardar_proyecto)

        act_guardar_como = menu_proj.addAction("💾 Guardar Proyecto Como...")
        act_guardar_como.setShortcut(QKeySequence("Ctrl+Shift+S"))
        act_guardar_como.triggered.connect(self._guardar_proyecto_como)

        self.btn_menu_proyecto.setMenu(menu_proj)

        lay_fp.addWidget(self.btn_abrir_proyecto_rapido)
        lay_fp.addWidget(self.lbl_badge_guardado)
        lay_fp.addWidget(self.btn_guardar_rapido)
        lay_fp.addWidget(self.btn_menu_proyecto)

        lay.addWidget(self.frame_proyecto)

        lay.addSpacing(10)

        # Selector de Modo de Edición
        lbl_modo = QLabel("Modo:")
        lbl_modo.setStyleSheet("font-weight: 600; color: #94a3b8;")
        lay.addWidget(lbl_modo)

        self.combo_modo = QComboBox()
        self.combo_modo.addItems([MODO_MULTI, MODO_SINGLE, MODO_TTS])
        self.combo_modo.setMinimumWidth(270)
        self.combo_modo.currentIndexChanged.connect(self._aplicar_modo)
        lay.addWidget(self.combo_modo)

        lay.addStretch()

        # Botón Detección Rápida en Header
        self.btn_detectar_top = QPushButton("🎙 Auto-Detectar Capítulos")
        self.btn_detectar_top.setObjectName("btn_ai")
        self.btn_detectar_top.setToolTip("Transcribe el audio y encuentra las marcas automáticamente")
        self.btn_detectar_top.clicked.connect(self._detectar)
        lay.addWidget(self.btn_detectar_top)

        # Botón Render / Exportar Principal
        self.btn_generar_top = QPushButton("🎬 Exportar MP4")
        self.btn_generar_top.setObjectName("primary")
        self.btn_generar_top.setToolTip("Exportar el video final en formato .mp4")
        self.btn_generar_top.clicked.connect(self._generar)
        lay.addWidget(self.btn_generar_top)

        return lay

    def _configurar_atajos(self) -> None:
        """Atajos de teclado de edición profesional."""
        QShortcut(QKeySequence("Ctrl+N"), self, self._nuevo_proyecto)
        QShortcut(QKeySequence("Ctrl+O"), self, self._abrir_proyecto)
        QShortcut(QKeySequence("Ctrl+S"), self, self._guardar_proyecto)
        QShortcut(QKeySequence("Ctrl+Shift+S"), self, self._guardar_proyecto_como)

        sh_space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        sh_space.activated.connect(self._toggle_espacio_reproduccion)

        # Herramientas de edición NLE (estilo Premiere Pro)
        sh_v = QShortcut(QKeySequence(Qt.Key.Key_V), self)
        sh_v.activated.connect(lambda: self._cambiar_herramienta_timeline("select"))
        sh_c = QShortcut(QKeySequence(Qt.Key.Key_C), self)
        sh_c.activated.connect(lambda: self._cambiar_herramienta_timeline("razor"))
        QShortcut(QKeySequence("Ctrl+K"), self, self._dividir_en_playhead)
        sh_s = QShortcut(QKeySequence(Qt.Key.Key_S), self)
        sh_s.activated.connect(self._toggle_snap_timeline)

    def _toggle_espacio_reproduccion(self) -> None:
        focus = self.focusWidget()
        if not isinstance(focus, (QLineEdit, QPlainTextEdit)):
            self.monitor.toggle_play()

    # ------------------------------------------------ Panel Izquierdo: Medios
    def _construir_panel_medios(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("PanelContainer")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lbl_tit = QLabel("BIBLIOTECA DE MEDIOS")
        lbl_tit.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #94a3b8; letter-spacing: 1px;"
        )
        lay.addWidget(lbl_tit)

        self.tabs_medios = QTabWidget()

        # Tab Audios
        w_audios = QWidget()
        lay_a = QVBoxLayout(w_audios)
        lay_a.setContentsMargins(4, 6, 4, 4)
        lay_a.setSpacing(6)

        self.chk_auto = QCheckBox("Auto-ordenar por nombre (intro, cap 1...)")
        self.chk_auto.setChecked(True)
        self.chk_auto.stateChanged.connect(self._reordenar)
        lay_a.addWidget(self.chk_auto)

        self.lista_audios = ListaMediosDraggable("audio")
        self.lista_audios.setAlternatingRowColors(True)
        lay_a.addWidget(self.lista_audios, 1)

        fila_a_btn = QHBoxLayout()
        self.btn_add_audio = QPushButton("+ Añadir audio")
        self.btn_add_audio.clicked.connect(self._anadir_audios)
        self.btn_detectar = QPushButton("🎙 Detectar")
        self.btn_detectar.setObjectName("btn_ai")
        self.btn_detectar.clicked.connect(self._detectar)
        self.btn_detectar.setVisible(False)

        b_quitar_a = QPushButton("Quitar")
        b_quitar_a.clicked.connect(self._quitar_audio)

        b_arriba = QPushButton("▲")
        b_arriba.setFixedWidth(28)
        b_arriba.clicked.connect(lambda: self._mover(-1))
        b_abajo = QPushButton("▼")
        b_abajo.setFixedWidth(28)
        b_abajo.clicked.connect(lambda: self._mover(+1))

        fila_a_btn.addWidget(self.btn_add_audio)
        fila_a_btn.addWidget(self.btn_detectar)
        fila_a_btn.addWidget(b_quitar_a)
        fila_a_btn.addWidget(b_arriba)
        fila_a_btn.addWidget(b_abajo)
        lay_a.addLayout(fila_a_btn)

        # Panel TTS (Texto a Voz)
        self.zona_tts = QWidget()
        lay_tts = QVBoxLayout(self.zona_tts)
        lay_tts.setContentsMargins(0, 4, 0, 0)
        lay_tts.setSpacing(4)
        lay_tts.addWidget(QLabel("Guion para voz artificial:"))
        self.txt_script = QPlainTextEdit()
        self.txt_script.setPlaceholderText(
            "Pega aquí tu guion con introducción, capítulos y cierre..."
        )
        self.txt_script.setFixedHeight(85)
        lay_tts.addWidget(self.txt_script)

        fila_voz = QHBoxLayout()
        self.combo_voz = QComboBox()
        fila_voz.addWidget(self.combo_voz, 1)
        self.spin_vel = QSpinBox()
        self.spin_vel.setRange(-10, 10)
        self.spin_vel.setValue(0)
        self.spin_vel.setToolTip("Velocidad (-10 a +10)")
        fila_voz.addWidget(self.spin_vel)
        self.btn_voz = QPushButton("🎙 Generar")
        self.btn_voz.setObjectName("primary")
        self.btn_voz.clicked.connect(self._generar_voz)
        fila_voz.addWidget(self.btn_voz)
        lay_tts.addLayout(fila_voz)
        self.zona_tts.setVisible(False)
        lay_a.addWidget(self.zona_tts)

        self.tabs_medios.addTab(w_audios, "🎵 Audios")

        # Tab Imágenes
        w_imagenes = QWidget()
        lay_img = QVBoxLayout(w_imagenes)
        lay_img.setContentsMargins(4, 6, 4, 4)
        lay_img.setSpacing(6)

        self.lista_imagenes = ListaMediosDraggable("imagen")
        self.lista_imagenes.setIconSize(QSize(64, 38))
        self.lista_imagenes.setToolTip("Arrastra una imagen hacia un capítulo del Timeline o de la Tabla")
        lay_img.addWidget(self.lista_imagenes, 1)

        lbl_arrastrar_hint = QLabel("💡 Arrastra la imagen a la pista o capítulo para asignarla")
        lbl_arrastrar_hint.setStyleSheet("color: #64748b; font-size: 10px; font-style: italic;")
        lay_img.addWidget(lbl_arrastrar_hint)

        fila_i_btn = QHBoxLayout()
        b_add_img = QPushButton("+ Añadir imágenes")
        b_add_img.clicked.connect(self._anadir_imagenes)
        b_del_img = QPushButton("Quitar")
        b_del_img.clicked.connect(self._quitar_imagen)
        fila_i_btn.addWidget(b_add_img)
        fila_i_btn.addWidget(b_del_img)
        lay_img.addLayout(fila_i_btn)

        self.tabs_medios.addTab(w_imagenes, "🖼 Imágenes")

        lay.addWidget(self.tabs_medios, 1)
        return panel

    # --------------------------------------------- Panel Central: Monitor
    def _construir_panel_monitor(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("PanelContainer")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(6, 6, 6, 6)

        self.monitor = MonitorWidget(self)
        self.monitor.solicitar_tiempo.connect(self._al_cambiar_tiempo_monitor)
        self.monitor.probar_estilo_click.connect(self._probar_estilo)
        self.monitor.ver_video_click.connect(self._ver_video)

        # Compatibilidad: lbl_video_preview para captura de frames de tests
        self.lbl_video_preview = QLabel()
        self.lbl_video_preview.setVisible(False)

        lay.addWidget(self.monitor, 1)
        return panel

    # -------------------------------------------- Panel Derecho: Inspector
    def _construir_panel_inspector(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("PanelContainer")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        lbl_tit = QLabel("INSPECTOR DE PROPIEDADES")
        lbl_tit.setStyleSheet(
            "font-size: 11px; font-weight: bold; color: #94a3b8; letter-spacing: 1px;"
        )
        lay.addWidget(lbl_tit)

        self.tabs_inspector = QTabWidget()

        # Pestaña 1: Configuración de Video
        w_video = QWidget()
        lay_v = QVBoxLayout(w_video)
        form_v = QFormLayout()
        form_v.setContentsMargins(4, 8, 4, 4)
        form_v.setSpacing(10)

        self.cbo_res = QComboBox()
        for txt, datos in (("1080p (1920×1080)", (1920, 1080)),
                           ("720p (1280×720)", (1280, 720)),
                           ("Vertical 1080×1920 (Shorts)", (1080, 1920))):
            self.cbo_res.addItem(txt, datos)
        self.cbo_res.currentIndexChanged.connect(self._cambio_resolucion)
        form_v.addRow("Resolución:", self.cbo_res)

        self.spin_trans = QDoubleSpinBox()
        self.spin_trans.setRange(0.2, 3.0)
        self.spin_trans.setSingleStep(0.1)
        self.spin_trans.setSuffix(" s")
        self.spin_trans.setValue(1.0)
        self.spin_trans.valueChanged.connect(lambda: self._actualizar_timeline_y_monitor())
        form_v.addRow("Duración trans.:", self.spin_trans)

        self.cbo_estilo = QComboBox()
        self.cbo_estilo.addItems(["Desvanecido suave", "Varios efectos animados"])
        form_v.addRow("Estilo trans.:", self.cbo_estilo)

        # ── Movimiento de las imágenes ──
        self.cbo_efecto = QComboBox()
        for clave, etiqueta in TIPOS_EFECTO.items():
            self.cbo_efecto.addItem(etiqueta, clave)
        self.cbo_efecto.setToolTip(
            "Movimiento que se aplica a las imágenes durante cada clip.")
        form_v.addRow("Movimiento:", self.cbo_efecto)

        self.spin_efecto_int = QDoubleSpinBox()
        self.spin_efecto_int.setRange(0.0, 50.0)
        self.spin_efecto_int.setDecimals(0)
        self.spin_efecto_int.setSingleStep(1.0)
        self.spin_efecto_int.setSuffix(" %")
        self.spin_efecto_int.setToolTip(
            "Cuánto acerca o aleja el zoom. 10-20 % suele ser suficiente.")
        form_v.addRow("Intensidad:", self.spin_efecto_int)

        self.spin_efecto_dist = QDoubleSpinBox()
        self.spin_efecto_dist.setRange(0.0, 100.0)
        self.spin_efecto_dist.setDecimals(0)
        self.spin_efecto_dist.setSingleStep(5.0)
        self.spin_efecto_dist.setSuffix(" %")
        self.spin_efecto_dist.setToolTip(
            "Cuánto recorre el encuadre durante el clip (0 % = solo zoom centrado)."
        )
        form_v.addRow("Distancia:", self.spin_efecto_dist)

        self.spin_efecto_vel = QDoubleSpinBox()
        self.spin_efecto_vel.setRange(0.10, 3.00)
        self.spin_efecto_vel.setDecimals(2)
        self.spin_efecto_vel.setSingleStep(0.05)
        self.spin_efecto_vel.setSuffix(" Hz")
        self.spin_efecto_vel.setToolTip("Ciclos por segundo del pulso o la sacudida.")
        form_v.addRow("Velocidad:", self.spin_efecto_vel)

        self.cbo_efecto_dir = QComboBox()
        for clave, etiqueta in DIRECCIONES.items():
            self.cbo_efecto_dir.addItem(etiqueta, clave)
        form_v.addRow("Dirección:", self.cbo_efecto_dir)

        self.chk_efecto_suave = QCheckBox("Suavizar movimiento")
        self.chk_efecto_suave.setToolTip("Acelera y desacelera al inicio y al final (ease-in-out).")
        form_v.addRow("", self.chk_efecto_suave)

        self.cbo_efecto_ambito = QComboBox()
        self.cbo_efecto_ambito.addItem("Todos los clips", "todos")
        self.cbo_efecto_ambito.addItem("Clip seleccionado", "clip")
        form_v.addRow("Aplicar a:", self.cbo_efecto_ambito)

        self.lbl_efecto_info = QLabel("")
        self.lbl_efecto_info.setWordWrap(True)
        self.lbl_efecto_info.setStyleSheet("color: #64748b; font-size: 10px;")
        form_v.addRow("", self.lbl_efecto_info)

        self.btn_quitar_efecto_clip = QPushButton("↺ Quitar efecto propio del clip")
        self.btn_quitar_efecto_clip.setStyleSheet("font-size: 10px; padding: 4px 8px;")
        self.btn_quitar_efecto_clip.clicked.connect(self._quitar_efecto_clip)
        form_v.addRow("", self.btn_quitar_efecto_clip)

        for widget in (self.cbo_efecto, self.cbo_efecto_dir):
            widget.currentIndexChanged.connect(self._al_cambiar_efecto_ui)
        self.cbo_efecto_ambito.currentIndexChanged.connect(
            self._al_cambiar_ambito_efecto)
        for widget in (self.spin_efecto_int, self.spin_efecto_dist, self.spin_efecto_vel):
            widget.valueChanged.connect(self._al_cambiar_efecto_ui)
        self.chk_efecto_suave.toggled.connect(self._al_cambiar_efecto_ui)

        self._cargar_efecto_en_ui(self.efecto_global)
        self._actualizar_controles_efecto()
        self.lbl_efecto_info.setText("El movimiento se aplica a todos los clips.")

        lay_v.addLayout(form_v)
        lay_v.addStretch()

        self.tabs_inspector.addTab(w_video, "⚙ Ajustes Video")

        # Pestaña 2: Configuración de Detección de Capítulos
        w_det = QWidget()
        scroll_det = QScrollArea()
        scroll_det.setWidgetResizable(True)
        scroll_det.setFrameShape(QFrame.Shape.NoFrame)
        scroll_det.viewport().setAutoFillBackground(False)
        w_det_inner = QWidget()
        lay_det = QVBoxLayout(w_det_inner)
        lay_det.setContentsMargins(6, 6, 6, 6)
        lay_det.setSpacing(10)

        # ── Título de sección ──
        lbl_det_tit = QLabel("DETECCIÓN AUTOMÁTICA DE CAPÍTULOS")
        lbl_det_tit.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: #38bdf8; letter-spacing: 1px;"
        )
        lay_det.addWidget(lbl_det_tit)

        lbl_hint = QLabel(
            "Configura las palabras que el audio pronuncia para marcar cada sección."
        )
        lbl_hint.setWordWrap(True)
        lbl_hint.setStyleSheet("color: #64748b; font-size: 10px;")
        lay_det.addWidget(lbl_hint)

        form_det = QFormLayout()
        form_det.setSpacing(8)
        form_det.setContentsMargins(0, 4, 0, 0)

        # Palabras de Intro
        self.txt_det_intro = QLineEdit()
        self.txt_det_intro.setPlaceholderText("intro, bienvenid, presentacion, inicio…")
        self.txt_det_intro.setText("intro, bienvenid, presentacion, inicio, introduc, prologo")
        self.txt_det_intro.setToolTip(
            "Palabras (separadas por coma) que indican el inicio/introducción del video."
        )
        form_det.addRow("🎬 Intro:", self.txt_det_intro)

        # Palabras de Cierre
        self.txt_det_cierre = QLineEdit()
        self.txt_det_cierre.setPlaceholderText("cierre, outro, despedida, conclusion…")
        self.txt_det_cierre.setText("cierre, outro, despedida, conclusion, final, termina")
        self.txt_det_cierre.setToolTip(
            "Palabras que indican el cierre o despedida al final del video."
        )
        form_det.addRow("🏁 Cierre:", self.txt_det_cierre)

        # Palabras de Capítulo
        self.txt_det_capitulo = QLineEdit()
        self.txt_det_capitulo.setPlaceholderText("capitulo, seccion, parte, paso, tema…")
        self.txt_det_capitulo.setText("capitulo, seccion, parte, paso, tema, episodio")
        self.txt_det_capitulo.setToolTip(
            "Palabras clave que marcan el inicio de un capítulo/sección."
        )
        form_det.addRow("📖 Capítulo:", self.txt_det_capitulo)

        # Modo de Detección
        self.cbo_det_modo = QComboBox()
        self.cbo_det_modo.addItem("🔢+📝 Combinado: palabra + número (recomendado)", "combinado")
        self.cbo_det_modo.addItem("🔀 Flexible (número o palabra con gap)", "flexible")
        self.cbo_det_modo.addItem("📝 Solo palabra clave", "solo_palabra")
        self.cbo_det_modo.addItem("🔢 Solo números secuenciales", "solo_numero")
        self.cbo_det_modo.setToolTip(
            "Combinado: requiere palabra + número ascendente (ej. 'capítulo 1', 'capítulo 2').\n"
            "Flexible: detecta 'capítulo 1' o la palabra clave separada por el tiempo mínimo.\n"
            "Solo palabra: cada vez que aparezca la palabra clave con separación mínima.\n"
            "Solo número: detecta números secuenciales pronunciados (1, 2, 3...)."
        )
        form_det.addRow("⚙ Modo:", self.cbo_det_modo)

        # Palabras Puente
        self.txt_det_puente = QLineEdit()
        self.txt_det_puente.setPlaceholderText("numero, el, la, de, del, nro…")
        self.txt_det_puente.setText("numero, nro, no, el, la, de, del, es")
        self.txt_det_puente.setToolTip(
            "Palabras que pueden aparecer entre la clave y el número "
            "(ej: 'capítulo número 2' → puente = 'número')."
        )
        form_det.addRow("🔗 Puentes:", self.txt_det_puente)

        # Gap mínimo entre capítulos
        self.spin_det_gap = QSpinBox()
        self.spin_det_gap.setRange(5, 600)
        self.spin_det_gap.setValue(60)
        self.spin_det_gap.setSuffix(" s")
        self.spin_det_gap.setToolTip(
            "Tiempo mínimo en segundos entre un capítulo y el siguiente.\n"
            "Evita falsos positivos si la palabra clave se repite en la misma sección."
        )
        form_det.addRow("⏱ Gap mínimo:", self.spin_det_gap)

        # Límite máximo de capítulos
        self.spin_det_max = QSpinBox()
        self.spin_det_max.setRange(0, 100)
        self.spin_det_max.setValue(0)
        self.spin_det_max.setSpecialValueText("Sin límite (0)")
        self.spin_det_max.setToolTip(
            "Número máximo de capítulos a detectar (0 = sin límite).\n"
            "Si sabes que tu video tiene exactamente 7 capítulos, pon 7 para cortar falsos extras."
        )
        form_det.addRow("🔢 Máx. capítulos:", self.spin_det_max)

        lay_det.addLayout(form_det)

        # Confianza mínima con slider
        grp_conf = QGroupBox("Confianza mínima del reconocimiento")
        lay_conf = QVBoxLayout(grp_conf)
        lay_conf.setSpacing(4)

        lbl_conf_hint = QLabel(
            "Más alta = solo palabras muy claras. Más baja = más capturas pero posibles errores."
        )
        lbl_conf_hint.setWordWrap(True)
        lbl_conf_hint.setStyleSheet("color: #64748b; font-size: 10px;")
        lay_conf.addWidget(lbl_conf_hint)

        fila_conf = QHBoxLayout()
        self.slider_confianza = QSlider(Qt.Orientation.Horizontal)
        self.slider_confianza.setRange(5, 90)   # 0.05 a 0.90
        self.slider_confianza.setValue(20)       # 0.20 por defecto
        self.slider_confianza.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider_confianza.setTickInterval(10)
        self.lbl_confianza_val = QLabel("0.20")
        self.lbl_confianza_val.setFixedWidth(34)
        self.lbl_confianza_val.setStyleSheet("color: #38bdf8; font-weight: bold;")
        self.slider_confianza.valueChanged.connect(
            lambda v: self.lbl_confianza_val.setText(f"{v / 100:.2f}")
        )
        fila_conf.addWidget(QLabel("Baja"))
        fila_conf.addWidget(self.slider_confianza, 1)
        fila_conf.addWidget(QLabel("Alta"))
        fila_conf.addWidget(self.lbl_confianza_val)
        lay_conf.addLayout(fila_conf)
        lay_det.addWidget(grp_conf)

        # Botón Restaurar Defaults
        btn_reset_det = QPushButton("↺ Restaurar valores por defecto")
        btn_reset_det.setStyleSheet("font-size: 10px; padding: 4px 8px;")
        btn_reset_det.clicked.connect(self._reset_config_deteccion)
        lay_det.addWidget(btn_reset_det)

        # Vista previa del comportamiento
        grp_preview = QGroupBox("Ejemplo de detección con la config actual")
        lay_prev = QVBoxLayout(grp_preview)
        lbl_ex = QLabel()
        lbl_ex.setWordWrap(True)
        lbl_ex.setStyleSheet("color: #94a3b8; font-size: 10px; font-family: monospace;")
        self.lbl_det_ejemplo = lbl_ex
        lay_prev.addWidget(lbl_ex)
        lay_det.addWidget(grp_preview)
        lay_det.addStretch()

        # Actualizar ejemplo al cambiar campos
        for campo in (self.txt_det_intro, self.txt_det_cierre,
                      self.txt_det_capitulo, self.txt_det_puente):
            campo.textChanged.connect(self._actualizar_ejemplo_deteccion)
        self.cbo_det_modo.currentIndexChanged.connect(self._actualizar_ejemplo_deteccion)
        self._actualizar_ejemplo_deteccion()

        scroll_det.setWidget(w_det_inner)
        lay_outer_det = QVBoxLayout(w_det)
        lay_outer_det.setContentsMargins(0, 0, 0, 0)
        lay_outer_det.addWidget(scroll_det)

        self.tabs_inspector.addTab(w_det, "🎙 Detección")

        # Pestaña 3: Subtítulos
        self.tabs_inspector.addTab(self._construir_tab_subtitulos(), "💬 Subtítulos")

        # Pestaña 4: YouTube & Publicación
        w_yt = QWidget()
        scroll_yt = QScrollArea()
        scroll_yt.setWidgetResizable(True)
        scroll_yt.setFrameShape(QFrame.Shape.NoFrame)
        scroll_yt.viewport().setAutoFillBackground(False)
        w_yt_inner = QWidget()
        lay_yt = QVBoxLayout(w_yt_inner)
        lay_yt.setContentsMargins(4, 4, 4, 4)

        self.grp_yt = QGroupBox("Publicar en YouTube al terminar")
        self.grp_yt.setCheckable(True)
        self.grp_yt.setChecked(False)
        form_yt = QFormLayout(self.grp_yt)
        form_yt.setSpacing(8)

        self.txt_titulo = QLineEdit()
        self.txt_titulo.setPlaceholderText("Título del video...")
        form_yt.addRow("Título:", self.txt_titulo)

        self.txt_desc = QPlainTextEdit()
        self.txt_desc.setFixedHeight(50)
        self.txt_desc.setPlaceholderText("Descripción para YouTube...")
        form_yt.addRow("Descripción:", self.txt_desc)

        self.txt_tags = QLineEdit()
        self.txt_tags.setPlaceholderText("etiqueta1, etiqueta2...")
        form_yt.addRow("Etiquetas:", self.txt_tags)

        self.lbl_secret = QLineEdit()
        self.lbl_secret.setReadOnly(True)
        self.lbl_secret.setPlaceholderText("client_secret.json...")
        b_secret = QPushButton("Buscar…")
        b_secret.clicked.connect(self._buscar_secret)
        fila_s = QHBoxLayout()
        fila_s.addWidget(self.lbl_secret, 1)
        fila_s.addWidget(b_secret)
        form_yt.addRow("OAuth Secret:", fila_s)

        self.lbl_mini = QLineEdit()
        self.lbl_mini.setReadOnly(True)
        self.lbl_mini.setPlaceholderText("miniatura.png...")
        b_mini = QPushButton("Buscar…")
        b_mini.clicked.connect(self._buscar_miniatura)
        fila_m = QHBoxLayout()
        fila_m.addWidget(self.lbl_mini, 1)
        fila_m.addWidget(b_mini)
        form_yt.addRow("Miniatura:", fila_m)

        self.chk_ver_antes = QCheckBox("Revisar antes de subir (quedará pendiente)")
        form_yt.addRow(self.chk_ver_antes)

        lay_yt.addWidget(self.grp_yt)

        self.btn_subir_pend = QPushButton("⬆ Subir video pendiente…")
        self.btn_subir_pend.clicked.connect(self._subir_pendiente)
        lay_yt.addWidget(self.btn_subir_pend)
        lay_yt.addStretch()

        scroll_yt.setWidget(w_yt_inner)
        lay_outer_yt = QVBoxLayout(w_yt)
        lay_outer_yt.setContentsMargins(0, 0, 0, 0)
        lay_outer_yt.addWidget(scroll_yt)

        self.tabs_inspector.addTab(w_yt, "🚀 YouTube")

        # Pestaña 5: Inteligencia Artificial
        self.panel_ia = PanelIA(self)
        self.tabs_inspector.addTab(self.panel_ia, "🤖 IA")

        lay.addWidget(self.tabs_inspector, 1)
        return panel

    # -------------------------------------------- Pestaña de Subtítulos
    def _construir_tab_subtitulos(self) -> QWidget:
        w = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)

        tit = QLabel("SUBTÍTULOS")
        tit.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: #38bdf8; letter-spacing: 1px;"
        )
        lay.addWidget(tit)

        hint = QLabel(
            "Se muestran en el monitor al instante. Con «🎙 Auto-Detectar "
            "Capítulos» (modo Un solo audio) se crean solos desde la voz. "
            "En modo Varios audios usa «⚡ Desde capítulos» o «+ Subtítulo». "
            "También puedes importar un .srt/.vtt. Los tiempos son los del video final."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(hint)

        self.chk_sub_mostrar = QCheckBox("Mostrar subtítulos en el monitor")
        self.chk_sub_mostrar.setChecked(True)
        lay.addWidget(self.chk_sub_mostrar)

        self.chk_quemar_subtitulos = QCheckBox("Quemar subtítulos en el video final")
        self.chk_quemar_subtitulos.setChecked(True)
        lay.addWidget(self.chk_quemar_subtitulos)

        self.chk_sub_auto = QCheckBox("Crear subtítulos automáticamente al transcribir")
        self.chk_sub_auto.setChecked(True)
        self.chk_sub_auto.setToolTip(
            "Al detectar la voz se generan subtítulos con las palabras y sus "
            "tiempos reales. Si no hay voz, se usan los títulos de capítulo."
        )
        lay.addWidget(self.chk_sub_auto)

        self.lbl_subs_estado = QLabel("")
        self.lbl_subs_estado.setWordWrap(True)
        self.lbl_subs_estado.setStyleSheet("color: #38bdf8; font-size: 10px;")
        lay.addWidget(self.lbl_subs_estado)

        self.tabla_subs = QTableWidget(0, 3)
        self.tabla_subs.setHorizontalHeaderLabels(["Inicio", "Fin", "Texto"])
        self.tabla_subs.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla_subs.setColumnWidth(0, 82)
        self.tabla_subs.setColumnWidth(1, 82)
        self.tabla_subs.horizontalHeader().setStretchLastSection(True)
        self.tabla_subs.verticalHeader().setVisible(False)
        self.tabla_subs.setMinimumHeight(160)
        self.tabla_subs.itemChanged.connect(self._al_editar_texto_sub)
        self.tabla_subs.itemSelectionChanged.connect(self._al_seleccionar_sub)
        lay.addWidget(self.tabla_subs)

        fila_btn = QHBoxLayout()
        b_add = QPushButton("+ Subtítulo")
        b_add.setToolTip("Añade un subtítulo en la posición del cabezal")
        b_add.clicked.connect(self._anadir_subtitulo)
        b_del = QPushButton("🗑")
        b_del.setToolTip("Eliminar el subtítulo seleccionado")
        b_del.setFixedWidth(32)
        b_del.clicked.connect(self._quitar_subtitulo)
        b_import = QPushButton("📥 Importar")
        b_import.setToolTip("Importar un archivo .srt o .vtt")
        b_import.clicked.connect(self._importar_subtitulos)
        b_export = QPushButton("📤 Exportar SRT")
        b_export.clicked.connect(self._exportar_subtitulos)
        for b in (b_add, b_del, b_import, b_export):
            fila_btn.addWidget(b)
        lay.addLayout(fila_btn)

        fila_gen = QHBoxLayout()
        b_caps = QPushButton("⚡ Desde capítulos")
        b_caps.setToolTip("Crea subtítulos con el título de cada capítulo")
        b_caps.clicked.connect(self._generar_subtitulos_capitulos)
        b_voz = QPushButton("🎙 Desde la voz")
        b_voz.setToolTip("Crea subtítulos con las palabras transcritas (Vosk)")
        b_voz.clicked.connect(self._generar_subtitulos_voz)
        fila_gen.addWidget(b_caps)
        fila_gen.addWidget(b_voz)
        lay.addLayout(fila_gen)

        grp_estilo = QGroupBox("Estilo de los subtítulos")
        form = QFormLayout(grp_estilo)
        form.setSpacing(8)

        self.cbo_fuente_sub = QFontComboBox()
        self.cbo_fuente_sub.setCurrentFont(QFont("Segoe UI"))
        form.addRow("Fuente:", self.cbo_fuente_sub)

        self.spin_tam_sub = QDoubleSpinBox()
        self.spin_tam_sub.setRange(2.0, 14.0)
        self.spin_tam_sub.setDecimals(1)
        self.spin_tam_sub.setSuffix(" % alto")
        self.spin_tam_sub.setValue(5.5)
        self.spin_tam_sub.setToolTip(
            "Tamaño de letra como porcentaje de la altura del video.")
        form.addRow("Tamaño:", self.spin_tam_sub)

        self.chk_negrita_sub = QCheckBox("Negrita")
        self.chk_negrita_sub.setChecked(True)
        form.addRow("", self.chk_negrita_sub)

        self.btn_color_sub = QPushButton("Color del texto…")
        self.btn_color_sub.clicked.connect(lambda: self._elegir_color_sub("texto"))
        form.addRow("Texto:", self.btn_color_sub)

        self.spin_contorno_sub = QDoubleSpinBox()
        self.spin_contorno_sub.setRange(0.0, 10.0)
        self.spin_contorno_sub.setDecimals(1)
        self.spin_contorno_sub.setValue(2.4)
        self.spin_contorno_sub.setSuffix(" px")
        form.addRow("Contorno:", self.spin_contorno_sub)

        self.btn_color_borde_sub = QPushButton("Color del contorno…")
        self.btn_color_borde_sub.clicked.connect(lambda: self._elegir_color_sub("borde"))
        form.addRow("Borde:", self.btn_color_borde_sub)

        self.chk_caja_sub = QCheckBox("Fondo de caja")
        form.addRow("", self.chk_caja_sub)

        self.spin_caja_op_sub = QSpinBox()
        self.spin_caja_op_sub.setRange(0, 100)
        self.spin_caja_op_sub.setValue(55)
        self.spin_caja_op_sub.setSuffix(" %")
        form.addRow("Opacidad caja:", self.spin_caja_op_sub)

        self.btn_color_caja_sub = QPushButton("Color de la caja…")
        self.btn_color_caja_sub.clicked.connect(lambda: self._elegir_color_sub("caja"))
        form.addRow("Caja:", self.btn_color_caja_sub)

        self.cbo_pos_sub = QComboBox()
        self.cbo_pos_sub.addItem("Abajo", "abajo")
        self.cbo_pos_sub.addItem("Centro", "centro")
        self.cbo_pos_sub.addItem("Arriba", "arriba")
        form.addRow("Posición:", self.cbo_pos_sub)

        self.spin_margen_sub = QDoubleSpinBox()
        self.spin_margen_sub.setRange(0.0, 25.0)
        self.spin_margen_sub.setDecimals(1)
        self.spin_margen_sub.setSuffix(" %")
        self.spin_margen_sub.setValue(4.0)
        form.addRow("Margen:", self.spin_margen_sub)

        self.spin_maxchars_sub = QSpinBox()
        self.spin_maxchars_sub.setRange(15, 90)
        self.spin_maxchars_sub.setValue(42)
        self.spin_maxchars_sub.setSuffix(" car.")
        form.addRow("Máx. por línea:", self.spin_maxchars_sub)

        lay.addWidget(grp_estilo)
        lay.addStretch()

        # Conexiones de estilo (en vivo)
        self.cbo_fuente_sub.currentFontChanged.connect(self._al_cambiar_estilo_sub)
        self.spin_tam_sub.valueChanged.connect(self._al_cambiar_estilo_sub)
        self.chk_negrita_sub.toggled.connect(self._al_cambiar_estilo_sub)
        self.spin_contorno_sub.valueChanged.connect(self._al_cambiar_estilo_sub)
        self.chk_caja_sub.toggled.connect(self._al_cambiar_estilo_sub)
        self.spin_caja_op_sub.valueChanged.connect(self._al_cambiar_estilo_sub)
        self.cbo_pos_sub.currentIndexChanged.connect(self._al_cambiar_estilo_sub)
        self.spin_margen_sub.valueChanged.connect(self._al_cambiar_estilo_sub)
        self.spin_maxchars_sub.valueChanged.connect(self._al_cambiar_estilo_sub)
        self.chk_sub_mostrar.toggled.connect(self._actualizar_subtitulos_monitor)
        self.chk_quemar_subtitulos.toggled.connect(self._marcar_proyecto_modificado)

        self._aplicar_estilo_sub_ui(self.estilo_subtitulos)
        self._refrescar_tabla_subtitulos()

        scroll.setWidget(inner)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        return w

    # ------------------------------------------------ Movimiento (efectos)
    def _efecto_desde_ui(self) -> ConfigEfecto:
        return ConfigEfecto(
            tipo=self.cbo_efecto.currentData() or "kenburns",
            intensidad=self.spin_efecto_int.value() / 100.0,
            distancia=self.spin_efecto_dist.value() / 100.0,
            velocidad=self.spin_efecto_vel.value(),
            direccion=self.cbo_efecto_dir.currentData() or "diagonal",
            suavizado=self.chk_efecto_suave.isChecked(),
        )

    def _cargar_efecto_en_ui(self, efecto: ConfigEfecto) -> None:
        self._cargando_efecto = True
        try:
            idx = self.cbo_efecto.findData(efecto.tipo)
            self.cbo_efecto.setCurrentIndex(max(0, idx))
            self.spin_efecto_int.setValue(efecto.intensidad * 100.0)
            self.spin_efecto_dist.setValue(efecto.distancia * 100.0)
            self.spin_efecto_vel.setValue(efecto.velocidad)
            idx_dir = self.cbo_efecto_dir.findData(efecto.direccion)
            self.cbo_efecto_dir.setCurrentIndex(max(0, idx_dir))
            self.chk_efecto_suave.setChecked(efecto.suavizado)
        finally:
            self._cargando_efecto = False
        self._actualizar_controles_efecto()

    def _actualizar_controles_efecto(self) -> None:
        tipo = self.cbo_efecto.currentData() or "kenburns"
        activo = tipo != "none"
        self.spin_efecto_int.setEnabled(activo)
        self.spin_efecto_dist.setEnabled(tipo in ("zoom_in", "zoom_out", "kenburns", "pan"))
        self.spin_efecto_vel.setEnabled(tipo in ("pulse", "shake"))
        self.cbo_efecto_dir.setEnabled(tipo in ("zoom_in", "zoom_out", "kenburns", "pan"))
        self.chk_efecto_suave.setEnabled(activo)
        self.btn_quitar_efecto_clip.setEnabled(
            self.cbo_efecto_ambito.currentData() == "clip"
            and self._clip_actual is not None
            and self._clip_actual in self.efectos_clip
        )

    def _efecto_para_clip(self, indice: int) -> ConfigEfecto:
        datos = self.efectos_clip.get(indice)
        return ConfigEfecto.desde_dict(datos) if datos else self.efecto_global

    def _al_cambiar_efecto_ui(self, *_args) -> None:
        if self._cargando_efecto:
            return
        cfg = self._efecto_desde_ui()
        if self.cbo_efecto_ambito.currentData() == "clip" and self._clip_actual is not None:
            self.efectos_clip[int(self._clip_actual)] = cfg.a_dict()
            self.lbl_efecto_info.setText(
                f"Movimiento propio del clip {self._clip_actual + 1} "
                "(el resto usa el movimiento global)."
            )
        else:
            self.efecto_global = cfg
            self.lbl_efecto_info.setText("El movimiento se aplica a todos los clips.")
        self._actualizar_controles_efecto()
        self._marcar_proyecto_modificado()
        self._actualizar_timeline_y_monitor()

    def _al_cambiar_ambito_efecto(self, *_args) -> None:
        if self._cargando_efecto:
            return
        if self.cbo_efecto_ambito.currentData() == "clip":
            if self._clip_actual is None:
                self.lbl_efecto_info.setText(
                    "Selecciona un clip en la línea de tiempo para editarlo.")
            else:
                cfg = self._efecto_para_clip(int(self._clip_actual))
                self._cargar_efecto_en_ui(cfg)
                self.lbl_efecto_info.setText(f"Editando el clip {self._clip_actual + 1}.")
        else:
            self._cargar_efecto_en_ui(self.efecto_global)
            self.lbl_efecto_info.setText("El movimiento se aplica a todos los clips.")
        self._actualizar_controles_efecto()

    def _seleccionar_clip(self, indice: int) -> None:
        self._clip_actual = int(indice)
        if self.cbo_efecto_ambito.currentData() == "clip":
            self._cargar_efecto_en_ui(self._efecto_para_clip(self._clip_actual))
            self.lbl_efecto_info.setText(f"Editando el clip {self._clip_actual + 1}.")
        self._actualizar_controles_efecto()

    def _quitar_efecto_clip(self) -> None:
        if self._clip_actual is not None:
            self.efectos_clip.pop(int(self._clip_actual), None)
            self._cargar_efecto_en_ui(self.efecto_global)
            self.lbl_efecto_info.setText(
                f"Clip {self._clip_actual + 1}: usa el movimiento global.")
            self._marcar_proyecto_modificado()
            self._actualizar_timeline_y_monitor()

    # -------------------------------------------------- Estilo de subtítulos
    def _estilo_sub_desde_ui(self) -> EstiloSubtitulos:
        return EstiloSubtitulos(
            fuente=self.cbo_fuente_sub.currentFont().family() or "Segoe UI",
            tamano=self.spin_tam_sub.value(),
            negrita=self.chk_negrita_sub.isChecked(),
            color=self._color_sub,
            color_contorno=self._color_borde_sub,
            contorno=self.spin_contorno_sub.value(),
            caja=self.chk_caja_sub.isChecked(),
            caja_color=self._color_caja_sub,
            caja_opacidad=self.spin_caja_op_sub.value() / 100.0,
            posicion=self.cbo_pos_sub.currentData() or "abajo",
            margen=self.spin_margen_sub.value(),
            max_caracteres=int(self.spin_maxchars_sub.value()),
        )

    def _aplicar_estilo_sub_ui(self, estilo: EstiloSubtitulos) -> None:
        self._cargando_subs = True
        try:
            self.cbo_fuente_sub.setCurrentFont(QFont(estilo.fuente or "Segoe UI"))
            self.spin_tam_sub.setValue(estilo.tamano)
            self.chk_negrita_sub.setChecked(estilo.negrita)
            self.spin_contorno_sub.setValue(estilo.contorno)
            self.chk_caja_sub.setChecked(estilo.caja)
            self.spin_caja_op_sub.setValue(int(round(estilo.caja_opacidad * 100)))
            idx_pos = self.cbo_pos_sub.findData(estilo.posicion)
            self.cbo_pos_sub.setCurrentIndex(max(0, idx_pos))
            self.spin_margen_sub.setValue(estilo.margen)
            self.spin_maxchars_sub.setValue(int(estilo.max_caracteres))
            self._color_sub = estilo.color
            self._color_borde_sub = estilo.color_contorno
            self._color_caja_sub = estilo.caja_color
            self._pintar_boton_color(self.btn_color_sub, self._color_sub)
            self._pintar_boton_color(self.btn_color_borde_sub, self._color_borde_sub)
            self._pintar_boton_color(self.btn_color_caja_sub, self._color_caja_sub)
        finally:
            self._cargando_subs = False

    def _pintar_boton_color(self, boton: QPushButton, color: str) -> None:
        texto = "#000000" if QColor(color).lightness() > 150 else "#FFFFFF"
        boton.setStyleSheet(
            f"background: {color}; color: {texto}; border: 1px solid #475569; "
            "border-radius: 4px; padding: 4px 8px; font-weight: bold;"
        )

    def _elegir_color_sub(self, cual: str) -> None:
        actual = {
            "texto": self._color_sub,
            "borde": self._color_borde_sub,
            "caja": self._color_caja_sub,
        }.get(cual, "#FFFFFF")
        color = QColorDialog.getColor(QColor(actual), self, "Elegir color")
        if not color.isValid():
            return
        hex_color = color.name().upper()
        if cual == "texto":
            self._color_sub = hex_color
            self._pintar_boton_color(self.btn_color_sub, hex_color)
        elif cual == "borde":
            self._color_borde_sub = hex_color
            self._pintar_boton_color(self.btn_color_borde_sub, hex_color)
        else:
            self._color_caja_sub = hex_color
            self._pintar_boton_color(self.btn_color_caja_sub, hex_color)
        self._al_cambiar_estilo_sub()

    def _al_cambiar_estilo_sub(self, *_args) -> None:
        if self._cargando_subs:
            return
        self.estilo_subtitulos = self._estilo_sub_desde_ui()
        self._marcar_proyecto_modificado()
        self._actualizar_subtitulos_monitor()

    def _marcar_proyecto_modificado(self, *_args) -> None:
        self._proyecto_modificado = True
        self._actualizar_titulo_proyecto()

    def _actualizar_subtitulos_monitor(self, *_args) -> None:
        if not hasattr(self, "monitor"):
            return
        self.monitor.set_subtitulos(
            self.subtitulos, self.estilo_subtitulos,
            self.chk_sub_mostrar.isChecked(),
        )
        self._actualizar_estado_subs()

    # ----------------------------------------------------- Tabla de subtítulos
    def _refrescar_tabla_subtitulos(self) -> None:
        self.subtitulos.sort(key=lambda c: float(c.get("inicio", 0.0)))
        self._cargando_subs = True
        try:
            self.tabla_subs.setRowCount(len(self.subtitulos))
            for i, cue in enumerate(self.subtitulos):
                sp_ini = QDoubleSpinBox()
                sp_ini.setRange(0.0, 36000.0)
                sp_ini.setDecimals(2)
                sp_ini.setSuffix(" s")
                sp_ini.setValue(float(cue.get("inicio", 0.0)))
                sp_ini.valueChanged.connect(
                    lambda v, fila=i: self._cambiar_tiempo_sub(fila, "inicio", v))
                self.tabla_subs.setCellWidget(i, 0, sp_ini)

                sp_fin = QDoubleSpinBox()
                sp_fin.setRange(0.0, 36000.0)
                sp_fin.setDecimals(2)
                sp_fin.setSuffix(" s")
                sp_fin.setValue(float(cue.get("fin", 0.0)))
                sp_fin.valueChanged.connect(
                    lambda v, fila=i: self._cambiar_tiempo_sub(fila, "fin", v))
                self.tabla_subs.setCellWidget(i, 1, sp_fin)

                item = QTableWidgetItem(str(cue.get("texto", "")))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.tabla_subs.setItem(i, 2, item)
        finally:
            self._cargando_subs = False
        self._actualizar_estado_subs()

    def _actualizar_estado_subs(self) -> None:
        if not hasattr(self, "lbl_subs_estado"):
            return
        if self.subtitulos:
            n = len(self.subtitulos)
            visibles = "visibles en el monitor" if self.chk_sub_mostrar.isChecked() \
                else "ocultos en el monitor"
            self.lbl_subs_estado.setText(f"✓ {n} subtítulo{'s' if n != 1 else ''} · {visibles}")
        elif self.palabras_transcripcion:
            self.lbl_subs_estado.setText(
                "Hay voz transcrita: pulsa «🎙 Desde la voz» para crear los subtítulos.")
        else:
            self.lbl_subs_estado.setText(
                "Sin subtítulos todavía: detecta la voz o añade uno con «+ Subtítulo».")

    def _cambiar_tiempo_sub(self, fila: int, campo: str, valor: float) -> None:
        if not (0 <= fila < len(self.subtitulos)):
            return
        self.subtitulos[fila][campo] = float(valor)
        inicio = float(self.subtitulos[fila].get("inicio", 0.0))
        fin = float(self.subtitulos[fila].get("fin", 0.0))
        if fin <= inicio:
            self.subtitulos[fila]["fin"] = inicio + 0.5
        self._subs_auto = False
        self._marcar_proyecto_modificado()
        self._actualizar_subtitulos_monitor()

    def _al_editar_texto_sub(self, item: QTableWidgetItem) -> None:
        if self._cargando_subs or item.column() != 2:
            return
        fila = item.row()
        if 0 <= fila < len(self.subtitulos):
            self.subtitulos[fila]["texto"] = item.text().strip()
            self._subs_auto = False
            self._marcar_proyecto_modificado()
            self._actualizar_subtitulos_monitor()

    def _al_seleccionar_sub(self) -> None:
        """Lleva el monitor al inicio del subtítulo seleccionado."""
        if self._cargando_subs:
            return
        fila = self.tabla_subs.currentRow()
        if 0 <= fila < len(self.subtitulos):
            t = float(self.subtitulos[fila].get("inicio", 0.0)) + 0.05
            self.timeline.set_tiempo_actual(t)
            self.monitor.set_tiempo(t)

    def _anadir_subtitulo(self) -> None:
        t = max(0.0, float(self.monitor._tiempo_actual))
        fin = t + 3.0
        siguientes = [float(c.get("inicio", 0.0)) for c in self.subtitulos
                      if float(c.get("inicio", 0.0)) > t]
        if siguientes:
            fin = min(fin, min(siguientes) - 0.05)
        self.subtitulos.append({
            "inicio": round(t, 2),
            "fin": round(max(t + 0.4, fin), 2),
            "texto": "Nuevo subtítulo",
        })
        self._subs_auto = False
        self._refrescar_tabla_subtitulos()
        self._marcar_proyecto_modificado()
        self._actualizar_subtitulos_monitor()

    def _quitar_subtitulo(self) -> None:
        fila = self.tabla_subs.currentRow()
        if 0 <= fila < len(self.subtitulos):
            self.subtitulos.pop(fila)
            self._subs_auto = False
            self._refrescar_tabla_subtitulos()
            self._marcar_proyecto_modificado()
            self._actualizar_subtitulos_monitor()

    def _importar_subtitulos(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Importar subtítulos", "", "Subtítulos (*.srt *.vtt)")
        if not ruta:
            return
        try:
            cues = leer_subtitulos(ruta)
        except Exception as exc:
            QMessageBox.warning(self, "No se pudo importar", str(exc))
            return
        if not cues:
            QMessageBox.information(
                self, "Sin subtítulos", "El archivo no contiene subtítulos válidos.")
            return
        if self.subtitulos:
            resp = QMessageBox.question(
                self, "Subtítulos existentes",
                "Ya hay subtítulos. ¿Quieres reemplazarlos por los del archivo?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if resp != QMessageBox.StandardButton.Yes:
                self.subtitulos.extend(cues)
                self.subtitulos.sort(key=lambda c: float(c.get("inicio", 0.0)))
                self._subs_auto = False
                self._refrescar_tabla_subtitulos()
                self._marcar_proyecto_modificado()
                self._actualizar_subtitulos_monitor()
                return
        self.subtitulos = cues
        self._subs_auto = False
        self._refrescar_tabla_subtitulos()
        self._marcar_proyecto_modificado()
        self._actualizar_subtitulos_monitor()
        self.log.appendPlainText(f"[Subtítulos] Importados {len(cues)} de {Path(ruta).name}")

    def _exportar_subtitulos(self) -> None:
        if not self.subtitulos:
            QMessageBox.information(self, "Sin subtítulos", "No hay subtítulos para exportar.")
            return
        dir_videos = Path.home() / "Videos"
        dir_videos.mkdir(exist_ok=True)
        ruta, _ = QFileDialog.getSaveFileName(
            self, "Exportar subtítulos", str(dir_videos / "subtitulos.srt"),
            "SubRip (*.srt)")
        if not ruta:
            return
        if not ruta.lower().endswith(".srt"):
            ruta += ".srt"
        try:
            escribir_srt(self.subtitulos, ruta)
            self.lbl_estado.setText(f"Subtítulos exportados: {Path(ruta).name}")
            self.log.appendPlainText(f"[Subtítulos] Exportados a {ruta}")
        except Exception as exc:
            QMessageBox.warning(self, "Error al exportar", str(exc))

    def _datos_tiempo_salida(self) -> tuple[float, list[dict]]:
        """Duración total y marcas en la línea de tiempo final del video."""
        if not self.audios:
            return 0.0, []
        try:
            if self.combo_modo.currentIndex() == 0:
                duraciones = [duracion_audio(a) for a in self.audios]
                t = float(self.spin_trans.value())
                total = sum(duraciones) - t * max(0, len(duraciones) - 1)
                marcas: list[dict] = []
                acum = 0.0
                for i, audio in enumerate(self.audios):
                    marcas.append({"titulo": Path(audio).stem, "tiempo": max(0.0, acum)})
                    acum += duraciones[i] - t
                return max(0.0, total), marcas
            return duracion_audio(self.audios[0]), list(self.marcas)
        except Exception:
            return 0.0, list(self.marcas)

    def _generar_subtitulos_capitulos(self, automatico: bool = False) -> None:
        total, marcas = self._datos_tiempo_salida()
        if not marcas:
            self.log.appendPlainText(
                "[Subtítulos] No hay capítulos. Detecta o añade marcas primero.")
            return
        self.subtitulos = cues_desde_capitulos(marcas, total)
        self._subs_auto = automatico
        self._refrescar_tabla_subtitulos()
        if not automatico:
            self._marcar_proyecto_modificado()
        self._actualizar_subtitulos_monitor()
        self.log.appendPlainText(
            f"[Subtítulos] {len(self.subtitulos)} títulos de capítulo generados.")

    def _generar_subtitulos_voz(self, silencioso: bool = False,
                                automatico: bool = False) -> None:
        if not self.palabras_transcripcion:
            if not silencioso:
                QMessageBox.information(
                    self, "Sin transcripción",
                    "Primero pulsa «🎙 Auto-Detectar Capítulos» para transcribir la voz.\n"
                    "Después podrás generar subtítulos con las palabras y sus tiempos.")
            return
        self.subtitulos = cues_desde_palabras(
            self.palabras_transcripcion,
            max_caracteres=self.estilo_subtitulos.max_caracteres,
        )
        self._subs_auto = automatico
        self._refrescar_tabla_subtitulos()
        if not automatico:
            self._marcar_proyecto_modificado()
        self._actualizar_subtitulos_monitor()
        self.log.appendPlainText(
            f"[Subtítulos] {len(self.subtitulos)} bloques generados desde la voz.")

    # -------------------------------------------- Panel Inferior: Timeline
    def _construir_panel_timeline(self) -> QWidget:
        self.caja_tabla = QGroupBox("LÍNEA DE TIEMPO & CAPÍTULOS")
        lay = QVBoxLayout(self.caja_tabla)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)

        # Barra de herramientas de Timeline
        barra_tl = QHBoxLayout()
        barra_tl.setSpacing(4)

        # ── Herramientas de edición NLE ──
        estilo_tool_btn = (
            "QPushButton { padding: 4px 8px; border: 1px solid #334155; "
            "border-radius: 4px; background: #1e293b; color: #94a3b8; font-size: 11px; }"
            "QPushButton:hover { background: #334155; color: #e2e8f0; }"
            "QPushButton:checked { background: #0ea5e9; color: #ffffff; border-color: #0ea5e9; }"
        )

        self.btn_tool_select = QPushButton("↖ Selección [V]")
        self.btn_tool_select.setCheckable(True)
        self.btn_tool_select.setChecked(True)
        self.btn_tool_select.setToolTip("Herramienta de Selección – Mover y recortar clips (V)")
        self.btn_tool_select.setStyleSheet(estilo_tool_btn)
        self.btn_tool_select.clicked.connect(lambda: self._cambiar_herramienta_timeline("select"))
        barra_tl.addWidget(self.btn_tool_select)

        self.btn_tool_razor = QPushButton("✂ Cuchilla [C]")
        self.btn_tool_razor.setCheckable(True)
        self.btn_tool_razor.setToolTip("Herramienta Cuchilla – Cortar clips al hacer clic (C)")
        self.btn_tool_razor.setStyleSheet(estilo_tool_btn)
        self.btn_tool_razor.clicked.connect(lambda: self._cambiar_herramienta_timeline("razor"))
        barra_tl.addWidget(self.btn_tool_razor)

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.VLine)
        sep1.setStyleSheet("color: #334155;")
        barra_tl.addWidget(sep1)

        self.btn_split_playhead = QPushButton("✂ Dividir [Ctrl+K]")
        self.btn_split_playhead.setToolTip("Dividir clip en la posición del cabezal (Ctrl+K)")
        self.btn_split_playhead.setStyleSheet(estilo_tool_btn)
        self.btn_split_playhead.clicked.connect(self._dividir_en_playhead)
        barra_tl.addWidget(self.btn_split_playhead)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.VLine)
        sep2.setStyleSheet("color: #334155;")
        barra_tl.addWidget(sep2)

        self.btn_snap = QPushButton("🧲 Imán [S]")
        self.btn_snap.setCheckable(True)
        self.btn_snap.setChecked(True)
        self.btn_snap.setToolTip("Activar/Desactivar ajuste magnético (Snap) [S]")
        self.btn_snap.setStyleSheet(estilo_tool_btn)
        self.btn_snap.clicked.connect(self._toggle_snap_timeline)
        barra_tl.addWidget(self.btn_snap)

        sep3 = QFrame()
        sep3.setFrameShape(QFrame.Shape.VLine)
        sep3.setStyleSheet("color: #334155;")
        barra_tl.addWidget(sep3)

        self.btn_add_marca = QPushButton("+ Marca")
        self.btn_add_marca.setToolTip("Añadir una marca de capítulo en la posición actual")
        self.btn_add_marca.setStyleSheet(estilo_tool_btn)
        self.btn_add_marca.clicked.connect(self._anadir_marca)
        barra_tl.addWidget(self.btn_add_marca)

        self.btn_del_marca = QPushButton("- Marca")
        self.btn_del_marca.setToolTip("Eliminar la marca seleccionada")
        self.btn_del_marca.setStyleSheet(estilo_tool_btn)
        self.btn_del_marca.clicked.connect(self._quitar_marca)
        barra_tl.addWidget(self.btn_del_marca)

        self.btn_calc = QPushButton("⏱ Duración")
        self.btn_calc.setStyleSheet(estilo_tool_btn)
        self.btn_calc.clicked.connect(self._estimar_duracion)
        barra_tl.addWidget(self.btn_calc)

        self.lbl_total = QLabel("")
        self.lbl_total.setStyleSheet("color: #38bdf8; font-weight: 500; font-size: 11px;")
        barra_tl.addWidget(self.lbl_total)

        barra_tl.addStretch()

        # ── Zoom horizontal del Timeline ──
        lbl_zoom = QLabel("🔍")
        lbl_zoom.setStyleSheet("color: #64748b; font-size: 13px;")
        barra_tl.addWidget(lbl_zoom)

        self.slider_zoom_tl = QSlider(Qt.Orientation.Horizontal)
        self.slider_zoom_tl.setRange(50, 500)  # 0.5x a 5.0x
        self.slider_zoom_tl.setValue(100)  # 1.0x por defecto
        self.slider_zoom_tl.setFixedWidth(110)
        self.slider_zoom_tl.setToolTip("Zoom horizontal del Timeline")
        self.slider_zoom_tl.setStyleSheet(
            "QSlider::groove:horizontal { height: 4px; background: #334155; border-radius: 2px; }"
            "QSlider::handle:horizontal { width: 12px; height: 12px; background: #0ea5e9; "
            "border-radius: 6px; margin: -4px 0; }"
            "QSlider::sub-page:horizontal { background: #0ea5e9; border-radius: 2px; }"
        )
        self.slider_zoom_tl.valueChanged.connect(
            lambda v: self.timeline.set_zoom_factor(v / 100.0)
        )
        barra_tl.addWidget(self.slider_zoom_tl)

        self.lbl_zoom_pct = QLabel("100%")
        self.lbl_zoom_pct.setStyleSheet("color: #64748b; font-size: 10px; min-width: 35px;")
        self.slider_zoom_tl.valueChanged.connect(
            lambda v: self.lbl_zoom_pct.setText(f"{v}%")
        )
        barra_tl.addWidget(self.lbl_zoom_pct)

        # Tabs para alternar entre vista de Pistas (Timeline) y Ajuste Fino (Tabla)
        self.tab_timeline_modo = QTabWidget()
        self.tab_timeline_modo.setStyleSheet("QTabWidget::pane { border: none; }")

        # Pestaña 1: Visual Timeline
        self.timeline = LineaDeTiempoWidget(self)
        self.timeline.tiempo_cambiado.connect(self._al_cambiar_tiempo_timeline)
        self.timeline.marca_seleccionada.connect(self._al_seleccionar_marca_timeline)
        self.timeline.marca_tiempo_modificada.connect(self._al_modificar_marca_timeline)
        self.timeline.solicitud_asignar_imagen.connect(self._asignar_imagen_a_capitulo)
        self.timeline.solicitud_importar_archivos.connect(self._importar_rutas_arrastradas)
        self.timeline.solicitud_cortar_en_tiempo.connect(self._cortar_en_tiempo)
        self.timeline.herramienta_cambiada.connect(self._al_cambiar_herramienta_desde_timeline)

        w_tl_visual = QWidget()
        lay_tl_v = QVBoxLayout(w_tl_visual)
        lay_tl_v.setContentsMargins(0, 0, 0, 0)
        lay_tl_v.addWidget(self.timeline)

        # Pestaña 2: Ajuste Fino / Modo Tabla
        w_ajuste_fino = QWidget()
        lay_af = QVBoxLayout(w_ajuste_fino)
        lay_af.setContentsMargins(0, 0, 0, 0)

        self._cuerpo_emparejar = self._panel_emparejamiento()
        self._cuerpo_marcas = self._panel_marcas()
        lay_af.addWidget(self._cuerpo_emparejar)
        lay_af.addWidget(self._cuerpo_marcas)
        self._cuerpo_marcas.setVisible(False)

        self.tab_timeline_modo.addTab(w_tl_visual, "🎚 Pistas Visuales (Timeline)")
        self.tab_timeline_modo.addTab(w_ajuste_fino, "📋 Ajuste Fino / Tabla de Capítulos")

        lay.addLayout(barra_tl)
        lay.addWidget(self.tab_timeline_modo, 1)

        return self.caja_tabla

    def _panel_emparejamiento(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabla = TablaMarcasDroppable(0, 2)
        self.tabla.setHorizontalHeaderLabels(["Audio (en orden)", "Imagen asignada"])
        self.tabla.horizontalHeader().setStretchLastSection(True)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.itemSelectionChanged.connect(self._fila_audio_seleccionada)
        lay.addWidget(self.tabla, 1)
        return w

    def _panel_marcas(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tabla_marcas = TablaMarcasDroppable(0, 3)
        self.tabla_marcas.setHorizontalHeaderLabels(["Capítulo", "Segundo exacto", "Imagen"])
        self.tabla_marcas.setColumnWidth(1, 140)
        self.tabla_marcas.horizontalHeader().setStretchLastSection(True)
        self.tabla_marcas.verticalHeader().setVisible(False)
        self.tabla_marcas.itemSelectionChanged.connect(self._fila_marca_seleccionada)
        lay.addWidget(self.tabla_marcas, 1)
        return w

    # --------------------------------------------- Barra Inferior / Consola
    def _construir_barra_inferior(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 2, 0, 0)
        lay.setSpacing(4)

        # Fila de Acciones Finales & Estado
        fila_acciones = QHBoxLayout()
        fila_acciones.setSpacing(8)

        self.lbl_estado = QLabel("Listo para editar.")
        self.lbl_estado.setStyleSheet("color: #94a3b8; font-weight: 500;")
        fila_acciones.addWidget(self.lbl_estado, 1)

        self.btn_prev = QPushButton("⚡ Vista Rápida")
        self.btn_prev.clicked.connect(self._probar_estilo)
        fila_acciones.addWidget(self.btn_prev)

        self.btn_generar = QPushButton("🎬 Generar Video")
        self.btn_generar.setObjectName("primary")
        self.btn_generar.clicked.connect(self._generar)
        fila_acciones.addWidget(self.btn_generar)

        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.clicked.connect(self._cancelar)
        self.btn_cancelar.setEnabled(False)
        fila_acciones.addWidget(self.btn_cancelar)

        self.btn_ver = QPushButton("▶ Ver Video")
        self.btn_ver.clicked.connect(self._ver_video)
        self.btn_ver.setEnabled(False)
        fila_acciones.addWidget(self.btn_ver)

        self.btn_abrir = QPushButton("📂 Carpeta")
        self.btn_abrir.clicked.connect(self._abrir_carpeta)
        self.btn_abrir.setEnabled(False)
        fila_acciones.addWidget(self.btn_abrir)

        self.btn_toggle_log = QPushButton("📜 Consola")
        self.btn_toggle_log.setCheckable(True)
        self.btn_toggle_log.setChecked(False)
        self.btn_toggle_log.toggled.connect(self._toggle_consola)
        fila_acciones.addWidget(self.btn_toggle_log)

        lay.addLayout(fila_acciones)

        # Barra de Progreso
        self.barra = QProgressBar()
        self.barra.setRange(0, 100)
        self.barra.setValue(0)
        self.barra.setTextVisible(False)
        lay.addWidget(self.barra)

        # Consola de Log (Plegable)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFixedHeight(95)
        self.log.setVisible(False)
        lay.addWidget(self.log)

        return w

    def _toggle_consola(self, activo: bool) -> None:
        self.log.setVisible(activo)

    # ---------------------------------------------------------------- Modos
    def _aplicar_modo(self) -> None:
        idx = self.combo_modo.currentIndex()
        multi = idx == 0
        self.chk_auto.setVisible(multi)
        self.btn_add_audio.setVisible(idx != 2)
        self.btn_detectar.setVisible(idx != 0)
        self.btn_detectar_top.setVisible(idx != 0)
        self.zona_tts.setVisible(idx == 2)

        self.btn_add_audio.setText(
            "+ Añadir audios…" if multi else "Elegir audio completo…"
        )
        self.caja_tabla.setTitle(
            "EMPAREJAMIENTO & LÍNEA DE TIEMPO" if multi else "CAPÍTULOS & LÍNEA DE TIEMPO"
        )
        if idx == 2:
            self.caja_tabla.setTitle("CAPÍTULOS DEL AUDIO GENERADO")

        self._cuerpo_emparejar.setVisible(multi)
        self._cuerpo_marcas.setVisible(not multi)

        if idx == 2:
            self._popular_voces()

        if not multi:
            if len(self.audios) > 1:
                self.audios = self.audios[:1]
                self.lista_audios.clear()
                for a in self.audios:
                    self.lista_audios.addItem(Path(a).name)
            self._refrescar_marcas()
        else:
            self._refrescar_tabla()

        self._actualizar_timeline_y_monitor()
        self._guardar_sesion()

    # ------------------------------------------------------------- Medios
    def _anadir_audios(self) -> None:
        unico = self.combo_modo.currentIndex() != 0
        rutas, _ = QFileDialog.getOpenFileNames(
            self, "Elegir audio completo" if unico else "Añadir audios", "",
            f"Audio ({AUDIO_EXT})")
        if not rutas:
            return
        self.audios = [rutas[0]] if unico else self.audios + rutas
        self.lista_audios.clear()
        for a in self.audios:
            self.lista_audios.addItem("Audio completo" if unico else f"{Path(a).name}")
        if unico:
            self._refrescar_marcas()
        elif self.chk_auto.isChecked():
            self._reordenar()

        self._proyecto_modificado = True
        self._actualizar_titulo_proyecto()
        self._actualizar_timeline_y_monitor()
        self._guardar_sesion()

    def _anadir_imagenes(self) -> None:
        rutas, _ = QFileDialog.getOpenFileNames(
            self, "Añadir imágenes", "", f"Imágenes ({IMAGEN_EXT})")
        if not rutas:
            return
        self.imagenes += rutas
        for r in rutas:
            item = QListWidgetItem(QIcon(r), Path(r).name)
            self.lista_imagenes.addItem(item)

        for k, v in list(self.img_manual.items()):
            if v == "":
                self.img_manual.pop(k)

        self._proyecto_modificado = True
        self._actualizar_titulo_proyecto()
        self._refrescar_tabla()
        self._refrescar_marcas()
        self._actualizar_timeline_y_monitor()
        self._guardar_sesion()

    def _reordenar(self) -> None:
        if self.chk_auto.isChecked():
            self.audios = ordering.ordenar_audios(self.audios)
        self.lista_audios.clear()
        for a in self.audios:
            clave = ordering.clave_audio(a)
            etiqueta = "intro" if clave[0] == -1 else "capítulo"
            self.lista_audios.addItem(f"{etiqueta}  ·  {Path(a).name}")
        self._refrescar_tabla()
        self._actualizar_timeline_y_monitor()
        self._guardar_sesion()

    def _refrescar_tabla(self) -> None:
        if self.combo_modo.currentIndex() != 0:
            return
        for i in list(self.img_manual):
            if i >= len(self.audios) or (
                self.img_manual[i] and self.img_manual[i] not in self.imagenes
            ):
                self.img_manual.pop(i, None)
        auto = ordering.emparejar_imagenes(self.audios, self.imagenes)
        self.tabla.setRowCount(len(self.audios))
        for fila_i, audio in enumerate(self.audios):
            self.tabla.setItem(fila_i, 0, QTableWidgetItem(Path(audio).name))
            combo = self._combo_imagenes(self.img_manual.get(fila_i) or auto[fila_i])
            combo.currentIndexChanged.connect(
                lambda _i, f=fila_i, c=combo: self._cambio_imagen(f, c))
            self.tabla.setCellWidget(fila_i, 1, combo)

    def _combo_imagenes(self, asignada: str | None) -> QComboBox:
        combo = QComboBox()
        combo.addItem("(sin imagen)", "")
        for r in self.imagenes:
            combo.addItem(QIcon(r), Path(r).name, r)
        if asignada:
            idx = combo.findData(asignada)
            if idx >= 0:
                combo.setCurrentIndex(idx)
        return combo

    def _cambio_imagen(self, fila: int, combo: QComboBox) -> None:
        self.img_manual[fila] = combo.currentData() or ""
        self._proyecto_modificado = True
        self._actualizar_titulo_proyecto()
        self._actualizar_timeline_y_monitor()

    # ------------------------------------------------------------- Marcas
    def _refrescar_marcas(self) -> None:
        if self.combo_modo.currentIndex() == 0:
            return
        # Emparejamiento inteligente por nombre: intro, capítulo N, cierre
        auto_img = ordering.emparejar_imagenes_por_capitulo(self.marcas, self.imagenes)

        self.tabla_marcas.setRowCount(len(self.marcas))
        for fila_i, marca in enumerate(self.marcas):
            self.tabla_marcas.setItem(
                fila_i, 0, QTableWidgetItem(marca.get("titulo", f"Marca {fila_i + 1}")))
            spin = QDoubleSpinBox()
            spin.setRange(0.0, 36000.0)
            spin.setDecimals(2)
            spin.setSuffix(" s")
            spin.setValue(float(marca.get("tiempo", 0.0)))
            spin.valueChanged.connect(
                lambda val, f=fila_i: self._cambio_marca(f, val))
            self.tabla_marcas.setCellWidget(fila_i, 1, spin)

            # Prioridad: manual > auto por nombre > None
            asig = self.img_manual.get(fila_i) or auto_img.get(fila_i)
            combo = self._combo_imagenes(asig)
            combo.currentIndexChanged.connect(
                lambda _i, f=fila_i, c=combo: self._cambio_imagen(f, c))
            self.tabla_marcas.setCellWidget(fila_i, 2, combo)

        self._actualizar_timeline_y_monitor()

    def _cambio_marca(self, fila: int, valor: float) -> None:
        if 0 <= fila < len(self.marcas):
            self.marcas[fila]["tiempo"] = float(valor)
            self._proyecto_modificado = True
            self._actualizar_titulo_proyecto()
            self._actualizar_timeline_y_monitor()

    def _anadir_marca(self) -> None:
        n = len(self.marcas)
        tiempo_base = self.monitor._tiempo_actual if hasattr(self, "monitor") else 0.0
        self.marcas.append({"titulo": f"Capítulo {n + 1}", "tiempo": round(tiempo_base, 1)})
        self.marcas.sort(key=lambda m: float(m.get("tiempo", 0.0)))
        self._proyecto_modificado = True
        self._actualizar_titulo_proyecto()
        self._refrescar_marcas()

    def _quitar_marca(self) -> None:
        fila = self.tabla_marcas.currentRow()
        if fila >= 0 and fila < len(self.marcas):
            self.marcas.pop(fila)
            manual = [v for k, v in self.img_manual.items() if k != fila]
            self.img_manual = dict(enumerate(manual))
            self._proyecto_modificado = True
            self._actualizar_titulo_proyecto()
            self._refrescar_marcas()

    def _quitar_audio(self) -> None:
        fila = self.lista_audios.currentRow()
        if fila >= 0 and self.audios:
            self.audios.pop(fila)
            if self.combo_modo.currentIndex() != 0:
                self.lista_audios.clear()
                self._refrescar_marcas()
            else:
                self._reordenar()
            self._proyecto_modificado = True
            self._actualizar_titulo_proyecto()
            self._actualizar_timeline_y_monitor()
            self._guardar_sesion()

    def _quitar_imagen(self) -> None:
        fila = self.lista_imagenes.currentRow()
        if fila >= 0 and fila < len(self.imagenes):
            ruta = self.imagenes.pop(fila)
            self.lista_imagenes.takeItem(fila)
            self.img_manual = {k: v for k, v in self.img_manual.items() if v != ruta}
            self._proyecto_modificado = True
            self._actualizar_titulo_proyecto()
            self._refrescar_tabla()
            self._refrescar_marcas()
            self._actualizar_timeline_y_monitor()
            self._guardar_sesion()

    def _mover(self, delta: int) -> None:
        fila = self.lista_audios.currentRow()
        destino = fila + delta
        if (self.audios and 0 <= fila < len(self.audios)
                and 0 <= destino < len(self.audios)):
            self.audios[fila], self.audios[destino] = (
                self.audios[destino], self.audios[fila])
            if self.chk_auto.isChecked():
                self.chk_auto.setChecked(False)
            self._proyecto_modificado = True
            self._actualizar_titulo_proyecto()
            self._reordenar()
            self.lista_audios.setCurrentRow(destino)

    # --------------------------------------------- Sincronización Timeline/Monitor
    def _actualizar_timeline_y_monitor(self) -> None:
        dur = 60.0
        nombre_a = "Sin audio"
        ruta_a = ""
        try:
            if self.audios:
                ruta_a = self.audios[0]
                nombre_a = Path(ruta_a).name
                if self.combo_modo.currentIndex() == 0:
                    dur = sum(duracion_audio(a) for a in self.audios)
                    dur -= self.spin_trans.value() * max(0, len(self.audios) - 1)
                else:
                    dur = duracion_audio(ruta_a)
            elif self.imagenes:
                dur = max(5.0, len(self.imagenes) * 5.0)
        except Exception:
            dur = max(10.0, len(self.imagenes) * 5.0)

        segmentos_en_vivo = []
        marcas_vis = []

        if self.combo_modo.currentIndex() == 0:
            # Modo varios audios
            if self.audios:
                auto = ordering.emparejar_imagenes(self.audios, self.imagenes)
                t_acum = 0.0
                t_trans = self.spin_trans.value()
                for i, a in enumerate(self.audios):
                    try:
                        d_a = duracion_audio(a)
                    except Exception:
                        d_a = 10.0
                    img = (
                        self.img_manual.get(i)
                        or (auto[i] if i < len(auto) and auto[i] else None)
                        or (self.imagenes[i] if i < len(self.imagenes) else None)
                        or (self.imagenes[0] if self.imagenes else "")
                    )
                    tit = Path(a).stem
                    # Igual que el render: cada clip entra con el crossfade
                    inicio = max(0.0, t_acum - i * t_trans)
                    segmentos_en_vivo.append({
                        "inicio": inicio,
                        "fin": inicio + d_a,
                        "imagen": img,
                        "titulo": tit,
                    })
                    marcas_vis.append({
                        "titulo": tit,
                        "tiempo": inicio,
                        "imagen": img,
                    })
                    t_acum += d_a
            elif self.imagenes:
                # Solo hay imágenes añadidas (sin audio aún)
                dur_img = 5.0
                for i, img in enumerate(self.imagenes):
                    t_ini = i * dur_img
                    t_fin = (i + 1) * dur_img
                    tit = f"Imagen {i + 1}"
                    segmentos_en_vivo.append({
                        "inicio": t_ini,
                        "fin": t_fin,
                        "imagen": img,
                        "titulo": tit,
                    })
                    marcas_vis.append({
                        "titulo": tit,
                        "tiempo": t_ini,
                        "imagen": img,
                    })
        else:
            # Modo un solo audio continuo (con marcas o auto-distribución)
            if self.marcas:
                # Emparejamiento inteligente por nombre de archivo
                auto_img = ordering.emparejar_imagenes_por_capitulo(self.marcas, self.imagenes)

                tiempos = [0.0]
                for m in sorted(self.marcas, key=lambda x: float(x.get("tiempo", 0.0))):
                    t = float(m.get("tiempo", 0.0))
                    if 0.0 < t < dur:
                        tiempos.append(t)
                tiempos.append(dur)

                for i in range(len(tiempos) - 1):
                    t_ini = tiempos[i]
                    t_fin = tiempos[i + 1]
                    img = (
                        self.img_manual.get(i)
                        or auto_img.get(i)
                        or (self.imagenes[-1] if self.imagenes else "")
                    )
                    tit = (self.marcas[i].get("titulo") if i < len(self.marcas) else f"Capítulo {i+1}")
                    segmentos_en_vivo.append({
                        "inicio": t_ini,
                        "fin": t_fin,
                        "imagen": img,
                        "titulo": tit or f"Capítulo {i+1}",
                    })
                    marcas_vis.append({
                        "titulo": tit or f"Capítulo {i+1}",
                        "tiempo": t_ini,
                        "imagen": img,
                    })
            elif self.imagenes:
                # Hay imágenes pero aún no se han detectado marcas: distribuir equitativamente
                dur_porcion = dur / max(1, len(self.imagenes))
                for i, img in enumerate(self.imagenes):
                    t_ini = i * dur_porcion
                    t_fin = (i + 1) * dur_porcion
                    tit = "Intro" if i == 0 else f"Capítulo {i}"
                    segmentos_en_vivo.append({
                        "inicio": t_ini,
                        "fin": t_fin,
                        "imagen": img,
                        "titulo": tit,
                    })
                    marcas_vis.append({
                        "titulo": tit,
                        "tiempo": t_ini,
                        "imagen": img,
                    })

        # Cada clip usa su efecto propio si lo tiene; si no, el global.
        for i, seg in enumerate(segmentos_en_vivo):
            seg["efecto"] = self._efecto_para_clip(i).a_dict()
        for i, marca in enumerate(marcas_vis):
            marca["efecto"] = self._efecto_para_clip(i)

        self.timeline.set_datos(marcas_vis, dur, nombre_a)
        trans_seg = self.spin_trans.value()
        self.monitor.set_todas_las_imagenes(self.imagenes)
        self.monitor.set_datos_en_vivo(
            segmentos_en_vivo, dur, trans_seg, self.efecto_global.intensidad,
            ruta_a, efecto=self.efecto_global,
        )

        # Si aún no hay subtítulos (o los actuales se generaron solos),
        # crearlos automáticamente con el título de cada capítulo.
        if (hasattr(self, "tabla_subs") and self.chk_sub_auto.isChecked()
                and self.audios and not self.palabras_transcripcion and marcas_vis
                and (not self.subtitulos or self._subs_auto)):
            self.subtitulos = cues_desde_capitulos(marcas_vis, dur)
            self._subs_auto = True
            self._refrescar_tabla_subtitulos()

        self._actualizar_subtitulos_monitor()
        self.monitor.set_tiempo(self.timeline._tiempo_actual, dur)

    def _al_cambiar_tiempo_timeline(self, t: float) -> None:
        self.monitor.set_tiempo(t)

    def _al_cambiar_tiempo_monitor(self, t: float) -> None:
        self.timeline.set_tiempo_actual(t)

    def _al_seleccionar_marca_timeline(self, idx: int) -> None:
        if 0 <= idx < self.tabla_marcas.rowCount():
            self.tabla_marcas.selectRow(idx)
        self._seleccionar_clip(idx)

    def _al_modificar_marca_timeline(self, idx: int, nuevo_t: float) -> None:
        if 0 <= idx < self.tabla_marcas.rowCount():
            spin = self.tabla_marcas.cellWidget(idx, 1)
            if spin:
                spin.setValue(nuevo_t)

    # ------------------------------------------- Herramientas NLE de Edición
    def _cambiar_herramienta_timeline(self, modo: str) -> None:
        """Cambia la herramienta activa en el timeline (select / razor)."""
        focus = self.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit)):
            return  # No interferir si se edita texto
        self.timeline.set_herramienta(modo)
        self.btn_tool_select.setChecked(modo == "select")
        self.btn_tool_razor.setChecked(modo == "razor")
        nombre = "Selección" if modo == "select" else "Cuchilla"
        self.lbl_estado.setText(f"🔧 Herramienta: {nombre}")

    def _al_cambiar_herramienta_desde_timeline(self, modo: str) -> None:
        """Actualiza UI cuando el timeline cambia de herramienta internamente."""
        self.btn_tool_select.setChecked(modo == "select")
        self.btn_tool_razor.setChecked(modo == "razor")

    def _toggle_snap_timeline(self) -> None:
        """Alterna el snapping magnético en el timeline."""
        focus = self.focusWidget()
        if isinstance(focus, (QLineEdit, QPlainTextEdit)):
            return
        activo = not self.timeline._snap_activo
        self.timeline.set_snap(activo)
        self.btn_snap.setChecked(activo)
        estado = "activado" if activo else "desactivado"
        self.lbl_estado.setText(f"🧲 Imán (Snap) {estado}")

    def _dividir_en_playhead(self) -> None:
        """Corta el clip en la posición actual del cabezal de reproducción."""
        self.timeline.dividir_en_playhead()

    def _cortar_en_tiempo(self, t: float) -> None:
        """Inserta un nuevo punto de corte (marca) en el segundo t dado.

        Busca el segmento que contiene 't', lo divide en dos y la imagen
        del segmento original se hereda al nuevo para mantener continuidad.
        """
        t = round(t, 2)
        if t <= 0.0:
            return

        # Verificar que no exista ya una marca muy cercana (< 0.5s)
        for m in self.marcas:
            if abs(float(m.get("tiempo", 0.0)) - t) < 0.5:
                self.lbl_estado.setText(f"⚠ Ya existe una marca cerca de {t:.1f}s")
                return

        # Encontrar el índice del segmento que se está cortando
        idx_segmento = 0
        for i, m in enumerate(self.marcas):
            if float(m.get("tiempo", 0.0)) <= t:
                idx_segmento = i + 1

        # Crear la nueva marca
        nueva_marca = {
            "titulo": f"Corte {t:.1f}s",
            "tiempo": t
        }

        # Heredar la imagen del segmento que se cortó
        if idx_segmento - 1 in self.img_manual:
            self.img_manual[idx_segmento] = self.img_manual[idx_segmento - 1]
            # Desplazar las asignaciones de imágenes posteriores
            nuevo_manual: dict[int, str] = {}
            for k, v in self.img_manual.items():
                if k >= idx_segmento and k != idx_segmento:
                    nuevo_manual[k + 1] = v
                else:
                    nuevo_manual[k] = v
            self.img_manual = nuevo_manual

        self.marcas.append(nueva_marca)
        self.marcas.sort(key=lambda m: float(m.get("tiempo", 0.0)))
        self._proyecto_modificado = True
        self._actualizar_titulo_proyecto()
        self._refrescar_marcas()
        self._actualizar_timeline_y_monitor()
        self._guardar_sesion()

        self.lbl_estado.setText(f"✂ Corte añadido en {t:.1f}s")
        self.log.appendPlainText(f"[Edición] Corte de clip en {t:.1f}s")

    def _fila_marca_seleccionada(self) -> None:
        fila = self.tabla_marcas.currentRow()
        if 0 <= fila < len(self.marcas):
            self._seleccionar_clip(fila)
            t = float(self.marcas[fila].get("tiempo", 0.0))
            self.timeline.set_tiempo_actual(t)
            self.monitor.set_tiempo(t)

    def _fila_audio_seleccionada(self) -> None:
        """Selección de una fila de audio en el modo de varios audios."""
        fila = self.tabla.currentRow()
        if 0 <= fila < len(self.audios):
            self._seleccionar_clip(fila)
            t = 0.0
            try:
                t = sum(duracion_audio(a) for a in self.audios[:fila])
                t -= self.spin_trans.value() * fila
            except ErrorMedia:
                t = 0.0
            self.timeline.set_tiempo_actual(max(0.0, t))
            self.monitor.set_tiempo(max(0.0, t))

    def _imagen_seleccionada_en_lista(self, actual: QListWidgetItem, previo) -> None:
        # Un clic simple en la lista NO debe alterar el video en vivo del monitor.
        # Solo actualiza la barra de estado con la información del archivo.
        if actual:
            fila = self.lista_imagenes.row(actual)
            if 0 <= fila < len(self.imagenes):
                r = self.imagenes[fila]
                self.lbl_estado.setText(f"Seleccionado: {Path(r).name} (arrástralo al timeline para asignarlo)")

    # ------------------------------------------------------------- Drag & Drop
    def _asignar_imagen_a_capitulo(self, idx: int, ruta_img: str) -> None:
        """Asigna una imagen arrastrada a un capítulo o segmento específico."""
        if not ruta_img or not Path(ruta_img).exists():
            return
        if ruta_img not in self.imagenes:
            self.imagenes.append(ruta_img)
            item = QListWidgetItem(QIcon(ruta_img), Path(ruta_img).name)
            item.setData(Qt.ItemDataRole.UserRole, ruta_img)
            item.setToolTip(ruta_img)
            self.lista_imagenes.addItem(item)

        self.img_manual[idx] = ruta_img
        self._proyecto_modificado = True
        self._actualizar_titulo_proyecto()

        if self.combo_modo.currentIndex() == 0:
            self._refrescar_tabla()
        else:
            self._refrescar_marcas()

        self._actualizar_timeline_y_monitor()
        self._guardar_sesion()

        nombre_cap = f"Capítulo {idx + 1}"
        if idx < len(self.marcas):
            nombre_cap = self.marcas[idx].get("titulo", nombre_cap)
        self.lbl_estado.setText(f"✓ Imagen '{Path(ruta_img).name}' asignada a: {nombre_cap}")
        self.log.appendPlainText(f"[Timeline] Asignada imagen '{Path(ruta_img).name}' a {nombre_cap}")

    def _importar_rutas_arrastradas(self, rutas: list[str], tipo: str = "auto") -> None:
        """Importa archivos arrastrados desde el explorador a las listas de medios."""
        exts_img = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        exts_aud = (".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma")

        imgs_nuevas = []
        auds_nuevos = []

        for r in rutas:
            ext = Path(r).suffix.lower()
            if ext in exts_img:
                imgs_nuevas.append(r)
            elif ext in exts_aud:
                auds_nuevos.append(r)

        cambio = False
        if imgs_nuevas:
            for r in imgs_nuevas:
                if r not in self.imagenes:
                    self.imagenes.append(r)
                    item = QListWidgetItem(QIcon(r), Path(r).name)
                    item.setData(Qt.ItemDataRole.UserRole, r)
                    item.setToolTip(r)
                    self.lista_imagenes.addItem(item)
            cambio = True

        if auds_nuevos:
            unico = self.combo_modo.currentIndex() != 0
            if unico:
                self.audios = [auds_nuevos[0]]
                self.lista_audios.clear()
                self.lista_audios.addItem("Audio completo · " + Path(auds_nuevos[0]).name)
            else:
                for a in auds_nuevos:
                    if a not in self.audios:
                        self.audios.append(a)
                        item = QListWidgetItem(Path(a).name)
                        item.setData(Qt.ItemDataRole.UserRole, a)
                        item.setToolTip(a)
                        self.lista_audios.addItem(item)
            cambio = True

        if cambio:
            self._proyecto_modificado = True
            self._actualizar_titulo_proyecto()
            if self.combo_modo.currentIndex() == 0:
                self._refrescar_tabla()
            else:
                self._refrescar_marcas()
            self._actualizar_timeline_y_monitor()
            self._guardar_sesion()
            self.lbl_estado.setText(
                f"Importados por arrastre: {len(imgs_nuevas)} imágenes, {len(auds_nuevos)} audios."
            )

    # --------------------------------------------- Gestión de Proyecto (.stcut)
    def _recopilar_datos_proyecto(self) -> dict:
        """Empaqueta todo el estado del proyecto para guardarlo en un archivo .stcut."""
        return {
            "audios": list(self.audios),
            "imagenes": list(self.imagenes),
            "marcas": list(self.marcas),
            "img_manual": {str(k): v for k, v in self.img_manual.items()},
            "modo": self.combo_modo.currentIndex(),
            "guion": self.txt_script.toPlainText(),
            "voz": self.combo_voz.currentData() or "",
            "velocidad": self.spin_vel.value(),
            "resolucion": self.cbo_res.currentText(),
            "resolucion_idx": self.cbo_res.currentIndex(),
            "transicion": self.spin_trans.value(),
            "estilo_transicion": self.cbo_estilo.currentIndex(),
            # Movimiento (global + excepciones por clip)
            "efecto": self.efecto_global.a_dict(),
            "efectos_clip": {str(k): dict(v) for k, v in self.efectos_clip.items()},
            # Subtítulos
            "subtitulos": [dict(c) for c in self.subtitulos],
            "estilo_subtitulos": self.estilo_subtitulos.a_dict(),
            "palabras": list(self.palabras_transcripcion),
            "quemar_subtitulos": self.chk_quemar_subtitulos.isChecked(),
            "mostrar_subtitulos": self.chk_sub_mostrar.isChecked(),
            "sub_auto": self.chk_sub_auto.isChecked(),
            # Inteligencia artificial (nunca incluye claves)
            "ia": self._ia_config.a_dict(),
            "youtube": {
                "titulo": self.txt_titulo.text(),
                "descripcion": self.txt_desc.toPlainText(),
                "tags": self.txt_tags.text(),
                "secret": self.lbl_secret.text(),
                "mini": self.lbl_mini.text(),
                "subir_activo": self.grp_yt.isChecked(),
                "ver_antes": self.chk_ver_antes.isChecked(),
            },
            "deteccion": {
                "intro": self.txt_det_intro.text(),
                "cierre": self.txt_det_cierre.text(),
                "capitulo": self.txt_det_capitulo.text(),
                "puente": self.txt_det_puente.text(),
                "modo": self.cbo_det_modo.currentIndex(),
                "confianza": self.slider_confianza.value(),
                "gap": self.spin_det_gap.value(),
                "max_caps": self.spin_det_max.value(),
            },
        }

    def _aplicar_datos_proyecto(self, datos: dict) -> None:
        """Restaura todo el estado de la aplicación a partir de un dict de proyecto."""
        self.audios = list(datos.get("audios") or [])
        self.imagenes = list(datos.get("imagenes") or [])
        self.marcas = list(datos.get("marcas") or [])
        self.img_manual = {int(k): v for k, v in (datos.get("img_manual") or {}).items()}

        self.combo_modo.setCurrentIndex(int(datos.get("modo") or 0))
        self.txt_script.setPlainText(str(datos.get("guion") or ""))
        self.spin_vel.setValue(int(datos.get("velocidad") or 0))
        self._voces_guardadas = str(datos.get("voz") or "")

        if "resolucion_idx" in datos:
            self.cbo_res.setCurrentIndex(int(datos["resolucion_idx"]))
        elif datos.get("resolucion"):
            idx_res = self.cbo_res.findText(str(datos["resolucion"]))
            if idx_res >= 0:
                self.cbo_res.setCurrentIndex(idx_res)
        if "transicion" in datos:
            self.spin_trans.setValue(float(datos["transicion"]))
        if "estilo_transicion" in datos:
            self.cbo_estilo.setCurrentIndex(int(datos["estilo_transicion"]))

        # Movimiento
        if datos.get("efecto"):
            self.efecto_global = ConfigEfecto.desde_dict(datos["efecto"])
        self.efectos_clip = {
            int(k): dict(v) for k, v in (datos.get("efectos_clip") or {}).items()
        }
        self._cargar_efecto_en_ui(self.efecto_global)

        # Subtítulos
        self.subtitulos = [
            {"inicio": float(c.get("inicio", 0.0)),
             "fin": float(c.get("fin", 0.0)),
             "texto": str(c.get("texto", ""))}
            for c in (datos.get("subtitulos") or [])
        ]
        if datos.get("estilo_subtitulos"):
            self.estilo_subtitulos = EstiloSubtitulos.desde_dict(datos["estilo_subtitulos"])
        self._aplicar_estilo_sub_ui(self.estilo_subtitulos)
        self.palabras_transcripcion = list(datos.get("palabras") or [])
        self._subs_auto = False
        if "quemar_subtitulos" in datos:
            self.chk_quemar_subtitulos.setChecked(bool(datos["quemar_subtitulos"]))
        if "mostrar_subtitulos" in datos:
            self.chk_sub_mostrar.setChecked(bool(datos["mostrar_subtitulos"]))
        if "sub_auto" in datos:
            self.chk_sub_auto.setChecked(bool(datos["sub_auto"]))
        self._refrescar_tabla_subtitulos()
        self._actualizar_subtitulos_monitor()

        # Inteligencia artificial
        self._ia_config = ConfigIA.desde_dict(datos.get("ia"))
        if hasattr(self, "panel_ia"):
            self.panel_ia.refrescar_desde_config()

        yt = datos.get("youtube") or {}
        if yt:
            self.txt_titulo.setText(str(yt.get("titulo") or ""))
            self.txt_desc.setPlainText(str(yt.get("descripcion") or ""))
            self.txt_tags.setText(str(yt.get("tags") or ""))
            self.lbl_secret.setText(str(yt.get("secret") or ""))
            self.lbl_mini.setText(str(yt.get("mini") or ""))
            self.grp_yt.setChecked(bool(yt.get("subir_activo", False)))
            self.chk_ver_antes.setChecked(bool(yt.get("ver_antes", False)))

        det = datos.get("deteccion") or {}
        defaults = ConfigDeteccion()
        self.txt_det_intro.setText(str(det.get("intro") or ", ".join(defaults.palabras_intro)))
        self.txt_det_cierre.setText(str(det.get("cierre") or ", ".join(defaults.palabras_cierre)))
        self.txt_det_capitulo.setText(str(det.get("capitulo") or ", ".join(defaults.palabras_capitulo)))
        self.txt_det_puente.setText(str(det.get("puente") or ", ".join(defaults.palabras_puente)))
        self.cbo_det_modo.setCurrentIndex(int(det.get("modo") or 0))
        self.slider_confianza.setValue(int(det.get("confianza") or 20))
        self.spin_det_gap.setValue(int(det.get("gap") or int(defaults.min_duracion_capitulo)))
        self.spin_det_max.setValue(int(det.get("max_caps") or defaults.max_capitulos))

        self.lista_audios.clear()
        for a in self.audios:
            self.lista_audios.addItem(Path(a).name)

        self.lista_imagenes.clear()
        for r in self.imagenes:
            if Path(r).exists():
                item = QListWidgetItem(QIcon(r), Path(r).name)
                item.setData(Qt.ItemDataRole.UserRole, r)
                item.setToolTip(r)
                self.lista_imagenes.addItem(item)

        self._aplicar_modo()
        if self.combo_modo.currentIndex() == 0:
            self._refrescar_tabla()
        else:
            self._refrescar_marcas()

        self._actualizar_timeline_y_monitor()
        self._proyecto_modificado = False
        self._actualizar_titulo_proyecto()
        self._guardar_sesion()

    # Alias históricos usados por las pruebas y por versiones anteriores
    def _empaquetar_proyecto(self) -> dict:
        return self._recopilar_datos_proyecto()

    def _desempaquetar_proyecto(self, datos: dict) -> None:
        self._aplicar_datos_proyecto(datos)

    def _guardar_proyecto(self) -> None:
        """Guarda directamente en el archivo actual si ya fue seleccionado; si no, pide ruta."""
        if self._ruta_proyecto is not None:
            try:
                guardar_proyecto(self._ruta_proyecto, self._recopilar_datos_proyecto())
                self._proyecto_modificado = False
                self._actualizar_titulo_proyecto()
                self.lbl_estado.setText(f"✓ Proyecto guardado con éxito: {self._ruta_proyecto.name}")
                self.log.appendPlainText(f"[Proyecto] Guardado en: {self._ruta_proyecto}")
            except Exception as exc:
                QMessageBox.critical(self, "Error al guardar", f"No se pudo guardar el proyecto:\n{exc}")
        else:
            self._guardar_proyecto_como()

    def _guardar_proyecto_como(self) -> None:
        """Abre explorador para seleccionar nueva ubicación y guarda el archivo .stcut."""
        sugerido = str(self._ruta_proyecto) if self._ruta_proyecto else "MiProyecto.stcut"
        ruta, _ = QFileDialog.getSaveFileName(
            self, "Guardar Proyecto Como", sugerido, FILTRO_PROYECTO
        )
        if not ruta:
            return
        p = Path(ruta)
        if not p.name.lower().endswith(EXTENSION_PROYECTO):
            p = p.with_suffix(EXTENSION_PROYECTO)
        self._ruta_proyecto = p
        self._guardar_proyecto()

    def _abrir_proyecto(self) -> None:
        """Abre un archivo de proyecto .stcut existente y lo fija como archivo activo."""
        if self._proyecto_modificado:
            resp = QMessageBox.question(
                self, "Cambios sin guardar",
                "El proyecto actual tiene cambios sin guardar. ¿Deseas guardarlo antes de abrir otro?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
            )
            if resp == QMessageBox.StandardButton.Save:
                self._guardar_proyecto()
            elif resp == QMessageBox.StandardButton.Cancel:
                return

        ruta, _ = QFileDialog.getOpenFileName(
            self, "Abrir Proyecto Studio Cut", "", FILTRO_PROYECTO
        )
        if not ruta:
            return
        try:
            datos = cargar_proyecto(ruta)
            self._ruta_proyecto = Path(ruta)
            self._aplicar_datos_proyecto(datos)
            self.lbl_estado.setText(f"✓ Proyecto abierto: {self._ruta_proyecto.name}")
            self.log.appendPlainText(f"[Proyecto] Cargado correctamente desde: {self._ruta_proyecto}")
        except Exception as exc:
            QMessageBox.critical(self, "Error al abrir proyecto", f"No se pudo abrir el archivo:\n{exc}")

    def _nuevo_proyecto(self) -> None:
        """Crea un proyecto nuevo en blanco."""
        if self._proyecto_modificado:
            resp = QMessageBox.question(
                self, "Cambios sin guardar",
                "El proyecto actual tiene cambios sin guardar. ¿Deseas guardarlo antes de crear uno nuevo?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
            )
            if resp == QMessageBox.StandardButton.Save:
                self._guardar_proyecto()
            elif resp == QMessageBox.StandardButton.Cancel:
                return

        self._ruta_proyecto = None
        self.audios = []
        self.imagenes = []
        self.marcas = []
        self.img_manual = {}
        self.efecto_global = ConfigEfecto()
        self.efectos_clip = {}
        self._clip_actual = 0
        self.subtitulos = []
        self.estilo_subtitulos = EstiloSubtitulos()
        self.palabras_transcripcion = []
        self._subs_auto = False
        self.lista_audios.clear()
        self.lista_imagenes.clear()
        self.txt_script.clear()
        self.txt_titulo.clear()
        self.txt_desc.clear()
        self.txt_tags.clear()
        self.lbl_mini.clear()
        self._cargar_efecto_en_ui(self.efecto_global)
        self._aplicar_estilo_sub_ui(self.estilo_subtitulos)
        self._refrescar_tabla_subtitulos()
        self._actualizar_subtitulos_monitor()
        self._proyecto_modificado = False
        self._aplicar_modo()
        self._actualizar_timeline_y_monitor()
        self._actualizar_titulo_proyecto()
        self.lbl_estado.setText("Nuevo proyecto en blanco creado.")

    def _actualizar_titulo_proyecto(self) -> None:
        """Actualiza el botón/etiqueta de proyecto en el header y el título de ventana."""
        nombre = self._ruta_proyecto.name if self._ruta_proyecto else "Sin título.stcut"
        mod_sym = " ●" if self._proyecto_modificado else ""
        estado_texto = "Sin guardar" if self._proyecto_modificado else "Guardado"
        color_texto = "#f59e0b" if self._proyecto_modificado else "#10b981"
        bg_badge = "#261f14" if self._proyecto_modificado else "#062e1e"
        borde_badge = "#78350f" if self._proyecto_modificado else "#059669"

        if hasattr(self, "btn_abrir_proyecto_rapido"):
            self.btn_abrir_proyecto_rapido.setText(f"📁 {nombre}")
            self.btn_abrir_proyecto_rapido.setToolTip(
                f"Proyecto: {self._ruta_proyecto or 'Sin guardar en disco'}\nHaz clic para abrir otro archivo .stcut"
            )
        if hasattr(self, "lbl_badge_guardado"):
            self.lbl_badge_guardado.setText(estado_texto)
            self.lbl_badge_guardado.setStyleSheet(
                f"color: {color_texto}; font-size: 10px; font-weight: bold; background: {bg_badge}; "
                f"border: 1px solid {borde_badge}; border-radius: 3px; padding: 1px 5px;"
            )
        self.setWindowTitle(f"Studio Cut · {nombre}{mod_sym}")

    def _cambio_resolucion(self) -> None:
        self.monitor.set_resolucion(self.cbo_res.currentText())

    # ------------------------------------------------------------- Detección
    def _construir_config_deteccion(self) -> ConfigDeteccion:
        """Lee los campos de la pestaña 'Detección' y construye un ConfigDeteccion."""
        def _palabras(txt: str) -> list[str]:
            return [p.strip() for p in txt.split(",") if p.strip()]

        return ConfigDeteccion(
            palabras_intro=_palabras(self.txt_det_intro.text()),
            palabras_cierre=_palabras(self.txt_det_cierre.text()),
            palabras_capitulo=_palabras(self.txt_det_capitulo.text()),
            modo_capitulos=self.cbo_det_modo.currentData() or "combinado",
            palabras_puente=_palabras(self.txt_det_puente.text()),
            confianza_minima=self.slider_confianza.value() / 100.0,
            min_duracion_capitulo=float(self.spin_det_gap.value()),
            max_capitulos=self.spin_det_max.value(),
        )

    def _reset_config_deteccion(self) -> None:
        """Restaura los valores por defecto de la configuración de detección."""
        defaults = ConfigDeteccion()
        self.txt_det_intro.setText(", ".join(defaults.palabras_intro))
        self.txt_det_cierre.setText(", ".join(defaults.palabras_cierre))
        self.txt_det_capitulo.setText(", ".join(defaults.palabras_capitulo))
        self.txt_det_puente.setText(", ".join(defaults.palabras_puente))
        self.cbo_det_modo.setCurrentIndex(0)  # combinado (primer elemento)
        self.spin_det_gap.setValue(int(defaults.min_duracion_capitulo))
        self.spin_det_max.setValue(defaults.max_capitulos)
        self.slider_confianza.setValue(20)

    def _actualizar_ejemplo_deteccion(self) -> None:
        """Muestra un ejemplo de qué detectaría la configuración actual."""
        modo = self.cbo_det_modo.currentData() or "flexible"
        caps = [p.strip() for p in self.txt_det_capitulo.text().split(",") if p.strip()]
        intros = [p.strip() for p in self.txt_det_intro.text().split(",") if p.strip()]
        cierres = [p.strip() for p in self.txt_det_cierre.text().split(",") if p.strip()]
        cap_ej = caps[0] if caps else "capítulo"
        intro_ej = intros[0] if intros else "intro"
        cierre_ej = cierres[0] if cierres else "cierre"

        if modo == "combinado":
            ejemplo = (
                f"🎬 '{intro_ej}' → Introducción\n"
                f"📖 '{cap_ej} 1' → Capítulo 1\n"
                f"📖 '{cap_ej} 2' → Capítulo 2\n"
                f"🏁 '{cierre_ej}' → Cierre"
            )
        elif modo == "solo_palabra":
            ejemplo = (
                f"🎬 '{intro_ej}' → Introducción\n"
                f"📖 '{cap_ej}' → Capítulo 1 (1ª vez)\n"
                f"📖 '{cap_ej}' → Capítulo 2 (2ª vez)\n"
                f"🏁 '{cierre_ej}' → Cierre"
            )
        elif modo == "solo_numero":
            ejemplo = (
                f"🎬 '{intro_ej}' → Introducción\n"
                f"🔢 'uno' o '1' → Capítulo 1\n"
                f"🔢 'dos' o '2' → Capítulo 2\n"
                f"🏁 '{cierre_ej}' → Cierre"
            )
        else:  # flexible
            ejemplo = (
                f"🎬 '{intro_ej}' → Introducción\n"
                f"📖 '{cap_ej} 1' o '{cap_ej}' → Capítulo 1\n"
                f"📖 '{cap_ej} número 2' → Capítulo 2\n"
                f"🏁 '{cierre_ej}' → Cierre"
            )
        self.lbl_det_ejemplo.setText(ejemplo)

    def _detectar(self) -> None:
        if not self.audios:
            QMessageBox.information(
                self, "Sin audio", "Primero elige el audio completo en la biblioteca de medios.")
            return
        self._t0_deteccion = time.monotonic()
        cfg = self._construir_config_deteccion()
        detector = DetectorCapitulos(self.audios[0], cfg=cfg)
        detector.progreso.connect(self._progreso_deteccion)
        detector.log.connect(self.log.appendPlainText)
        detector.listo.connect(self._deteccion_lista)
        detector.palabras.connect(self._palabras_detectadas)
        detector.fallo.connect(self._fallo)
        self._trabajador = detector
        self.btn_detectar.setEnabled(False)
        self.btn_detectar_top.setEnabled(False)
        self.btn_generar.setEnabled(False)
        self.btn_generar_top.setEnabled(False)
        self.btn_cancelar.setEnabled(True)
        self.barra.setValue(0)
        modo_txt = self.cbo_det_modo.currentText()
        self.lbl_estado.setText(f"Detectando capítulos — modo {modo_txt} (Vosk)…")
        detector.start()

    def _progreso_deteccion(self, pct: int, etapa: str) -> None:
        texto = {"descarga": "descargando modelo de voz",
                 "convertir": "convirtiendo audio",
                 "transcripcion": "transcribiendo con IA"}.get(etapa, etapa)
        self.barra.setValue(pct)
        if self._trabajador is not None:
            self.lbl_estado.setText(f"Detección de capítulos: {texto}… ({pct}%)")

    def _palabras_detectadas(self, palabras: list[dict]) -> None:
        """Guarda las palabras con tiempos y crea subtítulos si está activado."""
        self.palabras_transcripcion = list(palabras or [])
        if not self.palabras_transcripcion:
            return
        self.log.appendPlainText(
            f"[Voz] {len(self.palabras_transcripcion)} palabras transcritas con "
            "tiempos (disponibles para subtítulos)."
        )
        # Crear los subtítulos en cuanto llegan las palabras: el usuario los ve
        # en el monitor sin tener que pulsar nada más.
        if self.chk_sub_auto.isChecked() and (not self.subtitulos or self._subs_auto):
            self._generar_subtitulos_voz(silencioso=True, automatico=True)
            self.log.appendPlainText(
                "[Subtítulos] Generados automáticamente desde la voz.")

    def _deteccion_lista(self, marcas: list[dict]) -> None:
        self._trabajador = None
        self.btn_detectar.setEnabled(True)
        self.btn_detectar_top.setEnabled(True)
        self.btn_generar.setEnabled(True)
        self.btn_generar_top.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        transcurrido = time.monotonic() - self._t0_deteccion
        self.barra.setValue(100)

        # Si Vosk no reconoce todos los encabezados, usar los tiempos del guion.
        if self._ia_marcas_pendientes:
            pendientes = list(self._ia_marcas_pendientes)
            self._ia_marcas_pendientes = []
            if not marcas or len(marcas) < max(2, len(pendientes) - 1):
                marcas = pendientes
                self.log.appendPlainText(
                    "[IA] Vosk no reconoció todos los capítulos; se usan los "
                    "tiempos estimados a partir del guion."
                )

        if marcas:
            self.marcas = marcas
            self.img_manual.clear()

            # Auto-asignar imágenes por nombre: intro, capítulo N, cierre
            if self.imagenes:
                auto_img = ordering.emparejar_imagenes_por_capitulo(marcas, self.imagenes)
                self.img_manual.update(auto_img)
                self.log.appendPlainText(
                    f"[Auto] Imágenes asignadas automáticamente a {len(auto_img)} capítulos por nombre y posición")

            # Subtítulos automáticos: primero la voz; si no hay, los capítulos.
            if self.chk_sub_auto.isChecked() and (not self.subtitulos or self._subs_auto):
                if self.palabras_transcripcion:
                    self._generar_subtitulos_voz(silencioso=True, automatico=True)
                else:
                    self._generar_subtitulos_capitulos(automatico=True)
            else:
                if self.subtitulos:
                    self.log.appendPlainText(
                        "[Subtítulos] Se conservan los subtítulos existentes.")
                self._actualizar_subtitulos_monitor()

            self._refrescar_marcas()
            self.lbl_estado.setText(
                f"{len(marcas)} capítulos detectados en {transcurrido:.0f} s — imágenes asignadas automáticamente.")
            self._proyecto_modificado = True
            self._actualizar_titulo_proyecto()
            self._guardar_sesion()
        else:
            self.lbl_estado.setText("No se detectaron capítulos; añade marcas con '+ Marca'.")
            self._refrescar_marcas()

    # ---------------------------------------------------------------- Render
    def _probar_estilo(self) -> None:
        dir_videos = Path.home() / "Videos"
        dir_videos.mkdir(exist_ok=True)
        self._lanzar(str(dir_videos / "prueba_estilo.mp4"), rapida=True)

    def _generar(self) -> None:
        if not self.audios:
            QMessageBox.information(self, "Sin audios", "Añade audios e imágenes primero.")
            return
        salida = self._salida_actual()
        if salida:
            self._lanzar(salida, rapida=False)

    def _segmentos_actuales(self) -> tuple[list[str], list[str], list[float]]:
        if self.combo_modo.currentIndex() != 0:
            imagenes = [
                self.tabla_marcas.cellWidget(f, 2).currentData() or ""
                for f in range(self.tabla_marcas.rowCount())
            ]
            marcas = [float(self.tabla_marcas.cellWidget(f, 1).value())
                      for f in range(self.tabla_marcas.rowCount())]
            return list(self.audios[:1]), imagenes, marcas
        imagenes = [
            self.tabla.cellWidget(f, 1).currentData() or ""
            for f in range(self.tabla.rowCount())
        ]
        return list(self.audios), imagenes, []

    def _lanzar(self, salida: str, rapida: bool) -> None:
        if salida and not salida.lower().endswith(".mp4"):
            salida += ".mp4"
        modo_audio_unico = self.combo_modo.currentIndex() != 0
        audios, imagenes, marcas = self._segmentos_actuales()
        if not audios:
            return
        if modo_audio_unico and not marcas:
            QMessageBox.information(
                self, "Sin marcas",
                "Detecta los capítulos con '🎙 Detectar' o añádelos con '+ Marca'.")
            return

        cfg = ConfigRender(
            ancho=self.cbo_res.currentData()[0],
            alto=self.cbo_res.currentData()[1],
            transicion_seg=self.spin_trans.value(),
            ken_burns=self.efecto_global.intensidad,
            estilo_transicion=("fade" if self.cbo_estilo.currentIndex() == 0 else "variado"),
            efecto=self.efecto_global,
            efectos_clip={
                int(k): dict(v) for k, v in self.efectos_clip.items()
            },
            subtitulos=[dict(c) for c in self.subtitulos],
            estilo_subtitulos=self.estilo_subtitulos,
            quemar_subtitulos=self.chk_quemar_subtitulos.isChecked(),
        )

        trabajador = Trabajador(salida, cfg)
        trabajador.preparar(audios, imagenes)
        if modo_audio_unico:
            trabajador.modo_un_audio(marcas)
        if rapida:
            trabajador.como_previsualizacion()

        self._subida_pendiente = False
        self._abrir_al_terminar = False
        titulo = self.txt_titulo.text().strip()

        if self.grp_yt.isChecked() and not rapida:
            if not self.lbl_secret.text():
                QMessageBox.warning(
                    self, "Falta credencial",
                    "Para subir a YouTube selecciona client_secret.json.")
                return
            if not titulo:
                self._subida_pendiente = True
                self.log.appendPlainText(
                    "Falta título: el video se generará y quedará pendiente.")
            else:
                if self.chk_ver_antes.isChecked():
                    self._subida_pendiente = True
                    self._abrir_al_terminar = True
                else:
                    trabajador.subir_a_youtube({
                        "titulo": titulo,
                        "descripcion": self.txt_desc.toPlainText().strip(),
                        "etiquetas": [t.strip() for t in self.txt_tags.text().split(",") if t.strip()],
                        "client_secret": self.lbl_secret.text(),
                        "token_path": str(RUTA_TOKEN),
                        "miniatura": self.lbl_mini.text() or None,
                    })

        trabajador.progreso.connect(self.barra.setValue)
        trabajador.log.connect(self.log.appendPlainText)
        trabajador.listo.connect(self._listo)
        trabajador.fallo.connect(self._fallo)
        self._trabajador = trabajador

        self.btn_generar.setEnabled(False)
        self.btn_generar_top.setEnabled(False)
        self.btn_prev.setEnabled(False)
        self.btn_cancelar.setEnabled(True)
        self.barra.setValue(0)
        self.lbl_estado.setText("Renderizando video con efectos y subtítulos…")
        trabajador.start()

    def _cancelar(self) -> None:
        if self._trabajador:
            self._trabajador.cancelar()
            self.lbl_estado.setText("Cancelando operación…")

    def _listo(self, resultado: str) -> None:
        self._trabajador = None
        self.btn_generar.setEnabled(True)
        self.btn_generar_top.setEnabled(True)
        self.btn_prev.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.btn_detectar.setEnabled(True)
        self.btn_detectar_top.setEnabled(True)
        self.barra.setValue(100)
        self._ultimo_resultado = resultado
        es_url = bool(resultado) and resultado.startswith("http")

        self.btn_abrir.setEnabled(not es_url)
        self.btn_ver.setEnabled(not es_url and Path(resultado).exists())
        self.monitor.btn_ver.setEnabled(not es_url and Path(resultado).exists())

        if es_url:
            self.lbl_estado.setText(f"¡Subido a YouTube con éxito! {resultado}")
            self.log.appendPlainText(f"URL: {resultado}")
            self._desregistrar_pendiente(resultado)
        elif self._subida_pendiente and Path(resultado).exists():
            self._registrar_pendiente(resultado)
            if self._abrir_al_terminar:
                self._abrir_video(resultado)
            self.lbl_estado.setText(f"Video listo y PENDIENTE de subir: {Path(resultado).name}")
        else:
            self.lbl_estado.setText(f"¡Video generado con éxito! {Path(resultado).name}")

        self._capturar_frame_video()
        self._subida_pendiente = False
        self._guardar_sesion()

    def _fallo(self, mensaje: str) -> None:
        self._trabajador = None
        self.btn_generar.setEnabled(True)
        self.btn_generar_top.setEnabled(True)
        self.btn_prev.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.btn_detectar.setEnabled(True)
        self.btn_detectar_top.setEnabled(True)
        if "cancelado" in mensaje.lower():
            self.lbl_estado.setText("Cancelado.")
            self.log.appendPlainText("Cancelado por el usuario.")
        else:
            self.lbl_estado.setText("Error en el proceso. Revisa la consola.")
            self.log.appendPlainText(f"ERROR: {mensaje}")

    # ------------------------------------------------------ Captura de Frame
    def _capturar_frame_video(self) -> None:
        if not self._ultimo_resultado or not Path(self._ultimo_resultado).exists():
            return
        try:
            tmpfile = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmpfile.close()
            ruta_ff = self._get_ruta_ffmpeg()
            if not ruta_ff:
                return
            cmd = [
                ruta_ff, "-y", "-hide_banner", "-nostdin", "-loglevel", "error",
                "-i", self._ultimo_resultado,
                "-ss", "00:00:01.000",
                "-frames:v", "1",
                "-f", "image2",
                tmpfile.name
            ]
            subprocess.run(cmd, check=True, capture_output=True)
            pix = QPixmap(tmpfile.name)
            if not pix.isNull():
                self.monitor.set_frame(pix)
            try:
                os.unlink(tmpfile.name)
            except Exception:
                pass
        except Exception as e:
            self.log.appendPlainText(f"Error capturando fotograma: {e}")

    def _get_ruta_ffmpeg(self) -> str | None:
        from core.media import ruta_ffmpeg
        return ruta_ffmpeg()

    def _salida_actual(self) -> str:
        titulo = self.txt_titulo.text().strip() or "video_capitulos"
        nombre = "".join(c if (c.isalnum() or c in " -_") else " " for c in titulo).strip()
        nombre = nombre or "video"
        dir_videos = Path.home() / "Videos"
        dir_videos.mkdir(exist_ok=True)
        salida, _ = QFileDialog.getSaveFileName(
            self, "Guardar video exportado como…", str(dir_videos / f"{nombre}.mp4"),
            "Video MP4 (*.mp4)")
        return salida

    def _estimar_duracion(self) -> None:
        if not self.audios:
            QMessageBox.information(self, "Sin audios", "Añade audios primero.")
            return
        try:
            if self.combo_modo.currentIndex() != 0:
                total = duracion_audio(self.audios[0])
                self.lbl_total.setText(
                    f"Duración: {total / 60:.1f} min · {len(self.marcas)} cambios de imagen")
                return
            total = sum(duracion_audio(a) for a in self.audios)
            n = len(self.audios)
            final = total - self.spin_trans.value() * max(n - 1, 0)
            self.lbl_total.setText(
                f"Duración final: {max(final, 0) / 60:.1f} min ({n} segmentos)")
        except ErrorMedia as exc:
            QMessageBox.warning(self, "Error", str(exc))

    def _abrir_carpeta(self) -> None:
        if self._ultimo_resultado:
            carpeta = Path(self._ultimo_resultado).parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(carpeta)))

    def _abrir_video(self, ruta: str) -> None:
        if ruta and Path(ruta).exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))

    def _ver_video(self) -> None:
        if self._ultimo_resultado and Path(self._ultimo_resultado).exists():
            self._abrir_video(self._ultimo_resultado)

    # ------------------------------------------------------------- YouTube
    def _buscar_secret(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Elegir client_secret.json", "", "JSON (*.json)")
        if ruta:
            self.lbl_secret.setText(ruta)

    def _buscar_miniatura(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Elegir miniatura", "", f"Imágenes ({IMAGEN_EXT})")
        if ruta:
            self.lbl_mini.setText(ruta)

    def _pendientes(self) -> list[str]:
        try:
            lista = json.loads(str(self._ajustes.value("pendientes") or "[]"))
            return [p for p in lista if Path(p).exists()]
        except Exception:
            return []

    def _registrar_pendiente(self, ruta: str) -> None:
        pendientes = self._pendientes()
        if ruta not in pendientes:
            pendientes.append(ruta)
        self._ajustes.setValue("pendientes", json.dumps(pendientes))
        self.log.appendPlainText(f"Registrado como pendiente: {ruta}")

    def _desregistrar_pendiente(self, url: str) -> None:
        pendientes = [p for p in self._pendientes() if p != self._pendiente_ruta]
        self._ajustes.setValue("pendientes", json.dumps(pendientes))

    def _subir_pendiente(self) -> None:
        pendientes = self._pendientes()
        inicial = pendientes[0] if pendientes else str(Path.home() / "Videos")
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Elegir video pendiente", inicial, "Video MP4 (*.mp4)")
        if not ruta:
            return
        titulo = self.txt_titulo.text().strip()
        if not titulo:
            QMessageBox.warning(
                self, "Falta título",
                "Escribe el título en el panel de YouTube antes de subir.")
            return
        if not self.lbl_secret.text():
            QMessageBox.warning(
                self, "Falta credencial",
                "Selecciona client_secret.json en el panel de YouTube.")
            return

        trabajador = Trabajador("", ConfigRender())
        trabajador.solo_subir(ruta, {
            "titulo": titulo,
            "descripcion": self.txt_desc.toPlainText().strip(),
            "etiquetas": [t.strip() for t in self.txt_tags.text().split(",") if t.strip()],
            "client_secret": self.lbl_secret.text(),
            "token_path": str(RUTA_TOKEN),
            "miniatura": self.lbl_mini.text() or None,
        })
        trabajador.log.connect(self.log.appendPlainText)
        trabajador.listo.connect(self._pendiente_sube)
        trabajador.fallo.connect(self._fallo)
        self._trabajador = trabajador
        self._pendiente_ruta = ruta
        self.btn_generar.setEnabled(False)
        self.btn_generar_top.setEnabled(False)
        self.btn_cancelar.setEnabled(True)
        self.barra.setRange(0, 0)
        self.lbl_estado.setText(f"Subiendo a YouTube: {Path(ruta).name}…")
        trabajador.start()

    def _pendiente_sube(self, url: str) -> None:
        self._trabajador = None
        self.btn_generar.setEnabled(True)
        self.btn_generar_top.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.barra.setRange(0, 100)
        self.barra.setValue(100)
        self.lbl_estado.setText(f"¡Subido a YouTube con éxito! {url}")
        self.log.appendPlainText(f"URL: {url}")
        self._desregistrar_pendiente(url)

    # ------------------------------------------------------------- TTS
    def _popular_voces(self) -> None:
        if self.combo_voz.currentData() or self.combo_voz.count():
            return
        from core import voz_tts
        voces = voz_tts.listar_voces()
        if not voces:
            self.combo_voz.addItem("(voz por defecto del sistema)", "")
        for v in voces:
            self.combo_voz.addItem(f"{v['nombre']}  ·  {v['cultura']}", v["nombre"])
        guardada = getattr(self, "_voces_guardadas", "")
        idx = self.combo_voz.findData(guardada) if guardada else -1
        if idx < 0:
            for i in range(self.combo_voz.count()):
                if "jorge" in (self.combo_voz.itemText(i) or "").lower():
                    idx = i
                    break
        self.combo_voz.setCurrentIndex(max(idx, 0))

    def _generar_voz(self) -> None:
        texto = self.txt_script.toPlainText()
        if not texto.strip():
            QMessageBox.information(self, "Sin texto", "Escribe el guion que leerá la voz.")
            return
        dir_audios = CARPETA_CFG / "audios_generados"
        dir_audios.mkdir(parents=True, exist_ok=True)
        ruta_wav = str(dir_audios / f"voz_{time.strftime('%Y%m%d_%H%M%S')}.wav")

        trabajador = TrabajadorVoz(
            texto, ruta_wav, self.combo_voz.currentData(), self.spin_vel.value())
        trabajador.log.connect(self.log.appendPlainText)
        trabajador.listo.connect(self._voz_lista)
        trabajador.fallo.connect(self._voz_fallo)
        self._trabajador = trabajador
        self.btn_voz.setEnabled(False)
        self.btn_generar.setEnabled(False)
        self.btn_generar_top.setEnabled(False)
        self.btn_cancelar.setEnabled(True)
        self.barra.setRange(0, 0)
        self.lbl_estado.setText("Generando voz artificial (TTS)…")
        trabajador.start()

    def _voz_lista(self, ruta_wav: str) -> None:
        self._trabajador = None
        self.btn_voz.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.barra.setRange(0, 100)
        self.audios = [ruta_wav]
        self.lista_audios.clear()
        self.lista_audios.addItem(f"Voz generada · {Path(ruta_wav).name}")
        self.lbl_estado.setText("Audio generado con éxito. Detectando capítulos…")
        self._guardar_sesion()
        self._actualizar_timeline_y_monitor()
        self._detectar()

    def _voz_fallo(self, mensaje: str) -> None:
        self._trabajador = None
        self.btn_voz.setEnabled(True)
        self.btn_generar.setEnabled(True)
        self.btn_generar_top.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.barra.setRange(0, 100)
        self.lbl_estado.setText("Error generando la voz. Revisa la consola.")
        self.log.appendPlainText(f"ERROR: {mensaje}")

    # -------------------------------------------------------------- IA
    def _ia_aplicar_guion(self, texto: str) -> None:
        """Coloca el guion generado en el panel de Texto → voz."""
        self.txt_script.setPlainText(texto)
        if self.combo_modo.currentIndex() != 2:
            self.combo_modo.setCurrentIndex(2)
        else:
            self._aplicar_modo()
        self._proyecto_modificado = True
        self._actualizar_titulo_proyecto()
        self.lbl_estado.setText("Guion de IA insertado. Generando la voz…")
        self._guardar_sesion()

    def _ia_aplicar_imagenes(self, rutas: list[str]) -> None:
        """Sustituye las imágenes generadas por IA y refresca la línea de tiempo."""
        carpeta_ia = (CARPETA_CFG / "imagenes_generadas").resolve()

        def es_ia(ruta: str) -> bool:
            try:
                return Path(ruta).resolve().parent == carpeta_ia
            except OSError:
                return False

        self.imagenes = [r for r in self.imagenes if not es_ia(r)]
        self.img_manual = {
            k: v for k, v in self.img_manual.items() if not es_ia(v)
        }
        for ruta in rutas:
            ruta = str(ruta)
            if Path(ruta).exists() and ruta not in self.imagenes:
                self.imagenes.append(ruta)

        self.lista_imagenes.clear()
        for ruta in self.imagenes:
            if Path(ruta).exists():
                item = QListWidgetItem(QIcon(ruta), Path(ruta).name)
                item.setData(Qt.ItemDataRole.UserRole, ruta)
                item.setToolTip(ruta)
                self.lista_imagenes.addItem(item)

        self._proyecto_modificado = True
        self._actualizar_titulo_proyecto()
        self._actualizar_timeline_y_monitor()
        self._guardar_sesion()

    def _ia_aplicar_voz(self, ruta_wav: str, guion=None) -> None:
        """Usa el audio de IA y deja preparados los tiempos estimados del guion."""
        self._ia_marcas_pendientes = []
        if guion is not None:
            try:
                self._ia_marcas_pendientes = marcas_estimadas(
                    guion, duracion_audio(ruta_wav)
                )
            except Exception:  # noqa: BLE001
                self._ia_marcas_pendientes = []
        self._voz_lista(ruta_wav)

    # ------------------------------------------------------------- Sesión
    def _guardar_sesion(self) -> None:
        self._ajustes.setValue("audios", self.audios)
        self._ajustes.setValue("imagenes", self.imagenes)
        self._ajustes.setValue("marcas", self.marcas)
        self._ajustes.setValue("modo", self.combo_modo.currentIndex())
        self._ajustes.setValue("pendientes", json.dumps(self._pendientes()))
        self._ajustes.setValue("guion", self.txt_script.toPlainText())
        self._ajustes.setValue("voz", self.combo_voz.currentData() or "")
        self._ajustes.setValue("velocidad", self.spin_vel.value())
        self._ajustes.setValue("subtitulos", json.dumps(self.subtitulos, ensure_ascii=False))
        self._ajustes.setValue("estilo_subtitulos", json.dumps(self.estilo_subtitulos.a_dict()))
        self._ajustes.setValue("palabras", json.dumps(self.palabras_transcripcion, ensure_ascii=False))
        self._ajustes.setValue("quemar_subtitulos", self.chk_quemar_subtitulos.isChecked())
        self._ajustes.setValue("sub_auto", self.chk_sub_auto.isChecked())
        self._ajustes.setValue("efecto", json.dumps(self.efecto_global.a_dict()))
        self._ajustes.setValue(
            "efectos_clip", json.dumps({str(k): v for k, v in self.efectos_clip.items()}))
        # Configuración de detección
        self._ajustes.setValue("det_intro", self.txt_det_intro.text())
        self._ajustes.setValue("det_cierre", self.txt_det_cierre.text())
        self._ajustes.setValue("det_capitulo", self.txt_det_capitulo.text())
        self._ajustes.setValue("det_puente", self.txt_det_puente.text())
        self._ajustes.setValue("det_modo", self.cbo_det_modo.currentIndex())
        self._ajustes.setValue("det_confianza", self.slider_confianza.value())
        self._ajustes.setValue("det_gap", self.spin_det_gap.value())
        self._ajustes.setValue("det_max", self.spin_det_max.value())
        self._ajustes.setValue("img_manual", json.dumps({str(k): v for k, v in self.img_manual.items()}))
        self._ajustes.setValue("ruta_proyecto", str(self._ruta_proyecto) if self._ruta_proyecto else "")
        # Configuración de IA (sin claves)
        self._ajustes.setValue(
            "ia_config", json.dumps(self._ia_config.a_dict(), ensure_ascii=False)
        )

    def _cargar_sesion(self) -> None:
        self.audios = list(self._ajustes.value("audios") or [])
        self.imagenes = list(self._ajustes.value("imagenes") or [])
        self.marcas = list(self._ajustes.value("marcas") or [])
        try:
            self._ia_config = ConfigIA.desde_dict(
                json.loads(str(self._ajustes.value("ia_config") or "{}"))
            )
        except Exception:  # noqa: BLE001
            self._ia_config = ConfigIA()
        raw_manual = self._ajustes.value("img_manual")
        if raw_manual:
            try:
                self.img_manual = {int(k): v for k, v in json.loads(str(raw_manual)).items()}
            except Exception:
                self.img_manual = {}
        rp = str(self._ajustes.value("ruta_proyecto") or "")
        if rp and Path(rp).exists():
            self._ruta_proyecto = Path(rp)

        self.txt_script.setPlainText(str(self._ajustes.value("guion") or ""))
        self.spin_vel.setValue(int(self._ajustes.value("velocidad") or 0))

        # Movimiento y subtítulos de la sesión anterior
        try:
            self.efecto_global = ConfigEfecto.desde_dict(
                json.loads(str(self._ajustes.value("efecto") or "{}")))
        except Exception:
            self.efecto_global = ConfigEfecto()
        try:
            crudo = json.loads(str(self._ajustes.value("efectos_clip") or "{}"))
            self.efectos_clip = {int(k): dict(v) for k, v in crudo.items()}
        except Exception:
            self.efectos_clip = {}
        try:
            self.subtitulos = list(
                json.loads(str(self._ajustes.value("subtitulos") or "[]")))
        except Exception:
            self.subtitulos = []
        try:
            self.estilo_subtitulos = EstiloSubtitulos.desde_dict(
                json.loads(str(self._ajustes.value("estilo_subtitulos") or "{}")))
        except Exception:
            self.estilo_subtitulos = EstiloSubtitulos()
        try:
            self.palabras_transcripcion = list(
                json.loads(str(self._ajustes.value("palabras") or "[]")))
        except Exception:
            self.palabras_transcripcion = []
        quemar = self._ajustes.value("quemar_subtitulos")
        if quemar is not None:
            self.chk_quemar_subtitulos.setChecked(str(quemar).lower() in ("true", "1"))
        sub_auto = self._ajustes.value("sub_auto")
        if sub_auto is not None:
            self.chk_sub_auto.setChecked(str(sub_auto).lower() in ("true", "1"))
        self._cargar_efecto_en_ui(self.efecto_global)
        self._aplicar_estilo_sub_ui(self.estilo_subtitulos)
        # Si había voz transcrita pero no subtítulos, recrearlos al abrir.
        if (self.chk_sub_auto.isChecked() and self.palabras_transcripcion
                and not self.subtitulos):
            self.subtitulos = cues_desde_palabras(
                self.palabras_transcripcion,
                max_caracteres=self.estilo_subtitulos.max_caracteres,
            )
            self._subs_auto = True
        else:
            self._subs_auto = False
        self._refrescar_tabla_subtitulos()
        self._voces_guardadas = str(self._ajustes.value("voz") or "")
        modo = int(self._ajustes.value("modo") or 0)
        self.combo_modo.setCurrentIndex(modo)
        self._aplicar_modo()
        # Restaurar configuración de detección
        defaults = ConfigDeteccion()
        self.txt_det_intro.setText(
            str(self._ajustes.value("det_intro") or ", ".join(defaults.palabras_intro))
        )
        self.txt_det_cierre.setText(
            str(self._ajustes.value("det_cierre") or ", ".join(defaults.palabras_cierre))
        )
        self.txt_det_capitulo.setText(
            str(self._ajustes.value("det_capitulo") or ", ".join(defaults.palabras_capitulo))
        )
        self.txt_det_puente.setText(
            str(self._ajustes.value("det_puente") or ", ".join(defaults.palabras_puente))
        )
        det_modo_idx = int(self._ajustes.value("det_modo") or 0)
        self.cbo_det_modo.setCurrentIndex(det_modo_idx)
        self.slider_confianza.setValue(int(self._ajustes.value("det_confianza") or 20))
        self.spin_det_gap.setValue(int(self._ajustes.value("det_gap") or int(defaults.min_duracion_capitulo)))
        self.spin_det_max.setValue(int(self._ajustes.value("det_max") or defaults.max_capitulos))

        for r in self.imagenes:
            if Path(r).exists():
                item = QListWidgetItem(QIcon(r), Path(r).name)
                item.setData(Qt.ItemDataRole.UserRole, r)
                item.setToolTip(r)
                self.lista_imagenes.addItem(item)
        self._refrescar_marcas()
        if modo == 0:
            self._reordenar()
        if hasattr(self, "panel_ia"):
            self.panel_ia.refrescar_desde_config()
        self._actualizar_titulo_proyecto()
        self._actualizar_timeline_y_monitor()
        self._guardar_sesion()

    def closeEvent(self, evento) -> None:  # noqa: N802
        if self._proyecto_modificado:
            resp = QMessageBox.question(
                self, "Salir de Studio Cut",
                "El proyecto actual tiene cambios sin guardar. ¿Deseas guardarlo antes de salir?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
            )
            if resp == QMessageBox.StandardButton.Save:
                self._guardar_proyecto()
            elif resp == QMessageBox.StandardButton.Cancel:
                evento.ignore()
                return

        self._guardar_sesion()
        super().closeEvent(evento)
