"""Monitor de Previsualización y Motor de Reproducción en Vivo en tiempo real.

Permite previsualizar el video mientras se edita, con:
- Reproducción de audio en tiempo real sincronizado (QMediaPlayer + QAudioOutput)
- Composición de fotogramas en vivo con zoom Ken Burns dinámico
- Previsualización en vivo de transiciones de desvanecido (crossfade)
- Visualizador de audio cuando solo hay pistas de sonido
- Timecode digital interactivo y barra de transporte
"""
from __future__ import annotations

import math
from pathlib import Path
from PyQt6.QtCore import QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (
    QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap
)
from PyQt6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget
)

from core.effects import ConfigEfecto, transformacion
from core.subtitles import EstiloSubtitulos, cue_activo, envolver_texto

try:
    from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
    MULTIMEDIA_DISPONIBLE = True
except ImportError:
    MULTIMEDIA_DISPONIBLE = False


ANCHO_MAX_PREVIEW = 1600
LIMITE_CACHE_IMAGENES = 120


def _pixmap_preview(ruta: str) -> QPixmap | None:
    """Carga la imagen reducida para el monitor.

    El render final usa siempre el archivo original; la previsualización no
    necesita 4K y así se evitan cientos de MB de RAM con proyectos grandes.
    """
    pix = QPixmap(ruta)
    if pix.isNull():
        return None
    if pix.width() > ANCHO_MAX_PREVIEW:
        pix = pix.scaledToWidth(
            ANCHO_MAX_PREVIEW, Qt.TransformationMode.SmoothTransformation
        )
    return pix


