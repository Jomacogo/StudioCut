"""Pestaña «🤖 IA»: guion, imágenes y voz casi automáticos.

Todo el trabajo pesado (red, TTS, imágenes) ocurre en hilos para que la
interfaz siga respondiendo. Las claves se guardan cifradas con DPAPI y nunca
se escriben en el proyecto ni en los ajustes.
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QFrame,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from core import plugins
from core.ia import credenciales
from core.ia.imagenes import ESTILOS
from core.ia.pipeline import ConfigIA
from core.ia.proveedores import (
    BASE_OPENAI,
    BASE_OPENROUTER,
    MODELO_IMAGEN_DEFECTO,
    MODELO_TEXTO_DEFECTO,
    listar_modelos_gratis,
    verificar_clave,
)
from core.ia.voz import listar_voces_elevenlabs
from ui.trabajador import TrabajadorIA

_NOMBRES_ESTILO = {
    "cinematografico": "Cinematográfico (realista)",
    "ilustracion": "Ilustración editorial",
    "3d": "Animación 3D",
    "minimalista": "Minimalista",
    "acuarela": "Acuarela",
    "futurista": "Futurista / neón",
}

_TONOS = [
    "cercano y directo", "educativo y claro", "divertido y dinámico",
    "serio y profesional", "inspirador y motivador",
]
_PUBLICOS = [
    "público general", "principiantes", "jóvenes", "profesionales", "familias",
]


class _Tarea(QThread):
    """Ejecuta una función corta fuera del hilo de la interfaz."""

    listo = pyqtSignal(object)
    fallo = pyqtSignal(str)

    def __init__(self, funcion, parent=None):
        super().__init__(parent)
        self._funcion = funcion

    def run(self) -> None:  # pragma: no cover - hilo
        try:
            self.listo.emit(self._funcion())
        except Exception as exc:  # noqa: BLE001
            self.fallo.emit(str(exc))


class PanelIA(QWidget):
    def __init__(self, ventana, parent=None) -> None:
        super().__init__(parent)
        self._ventana = ventana
        self._cargando = False
        self._tareas: list[_Tarea] = []
        self._trabajador_ia: TrabajadorIA | None = None
        self._ultimo_guion = None
        self._manifest = None

        self._construir()
        self.refrescar_desde_config()
        self._actualizar_estado_claves()
        self._actualizar_estado_eleven()
        self._actualizar_estado_openai()
        self._actualizar_estado_mcp()

    # ------------------------------------------------------------- Construcción
    def _construir(self) -> None:
        raiz = QVBoxLayout(self)
        raiz.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        raiz.addWidget(scroll)

        inner = QWidget()
        inner.setObjectName("PanelIAInner")
        inner.setStyleSheet("QWidget#PanelIAInner { background: transparent; }")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(10)
        scroll.setWidget(inner)

        titulo = QLabel("INTELIGENCIA ARTIFICIAL")
        titulo.setStyleSheet(
            "font-size: 10px; font-weight: bold; color: #38bdf8; letter-spacing: 1px;"
        )
        lay.addWidget(titulo)

        hint = QLabel(
            "Escribe una idea y pulsa «⚡ Generar todo»: la app crea el guion, "
            "las imágenes de cada capítulo y la voz. Después detecta los "
            "capítulos y los subtítulos automáticamente. Usa tu propia clave de "
            "OpenRouter; se guarda cifrada en este equipo."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(hint)

        lay.addWidget(self._grupo_texto())
        lay.addWidget(self._grupo_guion())
        lay.addWidget(self._grupo_imagenes())
        lay.addWidget(self._grupo_voz())
        lay.addWidget(self._grupo_acciones())
        lay.addWidget(self._grupo_mcp())
        lay.addStretch()

    def _grupo_texto(self) -> QGroupBox:
        grupo = QGroupBox("Proveedor de texto")
        lay = QVBoxLayout(grupo)
        lay.setSpacing(6)

        form = QFormLayout()
        form.setSpacing(6)

        self.cbo_prov_texto = QComboBox()
        self.cbo_prov_texto.addItem("OpenRouter (recomendado)", "openrouter")
        self.cbo_prov_texto.addItem("OpenAI compatible · Groq · DeepSeek", "compatible")
        self.cbo_prov_texto.addItem("Ollama local (sin clave)", "ollama")
        self.cbo_prov_texto.currentIndexChanged.connect(self._cambio_proveedor_texto)
        form.addRow("Proveedor:", self.cbo_prov_texto)

        self.txt_base_url = QLineEdit()
        self.txt_base_url.setPlaceholderText("https://openrouter.ai/api/v1")
        self.txt_base_url.editingFinished.connect(self._guardar_config)
        form.addRow("URL base:", self.txt_base_url)

        self.cbo_modelo = QComboBox()
        self.cbo_modelo.setEditable(True)
        self.cbo_modelo.addItem(MODELO_TEXTO_DEFECTO, MODELO_TEXTO_DEFECTO)
        self.cbo_modelo.setToolTip(
            "Modelo de texto. Nemotron 3 Ultra (free) es el predeterminado."
        )
        self.cbo_modelo.currentTextChanged.connect(self._guardar_config)
        form.addRow("Modelo:", self.cbo_modelo)

        lay.addLayout(form)

        fila = QHBoxLayout()
        self.txt_clave_texto = QLineEdit()
        self.txt_clave_texto.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_clave_texto.setPlaceholderText("sk-or-v1-…")
        fila.addWidget(self.txt_clave_texto, 1)

        self.btn_guardar_clave = QPushButton("Guardar")
        self.btn_guardar_clave.clicked.connect(self._guardar_clave_texto)
        fila.addWidget(self.btn_guardar_clave)

        self.btn_probar_clave = QPushButton("Probar")
        self.btn_probar_clave.clicked.connect(self._probar_clave_texto)
        fila.addWidget(self.btn_probar_clave)

        self.btn_borrar_clave = QPushButton("Borrar")
        self.btn_borrar_clave.clicked.connect(self._borrar_clave_texto)
        fila.addWidget(self.btn_borrar_clave)
        lay.addLayout(fila)

        self.lbl_clave_texto = QLabel("")
        self.lbl_clave_texto.setWordWrap(True)
        self.lbl_clave_texto.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(self.lbl_clave_texto)

        fila2 = QHBoxLayout()
        self.btn_modelos = QPushButton("🔄 Ver modelos gratis")
        self.btn_modelos.clicked.connect(self._listar_modelos)
        fila2.addWidget(self.btn_modelos)
        self.lbl_modelo_info = QLabel("")
        self.lbl_modelo_info.setStyleSheet("color: #64748b; font-size: 10px;")
        fila2.addWidget(self.lbl_modelo_info, 1)
        lay.addLayout(fila2)
        return grupo

    def _grupo_guion(self) -> QGroupBox:
        grupo = QGroupBox("Guion")
        lay = QVBoxLayout(grupo)
        lay.setSpacing(6)

        self.txt_idea = QPlainTextEdit()
        self.txt_idea.setPlaceholderText(
            "Ej.: Cómo ahorrar dinero sin pasar hambre ni dejar de vivir. "
            "Consejos prácticos para principiantes."
        )
        self.txt_idea.setFixedHeight(64)
        lay.addWidget(self.txt_idea)

        form = QFormLayout()
        form.setSpacing(6)
        self.spin_duracion = QSpinBox()
        self.spin_duracion.setRange(1, 120)
        self.spin_duracion.setValue(5)
        self.spin_duracion.setSuffix(" min")
        self.spin_duracion.valueChanged.connect(self._guardar_config)
        form.addRow("Duración:", self.spin_duracion)

        self.spin_capitulos = QSpinBox()
        self.spin_capitulos.setRange(1, 20)
        self.spin_capitulos.setValue(3)
        self.spin_capitulos.valueChanged.connect(self._guardar_config)
        form.addRow("Capítulos:", self.spin_capitulos)

        self.cbo_tono = QComboBox()
        self.cbo_tono.setEditable(True)
        self.cbo_tono.addItems(_TONOS)
        self.cbo_tono.currentTextChanged.connect(self._guardar_config)
        form.addRow("Tono:", self.cbo_tono)

        self.cbo_publico = QComboBox()
        self.cbo_publico.setEditable(True)
        self.cbo_publico.addItems(_PUBLICOS)
        self.cbo_publico.currentTextChanged.connect(self._guardar_config)
        form.addRow("Público:", self.cbo_publico)
        lay.addLayout(form)

        self.chk_insertar = QCheckBox("Insertar el guion en «Texto → voz»")
        self.chk_insertar.setChecked(True)
        self.chk_insertar.toggled.connect(self._guardar_config)
        lay.addWidget(self.chk_insertar)

        self.btn_guion = QPushButton("✨ Generar solo el guion")
        self.btn_guion.clicked.connect(self._generar_solo_guion)
        lay.addWidget(self.btn_guion)
        return grupo

    def _grupo_imagenes(self) -> QGroupBox:
        grupo = QGroupBox("Imágenes de los capítulos")
        lay = QVBoxLayout(grupo)
        lay.setSpacing(6)

        self.chk_imagenes = QCheckBox("Generar una imagen por capítulo")
        self.chk_imagenes.setChecked(True)
        self.chk_imagenes.toggled.connect(self._guardar_config)
        lay.addWidget(self.chk_imagenes)

        form = QFormLayout()
        form.setSpacing(6)

        self.cbo_prov_imagen = QComboBox()
        self.cbo_prov_imagen.addItem("Pollinations · Flux (gratis, sin clave)", "pollinations")
        self.cbo_prov_imagen.addItem("OpenRouter · Gemini (calidad, de pago)", "openrouter")
        self.cbo_prov_imagen.addItem("OpenAI · gpt-image (de pago)", "openai")
        self.cbo_prov_imagen.addItem("No generar imágenes", "ninguno")
        self.cbo_prov_imagen.currentIndexChanged.connect(self._cambio_proveedor_imagen)
        form.addRow("Proveedor:", self.cbo_prov_imagen)

        self.txt_modelo_imagen = QLineEdit()
        self.txt_modelo_imagen.setPlaceholderText(MODELO_IMAGEN_DEFECTO)
        self.txt_modelo_imagen.editingFinished.connect(self._guardar_config)
        form.addRow("Modelo:", self.txt_modelo_imagen)

        self.cbo_estilo = QComboBox()
        for clave, nombre in _NOMBRES_ESTILO.items():
            self.cbo_estilo.addItem(nombre, clave)
        self.cbo_estilo.currentIndexChanged.connect(self._guardar_config)
        form.addRow("Estilo:", self.cbo_estilo)
        lay.addLayout(form)

        self.chk_respaldo = QCheckBox(
            "Si el proveedor falla, usar Pollinations gratis"
        )
        self.chk_respaldo.setChecked(True)
        self.chk_respaldo.toggled.connect(self._guardar_config)
        lay.addWidget(self.chk_respaldo)

        self.fila_openai = QWidget()
        fila_oa = QHBoxLayout(self.fila_openai)
        fila_oa.setContentsMargins(0, 0, 0, 0)
        self.txt_clave_openai = QLineEdit()
        self.txt_clave_openai.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_clave_openai.setPlaceholderText("Clave de OpenAI (solo para imágenes)")
        fila_oa.addWidget(self.txt_clave_openai, 1)
        self.btn_guardar_openai = QPushButton("Guardar")
        self.btn_guardar_openai.clicked.connect(self._guardar_clave_openai)
        fila_oa.addWidget(self.btn_guardar_openai)
        self.btn_borrar_openai = QPushButton("Borrar")
        self.btn_borrar_openai.clicked.connect(self._borrar_clave_openai)
        fila_oa.addWidget(self.btn_borrar_openai)
        lay.addWidget(self.fila_openai)
        self.fila_openai.setVisible(False)

        self.lbl_openai = QLabel("")
        self.lbl_openai.setWordWrap(True)
        self.lbl_openai.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(self.lbl_openai)
        self.lbl_openai.setVisible(False)

        self.lbl_imagenes = QLabel(
            "Se guardan en ui/.config/imagenes_generadas y se asignan solas a "
            "los capítulos por su nombre (intro, capítulo N, cierre)."
        )
        self.lbl_imagenes.setWordWrap(True)
        self.lbl_imagenes.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(self.lbl_imagenes)
        return grupo

    def _grupo_voz(self) -> QGroupBox:
        grupo = QGroupBox("Voz")
        lay = QVBoxLayout(grupo)
        lay.setSpacing(6)

        self.chk_voz = QCheckBox("Generar la voz del guion")
        self.chk_voz.setChecked(True)
        self.chk_voz.toggled.connect(self._guardar_config)
        lay.addWidget(self.chk_voz)

        form = QFormLayout()
        form.setSpacing(6)
        self.cbo_prov_voz = QComboBox()
        self.cbo_prov_voz.addItem("Windows / Edge · la voz de siempre", "edge")
        self.cbo_prov_voz.addItem("ElevenLabs · opcional", "elevenlabs")
        self.cbo_prov_voz.currentIndexChanged.connect(self._cambio_proveedor_voz)
        form.addRow("Proveedor:", self.cbo_prov_voz)
        lay.addLayout(form)

        fila = QHBoxLayout()
        self.txt_clave_eleven = QLineEdit()
        self.txt_clave_eleven.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_clave_eleven.setPlaceholderText("Clave de ElevenLabs (opcional)")
        fila.addWidget(self.txt_clave_eleven, 1)
        self.btn_guardar_eleven = QPushButton("Guardar")
        self.btn_guardar_eleven.clicked.connect(self._guardar_clave_eleven)
        fila.addWidget(self.btn_guardar_eleven)
        self.btn_borrar_eleven = QPushButton("Borrar")
        self.btn_borrar_eleven.clicked.connect(self._borrar_clave_eleven)
        fila.addWidget(self.btn_borrar_eleven)
        lay.addLayout(fila)

        fila2 = QHBoxLayout()
        self.cbo_voz_eleven = QComboBox()
        self.cbo_voz_eleven.setToolTip("Voces de tu cuenta de ElevenLabs")
        fila2.addWidget(self.cbo_voz_eleven, 1)
        self.btn_voces_eleven = QPushButton("Cargar voces")
        self.btn_voces_eleven.clicked.connect(self._cargar_voces_eleven)
        fila2.addWidget(self.btn_voces_eleven)
        lay.addLayout(fila2)

        self.lbl_eleven = QLabel("")
        self.lbl_eleven.setWordWrap(True)
        self.lbl_eleven.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(self.lbl_eleven)
        return grupo

    def _grupo_acciones(self) -> QGroupBox:
        grupo = QGroupBox("Generar")
        lay = QVBoxLayout(grupo)
        lay.setSpacing(6)

        fila = QHBoxLayout()
        self.btn_todo = QPushButton("⚡ Generar todo automáticamente")
        self.btn_todo.setObjectName("primary")
        self.btn_todo.clicked.connect(self._generar_todo)
        fila.addWidget(self.btn_todo, 1)
        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.setEnabled(False)
        self.btn_cancelar.clicked.connect(self._cancelar)
        fila.addWidget(self.btn_cancelar)
        lay.addLayout(fila)

        self.barra = QProgressBar()
        self.barra.setRange(0, 100)
        self.barra.setValue(0)
        lay.addWidget(self.barra)

        self.lbl_estado = QLabel("Listo para generar.")
        self.lbl_estado.setWordWrap(True)
        self.lbl_estado.setStyleSheet("color: #38bdf8; font-size: 10px;")
        lay.addWidget(self.lbl_estado)

        self.txt_log = QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setFixedHeight(110)
        self.txt_log.setPlaceholderText("Aquí verás el progreso de la IA…")
        lay.addWidget(self.txt_log)
        return grupo

    def _grupo_mcp(self) -> QGroupBox:
        grupo = QGroupBox("Complemento MCP (opcional)")
        lay = QVBoxLayout(grupo)
        lay.setSpacing(6)

        hint = QLabel(
            "Conecta Studio Cut con agentes externos. El complemento se instala "
            "solo desde aquí, se verifica con SHA-256 y únicamente se abre si "
            "Studio Cut le pasa el token de lanzamiento."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(hint)

        self.lbl_mcp = QLabel("")
        self.lbl_mcp.setWordWrap(True)
        self.lbl_mcp.setStyleSheet("color: #94a3b8; font-size: 10px;")
        lay.addWidget(self.lbl_mcp)

        fila = QHBoxLayout()
        self.btn_mcp_instalar = QPushButton("⬇ Buscar e instalar")
        self.btn_mcp_instalar.clicked.connect(self._mcp_buscar)
        fila.addWidget(self.btn_mcp_instalar)

        self.btn_mcp_local = QPushButton("📁 Desde carpeta…")
        self.btn_mcp_local.clicked.connect(self._mcp_instalar_local)
        fila.addWidget(self.btn_mcp_local)

        self.btn_mcp_probar = QPushButton("▶ Probar")
        self.btn_mcp_probar.clicked.connect(self._mcp_probar)
        fila.addWidget(self.btn_mcp_probar)
        lay.addLayout(fila)

        fila2 = QHBoxLayout()
        self.btn_mcp_copiar = QPushButton("📋 Copiar configuración MCP")
        self.btn_mcp_copiar.clicked.connect(self._mcp_copiar_config)
        fila2.addWidget(self.btn_mcp_copiar)

        self.btn_mcp_desinstalar = QPushButton("Desinstalar")
        self.btn_mcp_desinstalar.clicked.connect(self._mcp_desinstalar)
        fila2.addWidget(self.btn_mcp_desinstalar)
        lay.addLayout(fila2)
        return grupo

    # ------------------------------------------------------------- Config
    def config_actual(self) -> ConfigIA:
        cfg = ConfigIA()
        cfg.proveedor_texto = str(self.cbo_prov_texto.currentData() or "openrouter")
        cfg.base_url_texto = self._base_url_actual()
        cfg.modelo_texto = self.cbo_modelo.currentText().strip() or MODELO_TEXTO_DEFECTO
        cfg.proveedor_imagen = str(self.cbo_prov_imagen.currentData() or "pollinations")
        cfg.modelo_imagen = self.txt_modelo_imagen.text().strip() or MODELO_IMAGEN_DEFECTO
        cfg.estilo_imagen = str(self.cbo_estilo.currentData() or "cinematografico")
        cfg.generar_imagenes = self.chk_imagenes.isChecked()
        cfg.respaldo_imagenes_gratis = self.chk_respaldo.isChecked()
        cfg.proveedor_voz = str(self.cbo_prov_voz.currentData() or "edge")
        cfg.voz_edge = self._voz_edge()
        cfg.voz_elevenlabs = str(self.cbo_voz_eleven.currentData() or "")
        cfg.generar_voz = self.chk_voz.isChecked()
        cfg.insertar_guion = self.chk_insertar.isChecked()
        cfg.duracion_min = self.spin_duracion.value()
        cfg.n_capitulos = self.spin_capitulos.value()
        cfg.tono = self.cbo_tono.currentText().strip() or "cercano y directo"
        cfg.publico = self.cbo_publico.currentText().strip() or "público general"
        return cfg

    def refrescar_desde_config(self) -> None:
        cfg = getattr(self._ventana, "_ia_config", ConfigIA())
        self._cargando = True
        try:
            idx = self.cbo_prov_texto.findData(cfg.proveedor_texto)
            self.cbo_prov_texto.setCurrentIndex(max(0, idx))
            self.txt_base_url.setText(cfg.base_url_texto or "")
            if cfg.modelo_texto:
                pos = self.cbo_modelo.findText(cfg.modelo_texto)
                if pos < 0:
                    self.cbo_modelo.addItem(cfg.modelo_texto, cfg.modelo_texto)
                    pos = self.cbo_modelo.count() - 1
                self.cbo_modelo.setCurrentIndex(pos)

            idx_img = self.cbo_prov_imagen.findData(cfg.proveedor_imagen)
            self.cbo_prov_imagen.setCurrentIndex(max(0, idx_img))
            self.txt_modelo_imagen.setText(cfg.modelo_imagen or "")
            idx_est = self.cbo_estilo.findData(cfg.estilo_imagen)
            self.cbo_estilo.setCurrentIndex(max(0, idx_est))
            self.chk_imagenes.setChecked(cfg.generar_imagenes)
            self.chk_respaldo.setChecked(cfg.respaldo_imagenes_gratis)

            idx_voz = self.cbo_prov_voz.findData(cfg.proveedor_voz)
            self.cbo_prov_voz.setCurrentIndex(max(0, idx_voz))
            self.chk_voz.setChecked(cfg.generar_voz)
            self.chk_insertar.setChecked(cfg.insertar_guion)
            if cfg.voz_elevenlabs:
                pos = self.cbo_voz_eleven.findData(cfg.voz_elevenlabs)
                if pos < 0:
                    self.cbo_voz_eleven.addItem("Voz guardada", cfg.voz_elevenlabs)
                    pos = self.cbo_voz_eleven.count() - 1
                self.cbo_voz_eleven.setCurrentIndex(pos)

            self.spin_duracion.setValue(max(1, int(cfg.duracion_min)))
            self.spin_capitulos.setValue(max(1, int(cfg.n_capitulos)))
            self._poner_texto(self.cbo_tono, cfg.tono)
            self._poner_texto(self.cbo_publico, cfg.publico)
        finally:
            self._cargando = False
        self._cambio_proveedor_texto()
        self._cambio_proveedor_imagen()
        self._cambio_proveedor_voz()

    @staticmethod
    def _poner_texto(combo: QComboBox, texto: str) -> None:
        if not texto:
            return
        pos = combo.findText(texto)
        if pos < 0:
            combo.addItem(texto)
            pos = combo.count() - 1
        combo.setCurrentIndex(pos)

    def _guardar_config(self, *_args) -> None:
        if self._cargando:
            return
        self._ventana._ia_config = self.config_actual()
        try:
            self._ventana._guardar_sesion()
        except Exception:  # noqa: BLE001
            pass

    def _base_url_actual(self) -> str:
        proveedor = str(self.cbo_prov_texto.currentData() or "openrouter")
        if proveedor == "openrouter":
            return BASE_OPENROUTER
        if proveedor == "ollama":
            texto = self.txt_base_url.text().strip()
            return texto or "http://localhost:11434/v1"
        texto = self.txt_base_url.text().strip()
        return texto or BASE_OPENAI

    def _voz_edge(self) -> str:
        combo = getattr(self._ventana, "combo_voz", None)
        if combo is None:
            return ""
        return str(combo.currentData() or "")

    # --------------------------------------------------------- Cambios de UI
    def _cambio_proveedor_texto(self, *_args) -> None:
        proveedor = str(self.cbo_prov_texto.currentData() or "openrouter")
        self.txt_base_url.setVisible(proveedor != "openrouter")
        if proveedor == "openrouter":
            self.txt_base_url.setText(BASE_OPENROUTER)
        elif proveedor == "ollama" and not self.txt_base_url.text().strip():
            self.txt_base_url.setText("http://localhost:11434/v1")
        elif proveedor == "compatible" and (
            not self.txt_base_url.text().strip()
            or self.txt_base_url.text().strip() == BASE_OPENROUTER
        ):
            self.txt_base_url.setText(BASE_OPENAI)
        self._guardar_config()

    def _cambio_proveedor_imagen(self, *_args) -> None:
        proveedor = str(self.cbo_prov_imagen.currentData() or "pollinations")
        es_openai = proveedor == "openai"
        self.fila_openai.setVisible(es_openai)
        self.lbl_openai.setVisible(es_openai)
        actual = self.txt_modelo_imagen.text().strip()
        conocidos = {"", "flux", MODELO_IMAGEN_DEFECTO, "gpt-image-1"}
        predeterminado = {
            "pollinations": "flux",
            "openrouter": MODELO_IMAGEN_DEFECTO,
            "openai": "gpt-image-1",
            "ninguno": "",
        }.get(proveedor, "")
        if actual in conocidos and actual != predeterminado:
            self.txt_modelo_imagen.setText(predeterminado)
        if proveedor == "pollinations":
            self.txt_modelo_imagen.setPlaceholderText("flux (gratis)")
        elif proveedor == "openrouter":
            self.txt_modelo_imagen.setPlaceholderText(MODELO_IMAGEN_DEFECTO)
        elif proveedor == "openai":
            self.txt_modelo_imagen.setPlaceholderText("gpt-image-1")
        self.txt_modelo_imagen.setEnabled(proveedor != "ninguno")
        self._guardar_config()

    def _cambio_proveedor_voz(self, *_args) -> None:
        es_eleven = str(self.cbo_prov_voz.currentData() or "edge") == "elevenlabs"
        for widget in (
            self.txt_clave_eleven, self.btn_guardar_eleven, self.btn_borrar_eleven,
            self.cbo_voz_eleven, self.btn_voces_eleven,
        ):
            widget.setEnabled(es_eleven)
        if es_eleven and not self.cbo_voz_eleven.count():
            self.lbl_eleven.setText(
                "Guarda tu clave y pulsa «Cargar voces». Si ElevenLabs falla, "
                "se usa la voz de Windows sin cortar el flujo."
            )
        self._guardar_config()

    # -------------------------------------------------------------- Claves
    def _actualizar_estado_claves(self) -> None:
        pista = credenciales.pista_clave("openrouter")
        if pista:
            self.lbl_clave_texto.setText(
                f"✓ Clave de OpenRouter guardada ({pista}). Se guarda cifrada con "
                "la cuenta de Windows."
            )
            self.txt_clave_texto.setPlaceholderText(f"Guardada · {pista}")
        else:
            self.lbl_clave_texto.setText(
                "Sin clave guardada. Pega tu clave de OpenRouter y pulsa Guardar."
            )
            self.txt_clave_texto.setPlaceholderText("sk-or-v1-…")

    def _guardar_clave_texto(self) -> None:
        clave = self.txt_clave_texto.text().strip()
        if not clave:
            QMessageBox.information(
                self, "Sin clave", "Escribe la clave antes de guardarla."
            )
            return
        try:
            credenciales.guardar_clave("openrouter", clave)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return
        self.txt_clave_texto.clear()
        self._actualizar_estado_claves()
        self.lbl_estado.setText("Clave de OpenRouter guardada de forma cifrada.")

    def _borrar_clave_texto(self) -> None:
        if not credenciales.hay_clave("openrouter"):
            return
        resp = QMessageBox.question(
            self, "Borrar clave",
            "¿Borrar la clave de OpenRouter de este equipo?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        credenciales.borrar_clave("openrouter")
        self._actualizar_estado_claves()
        self.lbl_estado.setText("Clave de OpenRouter borrada.")

    def _probar_clave_texto(self) -> None:
        proveedor = str(self.cbo_prov_texto.currentData() or "openrouter")
        clave = self.txt_clave_texto.text().strip() or credenciales.obtener_clave("openrouter")
        base = self._base_url_actual()
        local = proveedor == "ollama"
        if not clave and not local:
            QMessageBox.information(
                self, "Sin clave", "Escribe o guarda una clave primero."
            )
            return
        modelo = self.cbo_modelo.currentText().strip()
        self.lbl_estado.setText("Probando la conexión…")
        self._lanzar_tarea(
            lambda: verificar_clave(clave, base, modelo=modelo),
            self._clave_probada_ok,
            self._clave_probada_fallo,
        )

    def _clave_probada_ok(self, mensaje) -> None:
        self.lbl_estado.setText(f"✓ {mensaje}")
        QMessageBox.information(self, "Conexión correcta", str(mensaje))

    def _clave_probada_fallo(self, mensaje: str) -> None:
        self.lbl_estado.setText("No se pudo conectar. Revisa la clave.")
        QMessageBox.warning(self, "Error de conexión", mensaje)

    def _actualizar_estado_eleven(self) -> None:
        pista = credenciales.pista_clave("elevenlabs")
        if pista:
            self.lbl_eleven.setText(f"✓ Clave de ElevenLabs guardada ({pista}).")
            self.txt_clave_eleven.setPlaceholderText(f"Guardada · {pista}")
        else:
            self.lbl_eleven.setText(
                "Sin clave de ElevenLabs. Si no la usas, la app sigue con la voz "
                "de Windows/Edge."
            )
            self.txt_clave_eleven.setPlaceholderText("Clave de ElevenLabs (opcional)")

    def _guardar_clave_eleven(self) -> None:
        clave = self.txt_clave_eleven.text().strip()
        if not clave:
            return
        try:
            credenciales.guardar_clave("elevenlabs", clave)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return
        self.txt_clave_eleven.clear()
        self._actualizar_estado_eleven()
        self.lbl_estado.setText("Clave de ElevenLabs guardada.")

    def _borrar_clave_eleven(self) -> None:
        credenciales.borrar_clave("elevenlabs")
        self._actualizar_estado_eleven()
        self.cbo_voz_eleven.clear()

    def _actualizar_estado_openai(self) -> None:
        pista = credenciales.pista_clave("openai")
        if pista:
            self.lbl_openai.setText(f"✓ Clave de OpenAI guardada ({pista}).")
            self.txt_clave_openai.setPlaceholderText(f"Guardada · {pista}")
        else:
            self.lbl_openai.setText(
                "Solo necesaria si eliges OpenAI para las imágenes. Para el "
                "resto del flujo no hace falta."
            )
            self.txt_clave_openai.setPlaceholderText(
                "Clave de OpenAI (solo para imágenes)"
            )

    def _guardar_clave_openai(self) -> None:
        clave = self.txt_clave_openai.text().strip()
        if not clave:
            return
        try:
            credenciales.guardar_clave("openai", clave)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return
        self.txt_clave_openai.clear()
        self._actualizar_estado_openai()
        self.lbl_estado.setText("Clave de OpenAI guardada de forma cifrada.")

    def _borrar_clave_openai(self) -> None:
        credenciales.borrar_clave("openai")
        self._actualizar_estado_openai()

    def _cargar_voces_eleven(self) -> None:
        clave = self.txt_clave_eleven.text().strip() or credenciales.obtener_clave("elevenlabs")
        if not clave:
            QMessageBox.information(
                self, "Sin clave", "Guarda primero tu clave de ElevenLabs."
            )
            return
        self.lbl_eleven.setText("Cargando voces de ElevenLabs…")
        self._lanzar_tarea(
            lambda: listar_voces_elevenlabs(clave),
            self._voces_eleven_listas,
            lambda m: self.lbl_eleven.setText(f"No se pudieron cargar: {m}"),
        )

    def _voces_eleven_listas(self, voces) -> None:
        self.cbo_voz_eleven.clear()
        for voz in voces or []:
            self.cbo_voz_eleven.addItem(voz.get("nombre") or "Voz", voz.get("id"))
        if self.cbo_voz_eleven.count():
            self.lbl_eleven.setText(
                f"✓ {self.cbo_voz_eleven.count()} voces disponibles."
            )
            self._guardar_config()
        else:
            self.lbl_eleven.setText("La cuenta no tiene voces disponibles.")

    def _listar_modelos(self) -> None:
        clave = credenciales.obtener_clave("openrouter")
        if not clave:
            QMessageBox.information(
                self, "Sin clave", "Guarda tu clave de OpenRouter primero."
            )
            return
        self.lbl_modelo_info.setText("Consultando modelos gratis…")
        self._lanzar_tarea(
            lambda: listar_modelos_gratis(clave),
            self._modelos_listos,
            lambda m: self.lbl_modelo_info.setText(f"Error: {m}"),
        )

    def _modelos_listos(self, modelos) -> None:
        actual = self.cbo_modelo.currentText().strip()
        self.cbo_modelo.blockSignals(True)
        self.cbo_modelo.clear()
        for modelo in modelos or []:
            texto = modelo["id"]
            self.cbo_modelo.addItem(texto, texto)
            pos = self.cbo_modelo.count() - 1
            self.cbo_modelo.setItemData(
                pos,
                f"{modelo['nombre']} · {modelo['contexto'] // 1000}k contexto",
                Qt.ItemDataRole.ToolTipRole,
            )
        if not self.cbo_modelo.count():
            self.cbo_modelo.addItem(MODELO_TEXTO_DEFECTO, MODELO_TEXTO_DEFECTO)
        pos = self.cbo_modelo.findText(actual)
        if pos < 0:
            self.cbo_modelo.addItem(actual or MODELO_TEXTO_DEFECTO)
            pos = self.cbo_modelo.count() - 1
        self.cbo_modelo.setCurrentIndex(pos)
        self.cbo_modelo.blockSignals(False)
        self.lbl_modelo_info.setText(
            f"{len(modelos or [])} modelos gratis disponibles."
        )

    # ------------------------------------------------------------ Tareas
    def _lanzar_tarea(self, funcion, al_listo, al_fallo) -> None:
        tarea = _Tarea(funcion, self)
        self._tareas.append(tarea)
        tarea.listo.connect(al_listo)
        tarea.fallo.connect(al_fallo)
        tarea.finished.connect(lambda: self._olvidar_tarea(tarea))
        tarea.start()

    def _olvidar_tarea(self, tarea: _Tarea) -> None:
        if tarea in self._tareas:
            self._tareas.remove(tarea)
        tarea.deleteLater()

    # ------------------------------------------------------------ Generar
    def _claves(self) -> dict:
        return {
            "openrouter": credenciales.obtener_clave("openrouter"),
            "openai": credenciales.obtener_clave("openai"),
            "elevenlabs": credenciales.obtener_clave("elevenlabs"),
        }

    def _generar_solo_guion(self) -> None:
        cfg = self.config_actual()
        cfg.generar_imagenes = False
        cfg.generar_voz = False
        self._arrancar(cfg, solo_guion=True)

    def _generar_todo(self) -> None:
        cfg = self.config_actual()
        self._arrancar(cfg, solo_guion=False)

    def _arrancar(self, cfg: ConfigIA, *, solo_guion: bool) -> None:
        idea = self.txt_idea.toPlainText().strip()
        if not idea:
            QMessageBox.information(
                self, "Falta la idea",
                "Escribe de qué trata el video (una frase basta).",
            )
            return
        claves = self._claves()
        if cfg.proveedor_texto == "openrouter" and not claves["openrouter"]:
            QMessageBox.information(
                self, "Falta la clave",
                "Pega tu clave de OpenRouter y pulsa Guardar.",
            )
            return

        notas: list[str] = []
        if cfg.proveedor_imagen == "openrouter" and not claves["openrouter"]:
            cfg.proveedor_imagen = "pollinations"
            notas.append(
                "[Imágenes] Sin clave de OpenRouter; se usa Pollinations (gratis)."
            )
        if cfg.proveedor_imagen == "openai" and not claves["openai"]:
            cfg.proveedor_imagen = "pollinations"
            notas.append(
                "[Imágenes] Sin clave de OpenAI; se usa Pollinations (gratis)."
            )

        self._guardar_config()
        self.txt_log.clear()
        for nota in notas:
            self._log(nota)
        self.barra.setValue(0)
        self._ultimo_guion = None

        carpeta = Path(__file__).resolve().parents[2] / "ui" / ".config" / "imagenes_generadas"

        trabajador = TrabajadorIA(idea, cfg, claves, str(carpeta), self)
        trabajador.progreso.connect(self._progreso)
        trabajador.log.connect(self._log)
        trabajador.guion.connect(self._guion_listo)
        trabajador.imagenes.connect(self._imagenes_listas)
        trabajador.voz.connect(self._voz_lista)
        trabajador.listo.connect(self._terminado)
        trabajador.fallo.connect(self._fallo)
        self._trabajador_ia = trabajador

        self.btn_todo.setEnabled(False)
        self.btn_guion.setEnabled(False)
        self.btn_cancelar.setEnabled(True)
        self.lbl_estado.setText(
            "Generando el guion…" if solo_guion else "Generando guion, imágenes y voz…"
        )
        trabajador.start()

    def _progreso(self, valor: int, etapa: str) -> None:
        self.barra.setValue(max(0, min(100, int(valor))))
        self.lbl_estado.setText(f"{etapa.capitalize()}… ({valor}%)")

    def _log(self, mensaje: str) -> None:
        self.txt_log.appendPlainText(mensaje)

    def _guion_listo(self, texto: str, guion) -> None:
        self._ultimo_guion = guion
        self._log(f"[Guion] {guion.palabras} palabras, {len(guion.capitulos)} capítulos.")
        if self.chk_insertar.isChecked():
            self._ventana._ia_aplicar_guion(texto)
            self._log("[Guion] Insertado en «Texto → voz».")

    def _imagenes_listas(self, rutas: list) -> None:
        if not rutas:
            return
        self._ventana._ia_aplicar_imagenes(list(rutas))
        self._log(f"[Imágenes] {len(rutas)} imágenes añadidas a la línea de tiempo.")

    def _voz_lista(self, ruta: str) -> None:
        if not ruta:
            return
        self._ventana._ia_aplicar_voz(ruta, self._ultimo_guion)
        self._log("[Voz] Audio generado. Detectando capítulos y subtítulos…")

    def _terminado(self, resultado) -> None:
        self._trabajador_ia = None
        self.btn_todo.setEnabled(True)
        self.btn_guion.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.barra.setValue(100)
        self.lbl_estado.setText("✓ Proceso completado.")
        self._log("[IA] Proceso completado.")

    def _fallo(self, mensaje: str) -> None:
        self._trabajador_ia = None
        self.btn_todo.setEnabled(True)
        self.btn_guion.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.barra.setValue(0)
        self.lbl_estado.setText("El proceso se detuvo. Revisa el detalle.")
        self._log(f"ERROR: {mensaje}")
        QMessageBox.warning(self, "No se pudo completar", mensaje)

    def _cancelar(self) -> None:
        if self._trabajador_ia is not None:
            self._trabajador_ia.cancelar()
            self.lbl_estado.setText("Cancelando…")

    # -------------------------------------------------------------- MCP
    def _actualizar_estado_mcp(self) -> None:
        estado = plugins.estado()
        if estado["instalado"]:
            self.lbl_mcp.setText(
                f"✓ Instalado: versión {estado['version']} · {estado['ruta']}"
            )
            self.btn_mcp_desinstalar.setEnabled(True)
            self.btn_mcp_probar.setEnabled(True)
            self.btn_mcp_instalar.setText("🔄 Buscar actualización")
        else:
            self.lbl_mcp.setText(
                "No instalado. La app ya incluye un servidor MCP interno; el "
                "complemento permite actualizarlo sin tocar Studio Cut."
            )
            self.btn_mcp_desinstalar.setEnabled(False)
            self.btn_mcp_probar.setEnabled(False)
            self.btn_mcp_instalar.setText("⬇ Buscar e instalar")

    def _mcp_buscar(self) -> None:
        self.lbl_mcp.setText("Consultando GitHub…")
        self._lanzar_tarea(
            plugins.consultar_manifest,
            self._mcp_manifest_listo,
            self._mcp_error,
        )

    def _mcp_manifest_listo(self, manifiesto: dict) -> None:
        self._manifest = manifiesto
        version = manifiesto["version"]
        actual = plugins.estado()["version"]
        if actual and actual == version:
            QMessageBox.information(
                self, "Sin novedades", f"Ya tienes la versión {version}."
            )
            self._actualizar_estado_mcp()
            return
        detalle = (
            f"Versión: {version}\n"
            f"Descripción: {manifiesto.get('descripcion', '')}\n"
            f"Autor: {manifiesto.get('autor', '')}\n"
            f"SHA-256: {manifiesto['sha256'][:16]}…\n\n"
            "Se descargará de GitHub y se verificará el hash antes de instalar. "
            "¿Continuar?"
        )
        resp = QMessageBox.question(self, "Instalar complemento", detalle)
        if resp != QMessageBox.StandardButton.Yes:
            self.lbl_mcp.setText("Instalación cancelada.")
            return
        self.lbl_mcp.setText("Descargando y verificando…")
        self._lanzar_tarea(
            lambda: plugins.instalar_desde_zip(
                plugins.descargar(str(manifiesto["url"])),
                version=str(manifiesto["version"]),
                sha256_esperado=str(manifiesto["sha256"]),
            ),
            self._mcp_instalado,
            self._mcp_error,
        )

    def _mcp_instalar_local(self) -> None:
        carpeta = QFileDialog.getExistingDirectory(
            self, "Elegir carpeta del complemento (con studiocut_ai)"
        )
        if not carpeta:
            return
        self.lbl_mcp.setText("Instalando desde carpeta local…")
        self._lanzar_tarea(
            lambda: plugins.instalar_desde_carpeta(carpeta),
            self._mcp_instalado,
            self._mcp_error,
        )

    def _mcp_instalado(self, ruta) -> None:
        self._actualizar_estado_mcp()
        self.lbl_mcp.setText(f"✓ Complemento instalado en {ruta}")
        QMessageBox.information(
            self, "Complemento instalado",
            "Listo. Pulsa «Probar» para comprobar que el servidor MCP responde.",
        )

    def _mcp_error(self, mensaje: str) -> None:
        self._actualizar_estado_mcp()
        self.lbl_mcp.setText(f"No se pudo instalar: {mensaje}")
        QMessageBox.warning(self, "Complemento MCP", mensaje)

    def _mcp_desinstalar(self) -> None:
        resp = QMessageBox.question(
            self, "Desinstalar complemento",
            "Se borrará la carpeta del complemento. ¿Continuar?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        plugins.desinstalar()
        self._actualizar_estado_mcp()
        self.lbl_mcp.setText("Complemento desinstalado.")

    def _mcp_probar(self) -> None:
        self.lbl_mcp.setText("Arrancando el servidor MCP…")
        self._lanzar_tarea(
            lambda: plugins.lanzar_mcp(prueba=True),
            self._mcp_probado,
            self._mcp_error,
        )

    def _mcp_probado(self, proceso) -> None:
        self.lbl_mcp.setText("✓ El servidor MCP respondió correctamente.")
        QMessageBox.information(
            self, "MCP conectado",
            "El complemento arrancó y respondió al ping. Ya puedes copiar la "
            "configuración MCP para tu cliente de IA.",
        )

    def _mcp_copiar_config(self) -> None:
        resp = QMessageBox.question(
            self, "Copiar configuración MCP",
            "El JSON incluirá tu clave de OpenRouter en la sección «env» para "
            "que el cliente externo pueda usarla. No lo compartas ni lo subas "
            "a ningún sitio. ¿Copiar?",
        )
        if resp != QMessageBox.StandardButton.Yes:
            return
        config = plugins.configuracion_mcp(incluir_claves=True)
        QApplication.clipboard().setText(json.dumps(config, ensure_ascii=False, indent=2))
        self.lbl_mcp.setText(
            "✓ Configuración copiada al portapapeles. Pégala en tu cliente MCP."
        )
