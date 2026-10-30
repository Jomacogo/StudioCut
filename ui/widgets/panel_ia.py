"""Pestaña «🤖 IA»: guion, imágenes y voz casi automáticos.

Todo el trabajo pesado (red, TTS, imágenes) ocurre en hilos para que la
interfaz siga respondiendo. Las claves se guardan cifradas con DPAPI y nunca
se escriben en el proyecto ni en los ajustes.
"""
from __future__ import annotations

import json
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from core import hardware, plugins
from core.ia import credenciales
from core.ia import local as ia_local
from core.ia.imagenes import ESTILOS
from core.ia.guion import PROMPT_CANAL_DEFECTO
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

URL_COMFYUI_DESCARGA = "https://github.com/comfyanonymous/ComfyUI/releases/latest"
URL_MODELO_TURBO = (
    "https://huggingface.co/stabilityai/sdxl-turbo/resolve/main/"
    "sd_xl_turbo_1.0_fp16.safetensors"
)

_GUIA_LOCAL = """PASO 1 · Instalar el motor (solo una vez)
  Pulsa «⬇ ComfyUI portable» (abre la página oficial de descargas).
  Descarga ComfyUI_windows_portable_nvidia.7z (unos 1,9 GB, comprimido).
  Descomprímelo con 7-Zip en C:\\ComfyUI.

  IMPORTANTE: no lo pongas dentro de OneDrive, el Escritorio sincronizado
  ni una carpeta con acentos. Los modelos pesan 7 GB y OneDrive intentaría
  subirlos cada vez, además de bloquear archivos mientras se generan.

PASO 2 · Descargar un modelo que quepa en 8 GB
  Pulsa «⬇ SDXL-Turbo (7 GB)» y espera: el archivo se llama
  sd_xl_turbo_1.0_fp16.safetensors.
  Muévelo (no lo dejes en Descargas) a:
      C:\\ComfyUI\\ComfyUI\\models\\checkpoints\\
  También sirve cualquier SDXL fp16 de ~6,5 GB o SDXL con LoRA Lightning.

PASO 3 · Arrancar el motor
  Doble clic en C:\\ComfyUI\\run_nvidia_gpu.bat
  Espera a que aparezca «Starting server». La primera vez tarda un poco
  más porque carga el modelo en la tarjeta.
  NO cierres esa ventana negra: es el motor que usa Studio Cut.
  Se abrirá una pestaña del navegador en http://127.0.0.1:8188; puedes
  cerrarla, el programa sigue funcionando.

PASO 4 · Conectar Studio Cut con ese motor
  1. En este panel: Proveedor de imágenes → «IA local en tu PC».
  2. Programa: ComfyUI.  Dirección: http://127.0.0.1:8188
  3. Pulsa «🔍 Buscar programa»: el modelo debe aparecer en la lista.
  4. Elige el modelo y pulsa «▶ Probar»: genera una imagen de prueba y te
     dice cuánto pesa. Si eso sale bien, ya está conectado.

PASO 5 · Ajustes para 8 GB de VRAM
  • Pasos: 6 es lo correcto con SDXL-Turbo (la app lo limita a 8).
  • Tamaño: 1280x720 para el video; 1024x1024 para probar.
  • Cada imagen tarda aproximadamente 15-40 s en una RTX 4060 Ti.
  • Genera de una en una: la app ya lo hace así con IA local.

SI ALGO FALLA
  • «No hay respuesta de ComfyUI» → la ventana negra está cerrada.
  • «No hay checkpoints instalados» → el .safetensors no está en
    models\\checkpoints, o se descargó a medias.
  • Error de nodo en la consola de ComfyUI → modelo incompleto; bórralo y
    vuelve a descargarlo.
  • Se queda sin memoria → baja a 1024x576 y cierra Chrome o juegos.

OPCIONAL · Flux (más calidad, más lento)
  Flux.1-schnell existe en GGUF Q4 de 6,8 GB, pero con 16 GB de RAM el
  margen es pequeño. Requiere el nodo extra ComfyUI-GGUF, el codificador de
  texto y el VAE de Flux, siguiendo el tutorial oficial de ComfyUI.
  Cuando el workflow funcione en ComfyUI: «Workflow → Export (API)»,
  guarda el JSON y elígelo en el campo «Workflow» de este panel. Studio Cut
  rellena dentro de tu workflow el prompt, el tamaño, la semilla y los
  pasos de cada escena.

Privacidad: la IA local no envía prompts ni imágenes a internet y no gasta
créditos. Todo ocurre en tu tarjeta gráfica."""


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
        self._ultimo_paquete: dict = {}
        self._manifest = None

        self._construir()
        self.refrescar_desde_config()
        self._actualizar_estado_claves()
        self._actualizar_estado_eleven()
        self._actualizar_estado_openai()
        self._actualizar_estado_useapi()
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
        lay.addWidget(self._grupo_paquete())
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
        self.spin_duracion.setValue(30)
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

        self.txt_prompt_canal = QPlainTextEdit()
        self.txt_prompt_canal.setObjectName("PromptCanalIA")
        self.txt_prompt_canal.setPlaceholderText("Instrucciones de tu canal para guiones y materiales…")
        self.txt_prompt_canal.setPlainText(PROMPT_CANAL_DEFECTO)
        self.txt_prompt_canal.setMinimumHeight(180)
        self.txt_prompt_canal.setMaximumHeight(260)
        self.txt_prompt_canal.setToolTip(
            "Prompt base editable. Se conserva en los ajustes del proyecto y guía "
            "el guion, los materiales de YouTube y las propuestas visuales."
        )
        self.txt_prompt_canal.textChanged.connect(self._guardar_config)
        lay.addWidget(QLabel("Prompt del canal (editable):"))
        lay.addWidget(self.txt_prompt_canal)

        self.chk_insertar = QCheckBox("Insertar el guion en «Texto → voz»")
        self.chk_insertar.setChecked(True)
        self.chk_insertar.toggled.connect(self._guardar_config)
        lay.addWidget(self.chk_insertar)

        fila_acciones = QHBoxLayout()
        self.btn_guion = QPushButton("✨ Generar solo el guion")
        self.btn_guion.clicked.connect(self._generar_solo_guion)
        fila_acciones.addWidget(self.btn_guion)
        self.btn_paquete = QPushButton("📦 Preparar publicación")
        self.btn_paquete.clicked.connect(self._generar_paquete_solo)
        fila_acciones.addWidget(self.btn_paquete)
        lay.addLayout(fila_acciones)
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
        self.cbo_prov_imagen.addItem("Google Flow · UseAPI", "useapi")
        self.cbo_prov_imagen.addItem("IA local en tu PC · ComfyUI / Forge", "local")
        self.cbo_prov_imagen.addItem("No generar imágenes", "ninguno")
        self.cbo_prov_imagen.currentIndexChanged.connect(self._cambio_proveedor_imagen)
        form.addRow("Proveedor:", self.cbo_prov_imagen)

        self.fila_modelo_img = QWidget()
        fila_mi = QHBoxLayout(self.fila_modelo_img)
        fila_mi.setContentsMargins(0, 0, 0, 0)
        fila_mi.addWidget(QLabel("Modelo:"))
        self.txt_modelo_imagen = QLineEdit()
        self.txt_modelo_imagen.setPlaceholderText(MODELO_IMAGEN_DEFECTO)
        self.txt_modelo_imagen.editingFinished.connect(self._guardar_config)
        fila_mi.addWidget(self.txt_modelo_imagen, 1)
        form.addRow(self.fila_modelo_img)

        self.cbo_estilo = QComboBox()
        for clave, nombre in _NOMBRES_ESTILO.items():
            self.cbo_estilo.addItem(nombre, clave)
        self.cbo_estilo.currentIndexChanged.connect(self._guardar_config)
        form.addRow("Estilo:", self.cbo_estilo)
        lay.addLayout(form)

        lay.addWidget(self._fila_ia_local())

        self.chk_miniatura = QCheckBox("Generar miniatura 16:9 para YouTube")
        self.chk_miniatura.setChecked(True)
        self.chk_miniatura.toggled.connect(self._guardar_config)
        lay.addWidget(self.chk_miniatura)

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

        self.fila_useapi = QWidget()
        fila_useapi = QHBoxLayout(self.fila_useapi)
        fila_useapi.setContentsMargins(0, 0, 0, 0)
        self.txt_cuenta_useapi = QLineEdit()
        self.txt_cuenta_useapi.setPlaceholderText("Email Google Flow configurado en UseAPI (opcional)")
        self.txt_cuenta_useapi.editingFinished.connect(self._guardar_config)
        fila_useapi.addWidget(self.txt_cuenta_useapi, 1)
        self.txt_token_useapi = QLineEdit()
        self.txt_token_useapi.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_token_useapi.setPlaceholderText("Token API de UseAPI")
        fila_useapi.addWidget(self.txt_token_useapi, 1)
        self.btn_guardar_useapi = QPushButton("Guardar token")
        self.btn_guardar_useapi.clicked.connect(self._guardar_token_useapi)
        fila_useapi.addWidget(self.btn_guardar_useapi)
        self.btn_borrar_useapi = QPushButton("Borrar")
        self.btn_borrar_useapi.clicked.connect(self._borrar_token_useapi)
        fila_useapi.addWidget(self.btn_borrar_useapi)
        lay.addWidget(self.fila_useapi)
        self.fila_useapi.setVisible(False)

        self.lbl_openai = QLabel("")
        self.lbl_openai.setWordWrap(True)
        self.lbl_openai.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(self.lbl_openai)
        self.lbl_openai.setVisible(False)
        self.lbl_useapi = QLabel("")
        self.lbl_useapi.setWordWrap(True)
        self.lbl_useapi.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(self.lbl_useapi)
        self.lbl_useapi.setVisible(False)

        self.lbl_imagenes = QLabel(
            "Se guardan en ui/.config/imagenes_generadas y se asignan solas a "
            "los capítulos por su nombre (intro, capítulo N, cierre)."
        )
        self.lbl_imagenes.setWordWrap(True)
        self.lbl_imagenes.setStyleSheet("color: #64748b; font-size: 10px;")
        lay.addWidget(self.lbl_imagenes)
        return grupo

    def _fila_ia_local(self) -> QWidget:
        """Ajustes de la IA instalada en el equipo (ComfyUI o Forge)."""
        marco = QFrame()
        marco.setObjectName("IALocalBox")
        lay = QVBoxLayout(marco)
        lay.setContentsMargins(8, 6, 8, 6)
        lay.setSpacing(6)
        self.fila_local = marco

        self.lbl_equipo = QLabel("Comprobando el equipo…")
        self.lbl_equipo.setWordWrap(True)
        self.lbl_equipo.setObjectName("HintLabel")
        lay.addWidget(self.lbl_equipo)

        form = QFormLayout()
        form.setSpacing(6)
        self.cbo_backend_local = QComboBox()
        for clave, nombre in ia_local.BACKENDS.items():
            self.cbo_backend_local.addItem(nombre, clave)
        self.cbo_backend_local.currentIndexChanged.connect(self._cambio_backend_local)
        form.addRow("Programa:", self.cbo_backend_local)

        self.txt_url_local = QLineEdit()
        self.txt_url_local.setPlaceholderText("http://127.0.0.1:8188")
        self.txt_url_local.editingFinished.connect(self._guardar_config)
        form.addRow("Dirección:", self.txt_url_local)

        self.cbo_modelo_local = QComboBox()
        self.cbo_modelo_local.setEditable(True)
        self.cbo_modelo_local.setToolTip(
            "Checkpoint instalado en el programa local. «Detectar» rellena la "
            "lista con los que tengas."
        )
        self.cbo_modelo_local.currentTextChanged.connect(self._guardar_config)
        form.addRow("Modelo:", self.cbo_modelo_local)

        self.spin_pasos_local = QSpinBox()
        self.spin_pasos_local.setRange(4, 80)
        self.spin_pasos_local.setValue(25)
        self.spin_pasos_local.setToolTip(
            "Más pasos = más calidad y más tiempo. Con Flux schnell bastan 4-8."
        )
        self.spin_pasos_local.valueChanged.connect(self._guardar_config)
        form.addRow("Pasos:", self.spin_pasos_local)

        fila_wf = QWidget()
        lay_wf = QHBoxLayout(fila_wf)
        lay_wf.setContentsMargins(0, 0, 0, 0)
        self.txt_workflow_local = QLineEdit()
        self.txt_workflow_local.setPlaceholderText(
            "Opcional: workflow API de ComfyUI (Flux GGUF, LoRAs…)"
        )
        self.txt_workflow_local.editingFinished.connect(self._guardar_config)
        lay_wf.addWidget(self.txt_workflow_local, 1)
        self.btn_workflow_local = QPushButton("…")
        self.btn_workflow_local.setFixedWidth(28)
        self.btn_workflow_local.setToolTip("Elegir un workflow JSON exportado en formato API")
        self.btn_workflow_local.clicked.connect(self._elegir_workflow_local)
        lay_wf.addWidget(self.btn_workflow_local)
        form.addRow("Workflow:", fila_wf)
        lay.addLayout(form)

        fila_botones = QHBoxLayout()
        self.btn_detectar_local = QPushButton("🔍 Buscar programa")
        self.btn_detectar_local.clicked.connect(self._detectar_local)
        fila_botones.addWidget(self.btn_detectar_local)
        self.btn_probar_local = QPushButton("▶ Probar")
        self.btn_probar_local.setToolTip("Genera una imagen de prueba para comprobar que funciona")
        self.btn_probar_local.clicked.connect(self._probar_local)
        fila_botones.addWidget(self.btn_probar_local)
        self.btn_guia_local = QPushButton("📖 Guía")
        self.btn_guia_local.setToolTip("Cómo instalar la IA local paso a paso")
        self.btn_guia_local.clicked.connect(self._mostrar_guia_local)
        fila_botones.addWidget(self.btn_guia_local)
        lay.addLayout(fila_botones)

        self.lbl_local_estado = QLabel("")
        self.lbl_local_estado.setWordWrap(True)
        self.lbl_local_estado.setObjectName("HintLabel")
        lay.addWidget(self.lbl_local_estado)

        marco.setVisible(False)
        QTimer_equipo = getattr(self, "_timer_equipo", None)
        if QTimer_equipo is None:
            # Diferir la detección para no retrasar el arranque de la ventana.
            from PyQt6.QtCore import QTimer
            self._timer_equipo = QTimer(self)
            self._timer_equipo.setSingleShot(True)
            self._timer_equipo.timeout.connect(self._revisar_equipo)
            self._timer_equipo.start(150)
        return marco

    def _grupo_paquete(self) -> QGroupBox:
        grupo = QGroupBox("Materiales y prompts visuales")
        lay = QVBoxLayout(grupo)
        lay.setSpacing(6)

        hint = QLabel(
            "El paquete se completa al generar todo o al pulsar «Preparar publicación». "
            "Puedes editar cada prompt. Las fuentes y datos generados deben comprobarse."
        )
        hint.setWordWrap(True)
        lay.addWidget(hint)

        form = QFormLayout()
        self.cbo_titulos_ia = QComboBox()
        self.cbo_titulos_ia.setEditable(True)
        self.cbo_titulos_ia.currentTextChanged.connect(self._seleccionar_titulo_ia)
        form.addRow("Título sugerido:", self.cbo_titulos_ia)
        lay.addLayout(form)

        # --- Miniatura: plantilla propia del usuario ---
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #232838;")
        lay.addWidget(sep)
        lay.addWidget(QLabel("MI PLANTILLA DE MINIATURA"))
        self.txt_plantilla_miniatura = QPlainTextEdit()
        self.txt_plantilla_miniatura.setPlaceholderText(
            "Opcional. Si escribes aquí, este prompt se usa para todas las "
            "miniaturas. Puedes usar {titulo} y {tema} como marcadores."
        )
        self.txt_plantilla_miniatura.setFixedHeight(84)
        self.txt_plantilla_miniatura.setToolTip(
            "Plantilla fija de miniatura. Ejemplo: «Wide cinematic 16:9 image… "
            "3D typography in aged gold that reads {titulo}»."
        )
        self.txt_plantilla_miniatura.textChanged.connect(self._guardar_config)
        lay.addWidget(self.txt_plantilla_miniatura)
        fila_plantilla = QHBoxLayout()
        self.btn_plantilla_ejemplo = QPushButton("Usar el ejemplo de mi canal")
        self.btn_plantilla_ejemplo.setToolTip(
            "Rellena la plantilla con el estilo de miniatura del canal "
            "(estatua de mármol, fondo negro, tipografía dorada)"
        )
        self.btn_plantilla_ejemplo.clicked.connect(self._plantilla_ejemplo)
        fila_plantilla.addWidget(self.btn_plantilla_ejemplo)
        self.btn_plantilla_limpiar = QPushButton("Vaciar")
        self.btn_plantilla_limpiar.setToolTip("Que la IA vuelva a proponer el prompt de cada miniatura")
        self.btn_plantilla_limpiar.clicked.connect(self._plantilla_vaciar)
        fila_plantilla.addWidget(self.btn_plantilla_limpiar)
        fila_plantilla.addStretch(1)
        lay.addLayout(fila_plantilla)

        self.txt_prompt_miniatura = QPlainTextEdit()
        self.txt_prompt_miniatura.setPlaceholderText("Prompt visual para miniatura 16:9…")
        self.txt_prompt_miniatura.setFixedHeight(95)
        self.txt_prompt_miniatura.setToolTip(
            "Prompt de la miniatura actual. Se rellena solo con la IA o con tu "
            "plantilla; puedes editarlo antes de regenerar."
        )
        lay.addWidget(QLabel("Prompt de la miniatura actual (editable):"))
        lay.addWidget(self.txt_prompt_miniatura)

        fila_mini = QHBoxLayout()
        self.btn_generar_miniatura = QPushButton("Generar / regenerar miniatura")
        self.btn_generar_miniatura.clicked.connect(self._regenerar_miniatura)
        fila_mini.addWidget(self.btn_generar_miniatura)
        self.lbl_vista_miniatura = QLabel("Sin miniatura generada")
        self.lbl_vista_miniatura.setWordWrap(True)
        fila_mini.addWidget(self.lbl_vista_miniatura, 1)
        lay.addLayout(fila_mini)

        # --- Prompts de cada imagen (solo si el usuario lo pide) ---
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #232838;")
        lay.addWidget(sep2)
        self.chk_mostrar_prompts = QCheckBox(
            "Mostrar los prompts de cada imagen (avanzado)"
        )
        self.chk_mostrar_prompts.setToolTip(
            "Si está desmarcado, la IA crea cada prompt y lo coloca en el video "
            "sin mostrarlo. Marca esta opción para revisarlos o cambiarlos uno a uno."
        )
        self.chk_mostrar_prompts.toggled.connect(self._cambio_mostrar_prompts)
        lay.addWidget(self.chk_mostrar_prompts)

        self.caja_prompts = QWidget()
        lay_caja = QVBoxLayout(self.caja_prompts)
        lay_caja.setContentsMargins(0, 2, 0, 0)
        lay_caja.setSpacing(6)
        self.lista_prompts_ia = QComboBox()
        self.lista_prompts_ia.currentIndexChanged.connect(self._cargar_prompt_seccion)
        lay_caja.addWidget(QLabel("Escena / capítulo:"))
        lay_caja.addWidget(self.lista_prompts_ia)
        self.txt_prompt_escena = QPlainTextEdit()
        self.txt_prompt_escena.setPlaceholderText("Prompt visual 16:9 para la escena seleccionada…")
        self.txt_prompt_escena.setFixedHeight(90)
        lay_caja.addWidget(self.txt_prompt_escena)
        self.btn_generar_escena = QPushButton("Generar imagen con este prompt")
        self.btn_generar_escena.clicked.connect(self._regenerar_escena)
        lay_caja.addWidget(self.btn_generar_escena)
        self.caja_prompts.setVisible(False)
        lay.addWidget(self.caja_prompts)

        self.txt_prompt_miniatura.textChanged.connect(self._guardar_paquete_editado)
        self.txt_prompt_escena.textChanged.connect(self._guardar_paquete_editado)
        return grupo

    def _plantilla_ejemplo(self) -> None:
        self.txt_plantilla_miniatura.setPlainText(
            "Wide cinematic 16:9 image. Classical dark academia aesthetic, "
            "resembling a high-quality Renaissance oil painting. A beautifully "
            "sculpted white marble statue of a solitary hooded figure with its "
            "head slightly bowed in deep concentration. Pitch-black moody "
            "background with a single harsh warm amber spotlight from above. "
            "The left side is empty dark negative space with elegant classic "
            "serif 3D typography in aged gold that reads exactly \"{titulo}\". "
            "Highly detailed, masterpiece, no modern elements, solemn and "
            "authoritative atmosphere, no other text."
        )

    def _plantilla_vaciar(self) -> None:
        self.txt_plantilla_miniatura.clear()

    def _cambio_mostrar_prompts(self, marcado: bool) -> None:
        self.caja_prompts.setVisible(bool(marcado))
        if marcado:
            self._refrescar_lista_prompts()
        self._guardar_config()

    def _refrescar_lista_prompts(self) -> None:
        """Rellena el desplegable con las secciones del último paquete."""
        prompts = (self._ultimo_paquete or {}).get("prompts_imagenes") or []
        actual = self.lista_prompts_ia.currentIndex()
        self.lista_prompts_ia.blockSignals(True)
        self.lista_prompts_ia.clear()
        for entrada in prompts:
            if isinstance(entrada, dict):
                self.lista_prompts_ia.addItem(
                    str(entrada.get("seccion") or "Sección"),
                    str(entrada.get("prompt") or ""),
                )
        self.lista_prompts_ia.blockSignals(False)
        if self.lista_prompts_ia.count():
            self.lista_prompts_ia.setCurrentIndex(
                min(max(0, actual), self.lista_prompts_ia.count() - 1)
            )
            self._cargar_prompt_seccion()

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
        cfg.cuenta_flow = self.txt_cuenta_useapi.text().strip()
        cfg.estilo_imagen = str(self.cbo_estilo.currentData() or "cinematografico")
        cfg.generar_imagenes = self.chk_imagenes.isChecked()
        cfg.respaldo_imagenes_gratis = self.chk_respaldo.isChecked()
        cfg.backend_local = str(self.cbo_backend_local.currentData() or "comfyui")
        cfg.url_local = self.txt_url_local.text().strip()
        cfg.modelo_local = self.cbo_modelo_local.currentText().strip()
        cfg.pasos_local = self.spin_pasos_local.value()
        cfg.workflow_local = self.txt_workflow_local.text().strip()
        cfg.prompt_miniatura = self.txt_plantilla_miniatura.toPlainText().strip()
        cfg.mostrar_prompts = self.chk_mostrar_prompts.isChecked()
        cfg.proveedor_voz = str(self.cbo_prov_voz.currentData() or "edge")
        cfg.voz_edge = self._voz_edge()
        cfg.voz_elevenlabs = str(self.cbo_voz_eleven.currentData() or "")
        cfg.generar_voz = self.chk_voz.isChecked()
        cfg.insertar_guion = self.chk_insertar.isChecked()
        cfg.duracion_min = self.spin_duracion.value()
        cfg.n_capitulos = self.spin_capitulos.value()
        cfg.tono = self.cbo_tono.currentText().strip() or "cercano y directo"
        cfg.publico = self.cbo_publico.currentText().strip() or "público general"
        cfg.prompt_canal = self.txt_prompt_canal.toPlainText().strip() or PROMPT_CANAL_DEFECTO
        cfg.generar_miniatura = self.chk_miniatura.isChecked()
        cfg.paquete_editorial = dict(
            self._ultimo_paquete or getattr(self._ventana, "_ia_paquete_editorial", {})
        )
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
            self.txt_cuenta_useapi.setText(cfg.cuenta_flow or "")
            idx_est = self.cbo_estilo.findData(cfg.estilo_imagen)
            self.cbo_estilo.setCurrentIndex(max(0, idx_est))
            self.chk_imagenes.setChecked(cfg.generar_imagenes)
            self.chk_respaldo.setChecked(cfg.respaldo_imagenes_gratis)
            idx_back = self.cbo_backend_local.findData(cfg.backend_local)
            self.cbo_backend_local.setCurrentIndex(max(0, idx_back))
            self.txt_url_local.setText(cfg.url_local or "")
            self.cbo_modelo_local.setEditText(cfg.modelo_local or "")
            self.spin_pasos_local.setValue(max(4, int(cfg.pasos_local or 25)))
            self.txt_workflow_local.setText(cfg.workflow_local or "")
            self.txt_plantilla_miniatura.setPlainText(cfg.prompt_miniatura or "")
            self.chk_mostrar_prompts.setChecked(bool(cfg.mostrar_prompts))
            self.caja_prompts.setVisible(bool(cfg.mostrar_prompts))

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
            self.txt_prompt_canal.setPlainText(cfg.prompt_canal or PROMPT_CANAL_DEFECTO)
            self.chk_miniatura.setChecked(cfg.generar_miniatura)
            self._ultimo_paquete = dict(cfg.paquete_editorial or {})
        finally:
            self._cargando = False
        paquete = self._ultimo_paquete or dict(cfg.paquete_editorial or {})
        self._mostrar_paquete(paquete)
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
        self._ventana._marcar_proyecto_modificado()
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
            "useapi": "nano-banana-2-lite",
            "local": "",
            "ninguno": "",
        }.get(proveedor, "")
        if actual in conocidos and actual != predeterminado:
            self.txt_modelo_imagen.setText(predeterminado)
        es_local = proveedor == "local"
        self.fila_local.setVisible(es_local)
        self.fila_modelo_img.setVisible(not es_local)
        es_useapi = proveedor == "useapi"
        self.fila_useapi.setVisible(es_useapi)
        self.lbl_useapi.setVisible(es_useapi)
        if es_useapi:
            pista = credenciales.pista_clave("useapi")
            self.lbl_useapi.setText(
                f"{'✓ Token guardado cifrado (' + pista + ').' if pista else 'Guarda tu token de UseAPI.'} "
                "Servicio de terceros; requiere cuenta/token UseAPI y cuenta Google Flow configurada allí."
            )
        if proveedor == "pollinations":
            self.txt_modelo_imagen.setPlaceholderText("flux (gratis)")
        elif proveedor == "useapi":
            self.txt_modelo_imagen.setPlaceholderText("nano-banana-2-lite / nano-banana-2 / nano-banana-pro")
        elif proveedor == "openrouter":
            self.txt_modelo_imagen.setPlaceholderText(MODELO_IMAGEN_DEFECTO)
        elif proveedor == "openai":
            self.txt_modelo_imagen.setPlaceholderText("gpt-image-1")
        self.txt_modelo_imagen.setEnabled(proveedor != "ninguno")
        if es_local and not self.txt_url_local.text().strip():
            backend = str(self.cbo_backend_local.currentData() or "comfyui")
            self.txt_url_local.setText(
                ia_local.URL_COMFYUI if backend == "comfyui" else ia_local.URL_FORGE
            )
        self._guardar_config()

    # ---------------------------------------------------------- IA local
    def _revisar_equipo(self, forzar: bool = False) -> None:
        """Detecta la GPU y decide si la IA local es una opción real."""
        try:
            equipo = hardware.detectar_equipo(usar_cache=not forzar)
        except Exception as exc:  # noqa: BLE001 - nunca debe romper la UI
            self.lbl_equipo.setText(f"No se pudo revisar el equipo: {exc}")
            return
        self._equipo = equipo
        texto = equipo.resumen()
        texto += "\n" + (
            equipo.consejo if equipo.cumple_requisitos else equipo.motivo
        )
        self.lbl_equipo.setText(texto)
        self.lbl_equipo.setStyleSheet(
            "color: #64748b; font-size: 10px;"
            if equipo.cumple_requisitos
            else "color: #fbbf24; font-size: 10px;"
        )
        self._habilitar_opcion_local(equipo.cumple_requisitos)
        self.btn_probar_local.setEnabled(equipo.cumple_requisitos)

    def _habilitar_opcion_local(self, habilitada: bool) -> None:
        """Activa o desactiva «IA local» en el desplegable de proveedores."""
        indice = self.cbo_prov_imagen.findData("local")
        if indice < 0:
            return
        item = self.cbo_prov_imagen.model().item(indice)
        if item is not None:
            item.setEnabled(habilitada)
        if habilitada or str(self.cbo_prov_imagen.currentData()) != "local":
            return
        # Se vuelve a un proveedor en la nube sin marcar el proyecto como
        # modificado ni disparar guardados: esto solo es una comprobación.
        bloqueado = self.cbo_prov_imagen.blockSignals(True)
        self.cbo_prov_imagen.setCurrentIndex(
            self.cbo_prov_imagen.findData("pollinations")
        )
        self.cbo_prov_imagen.blockSignals(bloqueado)
        self.fila_local.setVisible(False)
        self.fila_modelo_img.setVisible(True)
        self.lbl_local_estado.setText(
            "La IA local no cumple los requisitos en este equipo; se usará "
            "Pollinations."
        )
        if not self._cargando:
            self._ventana._ia_config = self.config_actual()
            try:
                self._ventana._guardar_sesion()
            except Exception:  # noqa: BLE001
                pass

    def _cambio_backend_local(self, *_args) -> None:
        backend = str(self.cbo_backend_local.currentData() or "comfyui")
        predeterminada = (
            ia_local.URL_COMFYUI if backend == "comfyui" else ia_local.URL_FORGE
        )
        actual = self.txt_url_local.text().strip()
        if not actual or actual in {ia_local.URL_COMFYUI, ia_local.URL_FORGE}:
            self.txt_url_local.setText(predeterminada)
        self.txt_url_local.setPlaceholderText(predeterminada)
        self.btn_workflow_local.setEnabled(backend == "comfyui")
        self._guardar_config()

    def _elegir_workflow_local(self) -> None:
        ruta, _ = QFileDialog.getOpenFileName(
            self, "Workflow de ComfyUI en formato API", "", "JSON (*.json)"
        )
        if ruta:
            self.txt_workflow_local.setText(ruta)
            self._guardar_config()

    def _detectar_local(self) -> None:
        self.btn_detectar_local.setEnabled(False)
        self.lbl_local_estado.setText(
            "Buscando ComfyUI (8188) y Forge/A1111 (7860) en este equipo…"
        )
        self._lanzar_tarea(
            ia_local.detectar_servidores, self._local_detectado, self._local_fallo
        )

    def _local_detectado(self, servidores) -> None:
        self.btn_detectar_local.setEnabled(True)
        encontrados = list(servidores or [])
        if not encontrados:
            self.lbl_local_estado.setText(
                "No encontré ningún programa local encendido. Abre ComfyUI o "
                "Forge (con --api) y pulsa otra vez «Buscar programa». "
                "Las instrucciones están en «📖 Guía»."
            )
            return
        estado = encontrados[0]
        self._cargando = True
        try:
            self.cbo_backend_local.setCurrentIndex(
                max(0, self.cbo_backend_local.findData(estado["backend"]))
            )
            self.txt_url_local.setText(str(estado["url"]))
            self.cbo_modelo_local.clear()
            for modelo in estado.get("modelos") or []:
                self.cbo_modelo_local.addItem(modelo, modelo)
            if self.cbo_modelo_local.count():
                self.cbo_modelo_local.setCurrentIndex(0)
        finally:
            self._cargando = False
        self._guardar_config()
        extra = ""
        if len(encontrados) > 1:
            otros = ", ".join(
                ia_local.BACKENDS.get(e["backend"], e["backend"])
                for e in encontrados[1:]
            )
            extra = f" También hay {otros} disponible."
        self.lbl_local_estado.setText(
            f"✓ {estado['mensaje']}.{extra}"
            + (
                f" {len(estado.get('modelos') or [])} modelo(s) disponibles."
                if estado.get("modelos") else ""
            )
        )

    def _local_fallo(self, mensaje: str) -> None:
        self.btn_detectar_local.setEnabled(True)
        self.lbl_local_estado.setText(f"No se pudo buscar: {mensaje}")

    def _probar_local(self) -> None:
        backend = str(self.cbo_backend_local.currentData() or "comfyui")
        url = self.txt_url_local.text().strip()
        self.lbl_local_estado.setText(
            "Generando una imagen de prueba; la primera vez tarda más porque "
            "el modelo se carga en la memoria de la tarjeta…"
        )
        self.btn_probar_local.setEnabled(False)
        self._lanzar_tarea(
            lambda: ia_local.probar_local(backend, url),
            self._local_probado,
            self._local_fallo,
        )

    def _local_probado(self, mensaje: str) -> None:
        self.btn_probar_local.setEnabled(True)
        self.lbl_local_estado.setText(f"✓ {mensaje}")

    def _mostrar_guia_local(self) -> None:
        """Ventana con la guía de instalación y los enlaces de descarga."""
        equipo = getattr(self, "_equipo", None)
        estado = (
            equipo.resumen() + "\n" + (equipo.consejo or "")
            if equipo is not None
            else "Equipo sin revisar"
        )
        caja = QDialog(self)
        caja.setWindowTitle("IA local · instalar y conectar")
        caja.resize(720, 660)
        lay = QVBoxLayout(caja)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        titulo = QLabel("IA LOCAL EN TU PC · COMFYUI + SDXL-TURBO")
        titulo.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #38bdf8; letter-spacing: 1px;"
        )
        lay.addWidget(titulo)
        lbl_eq = QLabel(estado)
        lbl_eq.setWordWrap(True)
        lbl_eq.setObjectName("HintLabel")
        lay.addWidget(lbl_eq)

        texto = QPlainTextEdit(_GUIA_LOCAL)
        texto.setReadOnly(True)
        texto.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        texto.setStyleSheet(
            "QPlainTextEdit { background: #0f131c; border: 1px solid #232838; "
            "border-radius: 6px; padding: 8px; color: #cbd5e1; font-size: 12px; }"
        )
        lay.addWidget(texto, 1)

        fila = QHBoxLayout()
        fila.setSpacing(6)
        b_comfy = QPushButton("⬇ ComfyUI portable")
        b_comfy.setToolTip("Abre la página oficial de descargas de ComfyUI")
        b_comfy.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(URL_COMFYUI_DESCARGA))
        )
        b_modelo = QPushButton("⬇ SDXL-Turbo (7 GB)")
        b_modelo.setToolTip(
            "Descarga sd_xl_turbo_1.0_fp16.safetensors desde Hugging Face"
        )
        b_modelo.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(URL_MODELO_TURBO)))
        b_carpeta = QPushButton("📂 Carpeta de modelos…")
        b_carpeta.setToolTip(
            "Elige tu carpeta ComfyUI\\models\\checkpoints para abrirla y "
            "arrastrar el modelo ahí"
        )
        b_carpeta.clicked.connect(self._abrir_carpeta_modelos)
        b_buscar = QPushButton("🔍 Buscar programa")
        b_buscar.clicked.connect(self._detectar_local)
        b_cerrar = QPushButton("Cerrar")
        b_cerrar.clicked.connect(caja.accept)
        for boton in (b_comfy, b_modelo, b_carpeta, b_buscar):
            fila.addWidget(boton)
        fila.addStretch(1)
        fila.addWidget(b_cerrar)
        lay.addLayout(fila)

        caja.exec()

    def _abrir_carpeta_modelos(self) -> None:
        """Abre en el explorador la carpeta de checkpoints del usuario."""
        carpeta = QFileDialog.getExistingDirectory(
            self,
            "Elige la carpeta ComfyUI\\models\\checkpoints",
            str(Path.home()),
        )
        if carpeta:
            QDesktopServices.openUrl(QUrl.fromLocalFile(carpeta))

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

    def _guardar_token_useapi(self) -> None:
        token = self.txt_token_useapi.text().strip()
        if not token:
            QMessageBox.information(self, "Sin token", "Pega tu token de UseAPI y pulsa Guardar token.")
            return
        try:
            credenciales.guardar_clave("useapi", token)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "No se pudo guardar", str(exc))
            return
        self.txt_token_useapi.clear()
        self._actualizar_estado_useapi()
        self.lbl_estado.setText("Token de UseAPI guardado cifrado en este equipo.")

    def _borrar_token_useapi(self) -> None:
        credenciales.borrar_clave("useapi")
        self._actualizar_estado_useapi()

    def _actualizar_estado_useapi(self) -> None:
        pista = credenciales.pista_clave("useapi")
        self.lbl_useapi.setText(
            f"{'✓ Token guardado cifrado (' + pista + ').' if pista else 'Sin token guardado.'} "
            "Google Flow vía UseAPI es un servicio de terceros y requiere configurar tu cuenta Flow en UseAPI."
        )

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
            "useapi": credenciales.obtener_clave("useapi"),
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

    def _generar_paquete_solo(self) -> None:
        cfg = self.config_actual()
        cfg.generar_imagenes = False
        cfg.generar_voz = False
        self._arrancar(cfg, solo_guion=False, solo_paquete=True)

    def _arrancar(
        self, cfg: ConfigIA, *, solo_guion: bool, solo_paquete: bool = False
    ) -> None:
        idea = self.txt_idea.toPlainText().strip()
        if solo_paquete:
            texto = self._ventana.txt_script.toPlainText().strip()
            if texto:
                from core.ia.guion import parsear_guion
                self._ultimo_guion = parsear_guion(texto)
                self._ultimo_guion.idea = idea
            if not self._ultimo_guion or not self._ultimo_guion.bloques:
                QMessageBox.information(
                    self, "Falta el guion",
                    "Genera un guion o pégalo en «Texto → voz» antes de preparar la publicación.",
                )
                return
        if not idea and solo_paquete and self._ultimo_guion:
            idea = self._ultimo_guion.idea or next(
                (b.titulo for b in self._ultimo_guion.capitulos if b.titulo),
                "Tema del guion",
            )
        if solo_paquete:
            self._ultimo_guion.idea = idea
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
        if cfg.proveedor_imagen == "useapi" and not claves["useapi"]:
            QMessageBox.information(
                self, "Falta token UseAPI",
                "Guarda tu token de UseAPI en el panel de imágenes de IA antes de generar con Google Flow."
            )
            return

        cfg.generar_paquete = not solo_guion
        cfg.generar_miniatura = cfg.generar_miniatura and not solo_guion
        if solo_paquete:
            cfg.generar_imagenes = False
            cfg.generar_voz = False
        self._guardar_config()
        self.txt_log.clear()
        for nota in notas:
            self._log(nota)
        self.barra.setValue(0)
        if not solo_paquete:
            self._ultimo_guion = None

        carpeta = Path(__file__).resolve().parents[2] / "ui" / ".config" / "imagenes_generadas"

        trabajador = TrabajadorIA(
            idea,
            cfg,
            claves,
            str(carpeta),
            self,
            guion_previo=self._ultimo_guion if solo_paquete else None,
        )
        trabajador.progreso.connect(self._progreso)
        trabajador.log.connect(self._log)
        trabajador.guion.connect(self._guion_listo)
        trabajador.paquete.connect(self._paquete_listo)
        trabajador.imagenes.connect(self._imagenes_listas)
        trabajador.voz.connect(self._voz_lista)
        trabajador.listo.connect(self._terminado)
        trabajador.fallo.connect(self._fallo)
        self._trabajador_ia = trabajador

        self.btn_todo.setEnabled(False)
        self.btn_guion.setEnabled(False)
        self.btn_paquete.setEnabled(False)
        self.btn_cancelar.setEnabled(True)
        self.lbl_estado.setText(
            "Generando el guion…" if solo_guion else
            "Preparando la publicación…" if solo_paquete else
            "Generando guion, imágenes y voz…"
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

    def _mostrar_paquete(self, paquete: dict) -> None:
        if paquete is None:
            paquete = getattr(self, "_ultimo_paquete", {})
        self._ultimo_paquete = dict(paquete or {})
        if not hasattr(self, "txt_prompt_miniatura"):
            return
        if not self._ultimo_paquete:
            self._cargando = True
            try:
                self.cbo_titulos_ia.clear()
                self.txt_prompt_miniatura.clear()
                self.lista_prompts_ia.clear()
                self.txt_prompt_escena.clear()
                self.lbl_vista_miniatura.setPixmap(QPixmap())
                self.lbl_vista_miniatura.setText("Sin miniatura generada")
            finally:
                self._cargando = False
            return
        self._cargando = True
        try:
            titulo_actual = self._ventana.txt_titulo.text().strip()
            self.cbo_titulos_ia.clear()
            for titulo in self._ultimo_paquete.get("titulos") or []:
                self.cbo_titulos_ia.addItem(str(titulo))
            prompt_miniatura = str(self._ultimo_paquete.get("prompt_miniatura") or "")
            self.txt_prompt_miniatura.setPlainText(prompt_miniatura)
            ruta_miniatura = str(self._ultimo_paquete.get("miniatura_ruta") or "")
            self.lbl_vista_miniatura.setPixmap(QPixmap())
            self.lbl_vista_miniatura.setText(ruta_miniatura or "Sin miniatura generada")
            titulo_actual = (
                titulo_actual
                or str(self._ultimo_paquete.get("titulo") or "")
                or str((self._ultimo_paquete.get("titulos") or [""])[0])
            )
            if titulo_actual:
                self.cbo_titulos_ia.setEditText(titulo_actual)
            if ruta_miniatura and Path(ruta_miniatura).exists():
                pixmap = QPixmap(ruta_miniatura)
                if not pixmap.isNull():
                    self.lbl_vista_miniatura.setPixmap(
                        pixmap.scaled(220, 124, Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
                    )
                    self.lbl_vista_miniatura.setToolTip(ruta_miniatura)
        finally:
            self._cargando = False
        self._refrescar_lista_prompts()

    def _guardar_paquete_editado(self) -> None:
        if self._cargando or not self._ultimo_paquete:
            return
        self._ultimo_paquete["prompt_miniatura"] = self.txt_prompt_miniatura.toPlainText().strip()
        titulo = self.cbo_titulos_ia.currentText().strip()
        if titulo and titulo not in (self._ultimo_paquete.get("titulos") or []):
            self._ultimo_paquete["titulos"] = list(self._ultimo_paquete.get("titulos") or []) + [titulo]
        if titulo:
            self._ultimo_paquete["titulo"] = titulo
        prompts = self._ultimo_paquete.get("prompts_imagenes") or []
        indice = self.lista_prompts_ia.currentIndex()
        if 0 <= indice < len(prompts):
            prompts[indice]["prompt"] = self.txt_prompt_escena.toPlainText().strip()
        self._ultimo_paquete["prompts_imagenes"] = prompts
        self._ventana._ia_paquete_editorial = dict(self._ultimo_paquete)
        self._ventana._ia_config = self.config_actual()
        self._ventana._ia_config.paquete_editorial = dict(self._ultimo_paquete)
        self._ventana._marcar_proyecto_modificado()
        self._ventana._guardar_sesion()

    def _seleccionar_titulo_ia(self, titulo: str) -> None:
        titulo = titulo.strip()
        if not self._cargando and titulo:
            self._ultimo_paquete["titulo"] = titulo
            self._ventana.txt_titulo.setText(titulo)
            self._ventana._ia_paquete_editorial = dict(self._ultimo_paquete)
            self._ventana._ia_config = self.config_actual()
            self._ventana._ia_config.paquete_editorial = dict(self._ultimo_paquete)
            self._ventana._marcar_proyecto_modificado()
            self._ventana._guardar_sesion()

    def _cargar_prompt_seccion(self, *_args) -> None:
        paquete = self._ultimo_paquete
        prompts = paquete.get("prompts_imagenes") or []
        indice = self.lista_prompts_ia.currentIndex()
        self.txt_prompt_escena.blockSignals(True)
        self.txt_prompt_escena.setPlainText(
            str(prompts[indice].get("prompt") or "")
            if 0 <= indice < len(prompts) and isinstance(prompts[indice], dict)
            else ""
        )
        self.txt_prompt_escena.blockSignals(False)

    def _regenerar_miniatura(self) -> None:
        cfg = self.config_actual()
        prompt = self.txt_prompt_miniatura.toPlainText().strip()
        if not prompt:
            from core.ia.pipeline import prompt_de_miniatura

            prompt = prompt_de_miniatura(
                self._ultimo_paquete, cfg, self.txt_idea.toPlainText().strip()
            )
            if prompt:
                self.txt_prompt_miniatura.setPlainText(prompt)
        if not prompt:
            QMessageBox.information(self, "Falta el prompt", "Escribe el prompt visual de la miniatura.")
            return
        proveedor = cfg.proveedor_imagen
        clave = self._claves().get(proveedor, "")
        if proveedor == "ninguno":
            QMessageBox.information(
                self, "Generación desactivada",
                "Selecciona un proveedor de imágenes para generar la miniatura.",
            )
            return
        proveedor = "openai" if proveedor == "compatible" else proveedor
        if proveedor == "useapi" and not clave:
            QMessageBox.information(self, "Falta token UseAPI", "Guarda el token de UseAPI en el panel de imágenes.")
            return
        if proveedor == "openai" and not clave:
            QMessageBox.information(self, "Falta la clave", "Guarda una clave de OpenAI para generar imágenes.")
            return
        if proveedor == "openrouter" and not clave:
            QMessageBox.information(self, "Falta la clave", "Guarda una clave de OpenRouter para generar imágenes.")
            return
        if proveedor == "local":
            equipo = getattr(self, "_equipo", None)
            if equipo is not None and not equipo.cumple_requisitos:
                QMessageBox.information(
                    self, "La IA local no cumple los requisitos", equipo.motivo,
                )
                return
        self.btn_generar_miniatura.setEnabled(False)
        self.lbl_estado.setText("Generando miniatura 16:9…")
        carpeta = Path(__file__).resolve().parents[2] / "ui" / ".config" / "imagenes_generadas"
        def generar():
            from core.ia.proveedores import generar_imagen_bytes
            from core.ia.pipeline import _extension_imagen
            import time
            carpeta.mkdir(parents=True, exist_ok=True)
            from core.ia.pipeline import _kw_local

            datos = generar_imagen_bytes(
                prompt, proveedor=proveedor, clave=clave,
                modelo=(cfg.modelo_local if proveedor == "local" else cfg.modelo_imagen),
                cuenta_flow=cfg.cuenta_flow,
                ancho=1280, alto=720, semilla=int(time.time()) % 999999,
                **_kw_local(cfg),
            )
            destino = carpeta / f"miniatura_{time.strftime('%Y%m%d_%H%M%S')}{_extension_imagen(datos)}"
            destino.write_bytes(datos)
            return str(destino)
        self._lanzar_tarea(generar, self._miniatura_lista, self._miniatura_fallo)

    def _miniatura_lista(self, ruta: str) -> None:
        self.btn_generar_miniatura.setEnabled(True)
        self._ultimo_paquete["prompt_miniatura"] = self.txt_prompt_miniatura.toPlainText().strip()
        self._ultimo_paquete["miniatura_ruta"] = ruta
        self._ventana._ia_aplicar_paquete(self._ultimo_paquete, elegir_titulo=False)
        self._mostrar_paquete(self._ultimo_paquete)
        self.lbl_estado.setText("Miniatura generada; revísala en el panel YouTube.")

    def _miniatura_fallo(self, mensaje: str) -> None:
        self.btn_generar_miniatura.setEnabled(True)
        self.lbl_estado.setText("No se pudo generar la miniatura.")
        self._log(f"[YouTube] Error al generar miniatura: {mensaje}")

    def _regenerar_escena(self) -> None:
        prompt = self.txt_prompt_escena.toPlainText().strip()
        if not prompt:
            QMessageBox.information(self, "Falta el prompt", "Escribe el prompt de esta escena.")
            return
        indice = self.lista_prompts_ia.currentIndex()
        prompts = self._ultimo_paquete.get("prompts_imagenes") or []
        if indice < 0 or indice >= len(prompts):
            return
        bloques = ([b for b in self._ultimo_guion.bloques if b.texto.strip()]
                   if self._ultimo_guion else [])
        bloque = bloques[indice] if indice < len(bloques) else None
        if bloque is None:
            return
        self._guardar_paquete_editado()
        cfg = self.config_actual()
        proveedor = cfg.proveedor_imagen
        clave = self._claves().get(proveedor, "")
        if proveedor == "ninguno":
            QMessageBox.information(
                self, "Generación desactivada",
                "Selecciona un proveedor de imágenes para generar la miniatura.",
            )
            return
        proveedor = "openai" if proveedor == "compatible" else proveedor
        if proveedor == "useapi" and not clave:
            QMessageBox.information(self, "Falta token UseAPI", "Guarda el token de UseAPI en el panel de imágenes.")
            return
        if proveedor == "openai" and not clave:
            QMessageBox.information(self, "Falta la clave", "Guarda una clave de OpenAI para generar imágenes.")
            return
        if proveedor == "openrouter" and not clave:
            QMessageBox.information(self, "Falta la clave", "Guarda una clave de OpenRouter para generar imágenes.")
            return
        self.btn_generar_escena.setEnabled(False)
        def generar():
            from core.ia.imagenes import _extension, _nombre_archivo
            from core.ia.proveedores import generar_imagen_bytes
            import time
            datos = generar_imagen_bytes(
                prompt, proveedor=proveedor, clave=clave, modelo=cfg.modelo_imagen,
                cuenta_flow=cfg.cuenta_flow,
                ancho=1920, alto=1080, semilla=int(time.time()) % 999999,
            )
            carpeta = Path(__file__).resolve().parents[2] / "ui" / ".config" / "imagenes_generadas"
            carpeta.mkdir(parents=True, exist_ok=True)
            ruta = carpeta / (_nombre_archivo(bloque, time.strftime('%Y%m%d_%H%M%S')) + _extension(datos))
            ruta.write_bytes(datos)
            return str(ruta)
        self._lanzar_tarea(
            generar,
            lambda ruta: self._escena_lista(ruta),
            lambda error: (self.btn_generar_escena.setEnabled(True), self._miniatura_fallo(error)),
        )

    def _escena_lista(self, ruta: str) -> None:
        self.btn_generar_escena.setEnabled(True)
        self._ventana._ia_aplicar_imagenes([ruta])
        self._log("[Imágenes] Escena regenerada con el prompt editado.")

    def _paquete_listo(self, paquete: dict) -> None:
        self._mostrar_paquete(paquete)
        titulos = self._ultimo_paquete.get("titulos") or []
        descripcion = self._ultimo_paquete.get("descripcion") or ""
        etiquetas = self._ultimo_paquete.get("etiquetas") or []
        self._ventana._ia_aplicar_paquete(
            self._ultimo_paquete,
            elegir_titulo=bool(titulos),
        )
        if titulos:
            self.cbo_titulos_ia.setCurrentIndex(0)
        self._guardar_paquete_editado()
        self._log(
            "[YouTube] Título, descripción, etiquetas y prompts preparados; "
            "revisa fuentes y datos antes de publicar."
        )
        if self._ultimo_paquete.get("miniatura_ruta"):
            self._log("[YouTube] Miniatura generada y enlazada al panel de publicación.")

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
        self.btn_paquete.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.barra.setValue(100)
        self.lbl_estado.setText("✓ Proceso completado.")
        self._log("[IA] Proceso completado.")

    def _fallo(self, mensaje: str) -> None:
        self._trabajador_ia = None
        self.btn_todo.setEnabled(True)
        self.btn_guion.setEnabled(True)
        self.btn_paquete.setEnabled(True)
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