class MonitorWidget(QWidget):
    solicitar_tiempo = pyqtSignal(float)
    probar_estilo_click = pyqtSignal()
    ver_video_click = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(380, 270)

        self._pixmap_frame: QPixmap | None = None
        self._tiempo_actual: float = 0.0
        self._duracion_total: float = 60.0
        self._capitulo_actual: str = "Intro"
        self._resolucion_texto: str = "1080p (1920×1080)"
        self._reproduciendo = False

        # Datos de segmentos para composición en vivo
        self._segmentos: list[dict] = []  # [{"inicio": f, "fin": f, "imagen": str, "titulo": str}]
        self._todas_las_imagenes: list[str] = []
        self._ruta_imagen_actual: str = ""
        self._duracion_transicion: float = 1.0
        self._ken_burns_factor: float = 0.14
        self._efecto_global: ConfigEfecto = ConfigEfecto()
        self._ruta_audio_actual: str = ""

        # Subtítulos en vivo
        self._cues: list[dict] = []
        self._estilo_sub: EstiloSubtitulos = EstiloSubtitulos()
        self._mostrar_subtitulos: bool = True

        # Cache de pixmaps cargados para rendimiento fluido
        self._cache_pixmaps: dict[str, QPixmap] = {}

        # Motor de audio con QMediaPlayer
        self._reproductor_audio: QMediaPlayer | None = None
        self._salida_audio: QAudioOutput | None = None
        self._iniciar_motor_audio()

        # Timer de 30 FPS para avance y animación fluida
        self._timer_render = QTimer(self)
        self._timer_render.setInterval(33)  # ~30 FPS
        self._timer_render.timeout.connect(self._tick_render)

        self._construir_ui()

    def _iniciar_motor_audio(self) -> None:
        if MULTIMEDIA_DISPONIBLE:
            try:
                self._reproductor_audio = QMediaPlayer(self)
                self._salida_audio = QAudioOutput(self)
                self._salida_audio.setVolume(1.0)
                self._reproductor_audio.setAudioOutput(self._salida_audio)
                self._reproductor_audio.positionChanged.connect(self._al_cambiar_posicion_audio)
            except Exception:
                self._reproductor_audio = None
                self._salida_audio = None

    def _construir_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # Barra superior del monitor
        barra_sup = QHBoxLayout()
        self.lbl_titulo_monitor = QLabel("LIVE PROGRAM MONITOR")
        self.lbl_titulo_monitor.setStyleSheet(
            "color: #94a3b8; font-size: 11px; font-weight: bold; letter-spacing: 1px;"
        )
        self.lbl_badge_live = QLabel("● EN VIVO")
        self.lbl_badge_live.setStyleSheet(
            "background: #14532d; color: #4ade80; border: 1px solid #16a34a; "
            "border-radius: 3px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
        )
        self.lbl_badge_res = QLabel("1080p")
        self.lbl_badge_res.setStyleSheet(
            "background: #1e293b; color: #38bdf8; border: 1px solid #0284c7; "
            "border-radius: 3px; padding: 2px 6px; font-size: 10px; font-weight: bold;"
        )
        barra_sup.addWidget(self.lbl_titulo_monitor)
        barra_sup.addWidget(self.lbl_badge_live)
        barra_sup.addStretch()
        barra_sup.addWidget(self.lbl_badge_res)
        layout.addLayout(barra_sup)

        # Viewport de pantalla (cinema canvas con compositor en vivo)
        self.pantalla = PantallaCanvas(self)
        layout.addWidget(self.pantalla, 1)

        # Barra de transporte, volumen y timecode
        barra_transporte = QHBoxLayout()
        barra_transporte.setSpacing(6)

        self.lbl_timecode = QLabel("00:00:00.00")
        self.lbl_timecode.setStyleSheet(
            "font-family: Consolas, 'Courier New', monospace; font-size: 13px; "
            "font-weight: bold; color: #4ade80; background: #0f172a; "
            "padding: 3px 8px; border-radius: 4px; border: 1px solid #1e293b;"
        )
        barra_transporte.addWidget(self.lbl_timecode)

        self.btn_inicio = QPushButton("⏮")
        self.btn_inicio.setToolTip("Ir al inicio")
        self.btn_inicio.setFixedSize(30, 26)
        self.btn_inicio.clicked.connect(self._ir_inicio)
        barra_transporte.addWidget(self.btn_inicio)

        self.btn_play = QPushButton("▶")
        self.btn_play.setToolTip("Reproducir / Pausar video y audio en tiempo real [Barra espaciadora]")
        self.btn_play.setFixedSize(38, 26)
        self.btn_play.setStyleSheet(
            "QPushButton { background: #2563eb; color: white; font-weight: bold; font-size: 13px; } "
            "QPushButton:hover { background: #3b82f6; }"
        )
        self.btn_play.clicked.connect(self.toggle_play)
        barra_transporte.addWidget(self.btn_play)

        self.btn_fin = QPushButton("⏭")
        self.btn_fin.setToolTip("Ir al final")
        self.btn_fin.setFixedSize(30, 26)
        self.btn_fin.clicked.connect(self._ir_fin)
        barra_transporte.addWidget(self.btn_fin)

        # Control de volumen
        self.btn_mute = QPushButton("🔊")
        self.btn_mute.setFixedSize(28, 26)
        self.btn_mute.setToolTip("Silenciar / Activar sonido")
        self.btn_mute.clicked.connect(self._toggle_mute)
        barra_transporte.addWidget(self.btn_mute)

        self.slider_volumen = QSlider(Qt.Orientation.Horizontal)
        self.slider_volumen.setRange(0, 100)
        self.slider_volumen.setValue(100)
        self.slider_volumen.setFixedWidth(65)
        self.slider_volumen.setToolTip("Volumen de previsualización")
        self.slider_volumen.valueChanged.connect(self._cambiar_volumen)
        barra_transporte.addWidget(self.slider_volumen)

        barra_transporte.addStretch()

        self.btn_quick_prev = QPushButton("⚡ Probar estilo")
        self.btn_quick_prev.setToolTip("Renderiza rápidamente un clip de prueba con transiciones")
        self.btn_quick_prev.setStyleSheet("font-size: 11px; padding: 4px 8px;")
        self.btn_quick_prev.clicked.connect(self.probar_estilo_click.emit)
        barra_transporte.addWidget(self.btn_quick_prev)

        self.btn_ver = QPushButton("▶ Ver video")
        self.btn_ver.setToolTip("Abre el último video renderizado en el reproductor de Windows")
        self.btn_ver.setStyleSheet("font-size: 11px; padding: 4px 8px;")
        self.btn_ver.setEnabled(False)
        self.btn_ver.clicked.connect(self.ver_video_click.emit)
        barra_transporte.addWidget(self.btn_ver)

        layout.addLayout(barra_transporte)

    # ------------------------------------------------------------- Configuración en Vivo
    def cache_imagen(self, ruta: str) -> QPixmap | None:
        """Devuelve la imagen de previsualización cacheada (escalada)."""
        if not ruta:
            return None
        existente = self._cache_pixmaps.get(ruta)
        if existente is not None:
            return existente
        if not Path(ruta).exists():
            return None
        pix = _pixmap_preview(ruta)
        if pix is None:
            return None
        if len(self._cache_pixmaps) >= LIMITE_CACHE_IMAGENES:
            self._cache_pixmaps.clear()
        self._cache_pixmaps[ruta] = pix
        return pix

    def set_todas_las_imagenes(self, rutas: list[str]) -> None:
        self._todas_las_imagenes = list(rutas)
        for r in rutas:
            self.cache_imagen(r)
        if self._todas_las_imagenes and not self._ruta_imagen_actual:
            self._ruta_imagen_actual = self._todas_las_imagenes[0]
        self.pantalla.update()

    def set_datos_en_vivo(self, segmentos: list[dict], dur_total: float,
                          dur_transicion: float = 1.0, ken_burns: float = 0.14,
                          ruta_audio: str = "",
                          efecto: ConfigEfecto | None = None) -> None:
        """Actualiza los datos del proyecto para la previsualización en tiempo real."""
        self._segmentos = list(segmentos)
        self._duracion_total = max(1.0, float(dur_total))
        self._duracion_transicion = max(0.2, float(dur_transicion))
        self._ken_burns_factor = float(ken_burns)
        if efecto is not None:
            self._efecto_global = efecto

        # Pre-cargar imágenes en cache
        for s in self._segmentos:
            self.cache_imagen(s.get("imagen", ""))

        # Actualizar audio en QMediaPlayer
        if ruta_audio and Path(ruta_audio).exists():
            if ruta_audio != self._ruta_audio_actual:
                self._ruta_audio_actual = ruta_audio
                if self._reproductor_audio:
                    self._reproductor_audio.setSource(QUrl.fromLocalFile(ruta_audio))
        else:
            self._ruta_audio_actual = ""

        self.pantalla.update()

    def set_subtitulos(self, cues: list[dict], estilo: EstiloSubtitulos | dict | None = None,
                       mostrar: bool = True) -> None:
        """Actualiza los subtítulos que se dibujan sobre el monitor."""
        self._cues = list(cues or [])
        if isinstance(estilo, dict):
            self._estilo_sub = EstiloSubtitulos.desde_dict(estilo)
        elif isinstance(estilo, EstiloSubtitulos):
            self._estilo_sub = estilo
        self._mostrar_subtitulos = bool(mostrar)
        self.pantalla.update()

    def set_frame(self, pixmap: QPixmap | None) -> None:
        self._pixmap_frame = pixmap
        self.pantalla.update()

    def set_imagen_actual(self, ruta: str, capitulo: str = "") -> None:
        self._ruta_imagen_actual = ruta
        if capitulo:
            self._capitulo_actual = capitulo
        self.cache_imagen(ruta)
        self.pantalla.update()

    def set_tiempo(self, seg: float, dur_total: float | None = None) -> None:
        self._tiempo_actual = float(seg)
        if dur_total:
            self._duracion_total = float(dur_total)

        mins = int(self._tiempo_actual // 60)
        segs = int(self._tiempo_actual % 60)
        frac = int((self._tiempo_actual - int(self._tiempo_actual)) * 100)
        self.lbl_timecode.setText(f"{mins:02d}:{segs:02d}.{frac:02d}")

        if not self._reproduciendo and self._reproductor_audio and self._reproductor_audio.isSeekable():
            self._reproductor_audio.setPosition(int(self._tiempo_actual * 1000))

        self.pantalla.update()

    def set_resolucion(self, texto: str) -> None:
        self._resolucion_texto = texto
        tag = "1080p"
        if "720p" in texto:
            tag = "720p"
        elif "vertical" in texto.lower() or "shorts" in texto.lower():
            tag = "Shorts 9:16"
        self.lbl_badge_res.setText(tag)
        self.pantalla.update()

    # ------------------------------------------------------------- Motor Playback
    def toggle_play(self) -> None:
        self._reproduciendo = not self._reproduciendo
        if self._reproduciendo:
            self.btn_play.setText("⏸")
            if self._tiempo_actual >= self._duracion_total - 0.2:
                self.set_tiempo(0.0)

            # Iniciar audio si existe
            if self._reproductor_audio and self._ruta_audio_actual and Path(self._ruta_audio_actual).exists():
                self._reproductor_audio.setPosition(int(self._tiempo_actual * 1000))
                self._reproductor_audio.play()

            self._timer_render.start()
        else:
            self.btn_play.setText("▶")
            if self._reproductor_audio:
                self._reproductor_audio.pause()
            self._timer_render.stop()

    def _tick_render(self) -> None:
        """Avanza el tiempo cuando no hay audio con posición activa o a ~30 FPS."""
        tiene_audio_activo = (
            self._reproductor_audio is not None
            and self._reproductor_audio.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        )
        if not tiene_audio_activo:
            nuevo_t = self._tiempo_actual + 0.033
            if nuevo_t >= self._duracion_total:
                nuevo_t = 0.0
                self.toggle_play()
                return
            self.set_tiempo(nuevo_t)
            self.solicitar_tiempo.emit(nuevo_t)
        else:
            # Mientras el audio corre, solicitar repintado continuo a 30 FPS para Ken Burns fluido
            self.pantalla.update()

    def _al_cambiar_posicion_audio(self, ms: int) -> None:
        if self._reproduciendo:
            t = ms / 1000.0
            if t >= self._duracion_total:
                self.toggle_play()
                return
            self.set_tiempo(t)
            self.solicitar_tiempo.emit(t)

    def _ir_inicio(self) -> None:
        self.set_tiempo(0.0)
        self.solicitar_tiempo.emit(0.0)

    def _ir_fin(self) -> None:
        self.set_tiempo(self._duracion_total)
        self.solicitar_tiempo.emit(self._duracion_total)

    def _toggle_mute(self) -> None:
        if self._salida_audio:
            muteado = not self._salida_audio.isMuted()
            self._salida_audio.setMuted(muteado)
            self.btn_mute.setText("🔇" if muteado else "🔊")

    def _cambiar_volumen(self, val: int) -> None:
        if self._salida_audio:
            self._salida_audio.setVolume(val / 100.0)


class PantallaCanvas(QFrame):
    """Lienzo con motor de composición en tiempo real: Ken Burns y Crossfade."""

    def __init__(self, monitor: MonitorWidget) -> None:
        super().__init__(monitor)
        self._monitor = monitor
        self.setStyleSheet("background: #07080c; border: 1px solid #1e2433; border-radius: 6px;")

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        ancho_w = self.width()
        alto_w = self.height()

        p.fillRect(0, 0, ancho_w, alto_w, QColor("#07080c"))

        # Determinar relación de aspecto
        es_vertical = "shorts" in self._monitor._resolucion_texto.lower() or "vertical" in self._monitor._resolucion_texto.lower()
        aspect_ratio = (9.0 / 16.0) if es_vertical else (16.0 / 9.0)

        # Marco centrado preservando proporción cinema
        w_obj = ancho_w - 16
        h_obj = w_obj / aspect_ratio
        if h_obj > alto_w - 16:
            h_obj = alto_w - 16
            w_obj = h_obj * aspect_ratio

        x_marco = (ancho_w - w_obj) / 2.0
        y_marco = (alto_w - h_obj) / 2.0
        rect_pantalla = QRectF(x_marco, y_marco, w_obj, h_obj)

        # Fondo negro de cine
        p.fillRect(rect_pantalla, QColor("#000000"))

        # ------------------- MOTOR DE COMPOSICIÓN EN VIVO -------------------
        dibujado = False
        t_actual = self._monitor._tiempo_actual
        segmentos = self._monitor._segmentos

        pix_act = None
        zoom, px, py = 1.0, 0.5, 0.5
        idx_activo = 0
        t_fin = self._monitor._duracion_total

        if segmentos:
            # Encontrar el segmento activo en este segundo
            for i, seg in enumerate(segmentos):
                if t_actual >= seg["inicio"]:
                    idx_activo = i

            seg_act = segmentos[idx_activo]
            self._monitor._capitulo_actual = seg_act.get("titulo", f"Capítulo {idx_activo+1}")

            t_ini = seg_act["inicio"]
            t_fin = seg_act["fin"]
            dur_seg = max(0.2, t_fin - t_ini)
            prog = min(1.0, max(0.0, (t_actual - t_ini) / dur_seg))

            efecto = ConfigEfecto.desde_dict(seg_act.get("efecto")) \
                if seg_act.get("efecto") else self._monitor._efecto_global
            zoom, px, py = transformacion(efecto, prog, dur_seg, idx_activo)

            ruta_act = seg_act.get("imagen", "")
            pix_act = self._obtener_pixmap(ruta_act)

        # Fallback 1: Si no hay segmentos en el video pero hay una imagen seleccionada
        if not pix_act and not segmentos and self._monitor._ruta_imagen_actual:
            pix_act = self._obtener_pixmap(self._monitor._ruta_imagen_actual)

        # Fallback 2: Probar cualquier imagen del proyecto
        if not pix_act and self._monitor._todas_las_imagenes:
            for r in self._monitor._todas_las_imagenes:
                pix_act = self._obtener_pixmap(r)
                if pix_act:
                    break

        # Fallback 3: Probar el cache de pixmaps
        if not pix_act and self._monitor._cache_pixmaps:
            pix_act = next(iter(self._monitor._cache_pixmaps.values()), None)

        # DIBUJADO DE IMAGEN CON EFECTO Y CROSSFADE
        if pix_act:
            self._dibujar_imagen_transformada(
                p, pix_act, rect_pantalla, zoom, px, py, opacidad=1.0)
            dibujado = True

            # Previsualización de transición desvanecida en vivo (crossfade)
            t_trans = self._monitor._duracion_transicion
            if segmentos and idx_activo < len(segmentos) - 1 and (t_actual >= t_fin - t_trans):
                seg_sig = segmentos[idx_activo + 1]
                ruta_sig = seg_sig.get("imagen", "")
                pix_sig = self._obtener_pixmap(ruta_sig)
                if pix_sig:
                    alpha = (t_actual - (t_fin - t_trans)) / max(0.1, t_trans)
                    alpha = min(1.0, max(0.0, alpha))
                    dur_sig = max(0.2, seg_sig["fin"] - seg_sig["inicio"])
                    prog_sig = min(1.0, max(0.0, (t_actual - seg_sig["inicio"]) / dur_sig))
                    efecto_sig = ConfigEfecto.desde_dict(seg_sig.get("efecto")) \
                        if seg_sig.get("efecto") else self._monitor._efecto_global
                    z_sig, x_sig, y_sig = transformacion(
                        efecto_sig, prog_sig, dur_sig, idx_activo + 1)
                    self._dibujar_imagen_transformada(
                        p, pix_sig, rect_pantalla, z_sig, x_sig, y_sig, opacidad=alpha)

        elif self._monitor._pixmap_frame and not self._monitor._pixmap_frame.isNull():
            p.drawPixmap(rect_pantalla.toRect(), self._monitor._pixmap_frame)
            dibujado = True

        elif self._monitor._ruta_audio_actual:
            # Solo audio cargado: dibujar reproductor de audio animado con forma de onda
            dibujado = True
            p.fillRect(rect_pantalla, QColor("#0d131f"))
            p.setPen(QPen(QColor("#2563eb"), 1))
            p.drawRect(rect_pantalla)

            # Forma de onda animada
            p.setPen(QPen(QColor("#38bdf8"), 2))
            c_y = rect_pantalla.center().y()
            step = 5
            x_ini = int(rect_pantalla.left() + 20)
            x_end = int(rect_pantalla.right() - 20)
            fase = t_actual * 4.0
            for x in range(x_ini, x_end, step):
                h_b = math.sin((x * 0.05) + fase) * 25 + math.sin((x * 0.1) - fase) * 15
                h_b = abs(h_b) + 4
                p.drawLine(x, int(c_y - h_b), x, int(c_y + h_b))

            p.setFont(QFont("Segoe UI", 11, QFont.Weight.Bold))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(rect_pantalla.left(), rect_pantalla.top() + 30, rect_pantalla.width(), 30),
                       Qt.AlignmentFlag.AlignCenter, f"🎵 {Path(self._monitor._ruta_audio_actual).name}")
            p.setFont(QFont("Segoe UI", 9))
            p.setPen(QColor("#94a3b8"))
            p.drawText(QRectF(rect_pantalla.left(), rect_pantalla.bottom() - 40, rect_pantalla.width(), 30),
                       Qt.AlignmentFlag.AlignCenter, "Añade imágenes en la pestaña '🖼 Imágenes' para verlas en el video")

        if not dibujado:
            # Slate vacío solo si no hay absolutamente ningún medio
            p.setPen(QColor("#1e293b"))
            p.drawRect(rect_pantalla)
            p.setPen(QColor("#64748b"))
            p.setFont(QFont("Segoe UI", 10))
            p.drawText(rect_pantalla, Qt.AlignmentFlag.AlignCenter,
                       "MONITOR LISTO\nAñade audios o imágenes en la biblioteca para previsualizar")

        # Capa de subtítulos (previsualización idéntica al render)
        self._dibujar_subtitulos(p, rect_pantalla, t_actual)

        # Borde exterior de pantalla
        p.setPen(QPen(QColor("#334155"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect_pantalla)

        # Overlay HUD: Capítulo actual
        if self._monitor._capitulo_actual:
            p.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
            texto_cap = f"📌 {self._monitor._capitulo_actual}"
            ancho_badge = len(texto_cap) * 7 + 16
            rect_cap = QRectF(x_marco + 8, y_marco + 8, ancho_badge, 22)
            p_cap = QPainterPath()
            p_cap.addRoundedRect(rect_cap, 4, 4)
            p.fillPath(p_cap, QColor(0, 0, 0, 160))
            p.setPen(QColor("#38bdf8"))
            p.drawText(rect_cap, Qt.AlignmentFlag.AlignCenter, texto_cap)

    def _obtener_pixmap(self, ruta: str) -> QPixmap | None:
        return self._monitor.cache_imagen(ruta)

    def _dibujar_imagen_transformada(self, p: QPainter, pix: QPixmap,
                                     rect_destino: QRectF, zoom: float,
                                     px: float = 0.5, py: float = 0.5,
                                     opacidad: float = 1.0) -> None:
        """Dibuja la imagen con recorte tipo ``cover`` + zoom + paneo.

        Reproduce exactamente lo que hace FFmpeg:
        ``scale=...:force_original_aspect_ratio=increase`` + ``crop`` +
        ``zoompan`` (recorte de tamaño ``tamaño/zoom`` desplazado por px/py).
        """
        if pix.isNull():
            return
        p.save()
        p.setClipRect(rect_destino)
        p.setOpacity(opacidad)

        iw, ih = float(pix.width()), float(pix.height())
        if iw <= 0 or ih <= 0:
            p.restore()
            return

        # 1) Recorte "cover" para llenar el marco sin deformar
        aspecto_marco = rect_destino.width() / max(1.0, rect_destino.height())
        aspecto_img = iw / ih
        if aspecto_img > aspecto_marco:
            crop_h = ih
            crop_w = ih * aspecto_marco
        else:
            crop_w = iw
            crop_h = iw / aspecto_marco
        crop_x = (iw - crop_w) / 2.0
        crop_y = (ih - crop_h) / 2.0

        # 2) Zoom y paneo dentro del recorte
        zoom = max(1.0, float(zoom))
        vis_w = crop_w / zoom
        vis_h = crop_h / zoom
        px = max(0.0, min(1.0, float(px)))
        py = max(0.0, min(1.0, float(py)))
        src_x = crop_x + px * max(0.0, crop_w - vis_w)
        src_y = crop_y + py * max(0.0, crop_h - vis_h)

        p.drawPixmap(rect_destino, pix, QRectF(src_x, src_y, vis_w, vis_h))
        p.restore()

    # Compatibilidad con llamadas antiguas
    def _dibujar_imagen_con_zoom(self, p: QPainter, pix: QPixmap,
                                 rect_destino: QRectF, zoom: float,
                                 opacidad: float = 1.0) -> None:
        self._dibujar_imagen_transformada(p, pix, rect_destino, zoom, 0.5, 0.5, opacidad)

    # ------------------------------------------------------------ Subtítulos
    def _dibujar_subtitulos(self, p: QPainter, rect: QRectF, t: float) -> None:
        monitor = self._monitor
        if not monitor._mostrar_subtitulos or not monitor._cues:
            return
        cue = cue_activo(monitor._cues, t)
        if not cue:
            return

        # Guardar/restaurar el estado del pincel y el lápiz: si no, el color de
        # relleno del subtítulo se filtra al resto del fotograma y lo cubre.
        p.save()
        try:
            p.setClipRect(rect)
            self._pintar_cue(p, rect, cue, monitor._estilo_sub)
        finally:
            p.restore()

    def _pintar_cue(self, p: QPainter, rect: QRectF, cue: dict,
                    estilo: EstiloSubtitulos) -> None:
        lineas = envolver_texto(cue.get("texto", ""), estilo.max_caracteres)
        if not lineas or not lineas[0]:
            return

        alto_marco = max(1.0, rect.height())
        tam_px = max(8.0, alto_marco * max(1.0, estilo.tamano) / 100.0)
        fuente = QFont(estilo.fuente or "Segoe UI")
        fuente.setPixelSize(max(8, int(round(tam_px))))
        fuente.setBold(bool(estilo.negrita))
        p.setFont(fuente)
        metricas = p.fontMetrics()

        alto_linea = metricas.height()
        alto_total = alto_linea * len(lineas)
        margen = alto_marco * max(0.0, estilo.margen) / 100.0

        if estilo.posicion == "arriba":
            y0 = rect.top() + margen
        elif estilo.posicion == "centro":
            y0 = rect.center().y() - alto_total / 2.0
        else:
            y0 = rect.bottom() - margen - alto_total

        # Caja de fondo opcional
        if estilo.caja:
            ancho_texto = max(metricas.horizontalAdvance(l) for l in lineas)
            pad_x = tam_px * 0.45
            pad_y = tam_px * 0.22
            caja = QRectF(
                rect.center().x() - ancho_texto / 2.0 - pad_x,
                y0 - pad_y,
                ancho_texto + pad_x * 2.0,
                alto_total + pad_y * 2.0,
            )
            color_caja = QColor(estilo.caja_color)
            color_caja.setAlphaF(max(0.0, min(1.0, estilo.caja_opacidad)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color_caja)
            p.drawRoundedRect(caja, tam_px * 0.18, tam_px * 0.18)

        # Texto con contorno (mismo look que el ASS del render)
        trayectoria = QPainterPath()
        for i, linea in enumerate(lineas):
            ancho_linea = metricas.horizontalAdvance(linea)
            x = rect.center().x() - ancho_linea / 2.0
            y = y0 + i * alto_linea + metricas.ascent()
            trayectoria.addText(x, y, fuente, linea)

        grosor = max(0.0, estilo.contorno * rect.height() / 1080.0)
        if grosor > 0.05:
            lapiz = QPen(QColor(estilo.color_contorno), grosor)
            lapiz.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            lapiz.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(lapiz)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(trayectoria)

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(estilo.color))
        p.drawPath(trayectoria)

