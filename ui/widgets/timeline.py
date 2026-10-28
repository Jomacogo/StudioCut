"""Widget de Línea de Tiempo (Timeline) profesional multitrack estilo NLE.

Incluye regla de tiempo interactiva, pista de marcadores/capítulos,
pista de video V1 (clips de imagen con transiciones), pista de audio A1,
cabezal de reproducción (playhead) arrastrable y zoom.
"""
from __future__ import annotations

import math
from pathlib import Path
from PyQt6.QtCore import QPoint, QRect, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygon
)
from PyQt6.QtWidgets import QWidget

from core.effects import ConfigEfecto


class LineaDeTiempoWidget(QWidget):
    tiempo_cambiado = pyqtSignal(float)
    marca_seleccionada = pyqtSignal(int)
    marca_tiempo_modificada = pyqtSignal(int, float)
    solicitud_asignar_imagen = pyqtSignal(int, str)
    solicitud_importar_archivos = pyqtSignal(list)
    solicitud_cortar_en_tiempo = pyqtSignal(float)
    herramienta_cambiada = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(175)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)

        self._duracion_total = 60.0  # segundos por defecto
        self._tiempo_actual = 0.0
        self._marcas: list[dict] = []  # [{"titulo": str, "tiempo": float, "imagen": str}]
        self._nombre_audio = "Audio Principal"
        self._zoom = 1.0  # factor de zoom horizontal (1.0 = normal)
        self._arrastrando_playhead = False
        self._arrastrando_marca: int | None = None
        self._indice_hover_marca: int | None = None

        # Herramientas de edición profesional NLE estilo Premiere
        self._herramienta: str = "select"  # "select" (V) | "razor" (C)
        self._snap_activo: bool = True
        self._arrastrando_borde: int | None = None
        self._borde_hover_idx: int | None = None
        self._mouse_x_razor: float | None = None

        # Dimensiones de layout
        self._ancho_cabecera = 54   # Ancho columna etiquetas V1, A1, M
        self._alto_regla = 28
        self._alto_marcas = 30
        self._alto_video = 48
        self._alto_audio = 38
        self._espacio_pistas = 4

    # ------------------------------------------------------------- Propiedades
    def set_duracion(self, duracion: float) -> None:
        self._duracion_total = max(1.0, float(duracion))
        self.update()

    def set_tiempo_actual(self, t: float) -> None:
        self._tiempo_actual = max(0.0, min(float(t), self._duracion_total))
        self.update()

    def set_datos(self, marcas: list[dict], duracion: float | None = None, nombre_audio: str = "") -> None:
        self._marcas = list(marcas)
        if duracion and duracion > 0:
            self._duracion_total = float(duracion)
        elif self._marcas:
            max_t = max((m.get("tiempo", 0.0) for m in self._marcas), default=60.0)
            self._duracion_total = max(self._duracion_total, max_t + 10.0)
        if nombre_audio:
            self._nombre_audio = nombre_audio
        self.update()

    # -------------------------------------------------------- Coordenadas y Tiempo
    def _ancho_pistas(self) -> int:
        return max(100, self.width() - self._ancho_cabecera - 16)

    def _px_por_segundo(self) -> float:
        return (self._ancho_pistas() / max(1.0, self._duracion_total)) * self._zoom

    def _segundo_a_x(self, seg: float) -> float:
        return self._ancho_cabecera + (seg * self._px_por_segundo())

    def _x_a_segundo(self, x: float) -> float:
        seg = (x - self._ancho_cabecera) / max(0.001, self._px_por_segundo())
        return max(0.0, min(seg, self._duracion_total))

    # --------------------------------------------------------------- Eventos de Pintado
    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

        ancho = self.width()
        alto = self.height()

        # Fondo general de la línea de tiempo
        p.fillRect(0, 0, ancho, alto, QColor("#12141a"))

        # Regla de tiempo superior
        self._dibujar_regla(p)

        # Cabeceras de pistas (V1, A1, Marcadores)
        self._dibujar_cabeceras(p)

        # Pista de Marcadores/Capítulos
        self._dibujar_pista_marcas(p)

        # Pista de Video V1 (Clips de imágenes)
        self._dibujar_pista_video(p)

        # Pista de Audio A1 (Forma de onda visual)
        self._dibujar_pista_audio(p)

        # Cabezal de reproducción (Playhead)
        self._dibujar_playhead(p)

        # Manija de Trim / Estiramiento y Guía de Cuchilla (Razor)
        self._dibujar_manija_trim(p)
        self._dibujar_guia_razor(p)

    def _dibujar_regla(self, p: QPainter) -> None:
        p.fillRect(0, 0, self.width(), self._alto_regla, QColor("#181b24"))
        p.setPen(QColor("#2d3242"))
        p.drawLine(0, self._alto_regla, self.width(), self._alto_regla)

        # Cuadrícula y números de segundo/minuto
        px_seg = self._px_por_segundo()
        if px_seg <= 0:
            return

        # Elegir intervalo inteligente según zoom
        posibles_intervalos = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        intervalo = 10
        for cand in posibles_intervalos:
            if cand * px_seg >= 65:
                intervalo = cand
                break

        p.setFont(QFont("Segoe UI", 8))
        total_segs = int(math.ceil(self._duracion_total))

        for s in range(0, total_segs + intervalo, intervalo):
            x = self._segundo_a_x(s)
            if x > self.width():
                break
            if x >= self._ancho_cabecera:
                # Marca principal
                p.setPen(QColor("#4f566b"))
                p.drawLine(int(x), self._alto_regla - 10, int(x), self._alto_regla)

                # Texto tiempo (mm:ss)
                mins = s // 60
                segs = s % 60
                texto = f"{mins:02d}:{segs:02d}"
                p.setPen(QColor("#9da5b8"))
                p.drawText(int(x) + 4, self._alto_regla - 4, texto)

            # Sub-marcas
            sub_inter = intervalo / 5.0
            if sub_inter * px_seg >= 8:
                for k in range(1, 5):
                    sub_s = s + k * sub_inter
                    sub_x = self._segundo_a_x(sub_s)
                    if self._ancho_cabecera <= sub_x <= self.width():
                        p.setPen(QColor("#2c3140"))
                        p.drawLine(int(sub_x), self._alto_regla - 5, int(sub_x), self._alto_regla)

    def _dibujar_cabeceras(self, p: QPainter) -> None:
        y_marcas = self._alto_regla
        y_video = y_marcas + self._alto_marcas + self._espacio_pistas
        y_audio = y_video + self._alto_video + self._espacio_pistas

        p.fillRect(0, y_marcas, self._ancho_cabecera, self.height() - y_marcas, QColor("#181b24"))
        p.setPen(QColor("#2d3242"))
        p.drawLine(self._ancho_cabecera, 0, self._ancho_cabecera, self.height())

        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))

        # Etiqueta M (Marcas)
        p.setPen(QColor("#a855f7"))
        p.drawText(QRect(0, y_marcas, self._ancho_cabecera, self._alto_marcas),
                   Qt.AlignmentFlag.AlignCenter, "CAPS")

        # Etiqueta V1
        p.setPen(QColor("#38bdf8"))
        p.drawText(QRect(0, y_video, self._ancho_cabecera, self._alto_video),
                   Qt.AlignmentFlag.AlignCenter, "V1")

        # Etiqueta A1
        p.setPen(QColor("#4ade80"))
        p.drawText(QRect(0, y_audio, self._ancho_cabecera, self._alto_audio),
                   Qt.AlignmentFlag.AlignCenter, "A1")

    def _dibujar_pista_marcas(self, p: QPainter) -> None:
        y0 = self._alto_regla
        h = self._alto_marcas
        p.fillRect(self._ancho_cabecera, y0, self.width() - self._ancho_cabecera, h, QColor("#151821"))
        p.setPen(QColor("#202431"))
        p.drawLine(self._ancho_cabecera, y0 + h, self.width(), y0 + h)

        if not self._marcas:
            p.setPen(QColor("#4b5568"))
            p.setFont(QFont("Segoe UI", 8, italic=True))
            p.drawText(self._ancho_cabecera + 15, y0 + 20, "Sin marcas detectadas · Usa '🎙 Detectar capítulos' o '+ Marca'")
            return

        p.setFont(QFont("Segoe UI", 8))
        for i, m in enumerate(self._marcas):
            t = float(m.get("tiempo", 0.0))
            x = self._segundo_a_x(t)
            titulo = m.get("titulo", f"Marca {i+1}")
            es_intro = "intro" in titulo.lower()
            es_cierre = "cierre" in titulo.lower() or "outro" in titulo.lower()

            if es_intro:
                color_chip = QColor("#059669")
                color_border = QColor("#34d399")
            elif es_cierre:
                color_chip = QColor("#d97706")
                color_border = QColor("#fbbf24")
            else:
                color_chip = QColor("#4f46e5")
                color_border = QColor("#818cf8")

            # Banderita o chip de capítulo
            ancho_chip = max(60, len(titulo) * 6 + 18)
            rect_chip = QRectF(x, y0 + 3, ancho_chip, h - 6)

            # Fondo del chip
            path = QPainterPath()
            path.addRoundedRect(rect_chip, 4, 4)
            p.fillPath(path, color_chip)
            p.setPen(QPen(color_border, 1))
            p.drawPath(path)

            # Texto
            p.setPen(QColor("#ffffff"))
            p.drawText(rect_chip, Qt.AlignmentFlag.AlignCenter, titulo)

            # Línea vertical punteada indicando el corte
            p.setPen(QPen(QColor(color_border.red(), color_border.green(), color_border.blue(), 180), 1, Qt.PenStyle.DashLine))
            p.drawLine(int(x), y0 + h, int(x), self.height())

    def _dibujar_pista_video(self, p: QPainter) -> None:
        y0 = self._alto_regla + self._alto_marcas + self._espacio_pistas
        h = self._alto_video
        p.fillRect(self._ancho_cabecera, y0, self.width() - self._ancho_cabecera, h, QColor("#141720"))
        p.setPen(QColor("#202431"))
        p.drawLine(self._ancho_cabecera, y0 + h, self.width(), y0 + h)

        # Si hay marcas, cada segmento entre marcas es un clip visual
        if self._marcas:
            marcas_ordenadas = sorted(self._marcas, key=lambda m: float(m.get("tiempo", 0.0)))
            tiempos = [float(m.get("tiempo", 0.0)) for m in marcas_ordenadas]
            if tiempos[0] > 0.0:
                tiempos = [0.0] + tiempos
            tiempos.append(self._duracion_total)

            p.setFont(QFont("Segoe UI", 8))
            for i in range(len(tiempos) - 1):
                t_ini = tiempos[i]
                t_fin = tiempos[i + 1]
                if t_fin <= t_ini:
                    continue

                x1 = self._segundo_a_x(t_ini)
                x2 = self._segundo_a_x(t_fin)
                w = max(4.0, x2 - x1)

                clip_rect = QRectF(x1 + 1, y0 + 3, w - 2, h - 6)

                # Degradado de clip de video
                grad = QLinearGradient(clip_rect.topLeft(), clip_rect.bottomLeft())
                if i % 2 == 0:
                    grad.setColorAt(0, QColor("#1e3a8a"))
                    grad.setColorAt(1, QColor("#172554"))
                    borde = QColor("#3b82f6")
                else:
                    grad.setColorAt(0, QColor("#312e81"))
                    grad.setColorAt(1, QColor("#1e1b4b"))
                    borde = QColor("#6366f1")

                p_clip = QPainterPath()
                p_clip.addRoundedRect(clip_rect, 5, 5)
                p.fillPath(p_clip, grad)
                p.setPen(QPen(borde, 1))
                p.drawPath(p_clip)

                # Título de imagen / clip
                nombre_img = f"Imagen #{i+1}"
                if i < len(self._marcas):
                    img_path = self._marcas[i].get("imagen", "")
                    if img_path:
                        nombre_img = Path(img_path).name

                p.setPen(QColor("#e2e8f0"))
                if w > 35:
                    p.drawText(QRectF(x1 + 6, y0 + 6, w - 12, 16),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               nombre_img)
                    p.setPen(QColor("#93c5fd"))
                    dur_seg = t_fin - t_ini
                    efecto = self._marcas[i].get("efecto")
                    if hasattr(efecto, "resumen"):
                        etiqueta_efecto = efecto.resumen()
                    elif isinstance(efecto, dict):
                        etiqueta_efecto = ConfigEfecto.desde_dict(efecto).resumen()
                    else:
                        etiqueta_efecto = "Sin movimiento"
                    p.drawText(QRectF(x1 + 6, y0 + 24, w - 12, 14),
                               Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                               f"🎬 {dur_seg:.1f}s · {etiqueta_efecto}")
        else:
            # Clip único por defecto
            x1 = self._segundo_a_x(0.0)
            x2 = self._segundo_a_x(self._duracion_total)
            clip_rect = QRectF(x1 + 1, y0 + 3, max(20.0, x2 - x1 - 2), h - 6)
            p.fillRect(clip_rect, QColor("#1e293b"))
            p.setPen(QColor("#475569"))
            p.drawRect(clip_rect)
            p.setPen(QColor("#94a3b8"))
            p.drawText(clip_rect, Qt.AlignmentFlag.AlignCenter, "Pista de Video (V1) · Asigna imágenes a cada capítulo")

    def _dibujar_pista_audio(self, p: QPainter) -> None:
        y0 = self._alto_regla + self._alto_marcas + self._alto_video + (self._espacio_pistas * 2)
        h = self._alto_audio
        p.fillRect(self._ancho_cabecera, y0, self.width() - self._ancho_cabecera, h, QColor("#10131a"))

        # Clip de audio
        x1 = self._segundo_a_x(0.0)
        x2 = self._segundo_a_x(self._duracion_total)
        w = max(10.0, x2 - x1)
        audio_rect = QRectF(x1 + 1, y0 + 3, w - 2, h - 6)

        grad_a = QLinearGradient(audio_rect.topLeft(), audio_rect.bottomLeft())
        grad_a.setColorAt(0, QColor("#064e3b"))
        grad_a.setColorAt(1, QColor("#022c22"))
        path_a = QPainterPath()
        path_a.addRoundedRect(audio_rect, 4, 4)
        p.fillPath(path_a, grad_a)
        p.setPen(QPen(QColor("#10b981"), 1))
        p.drawPath(path_a)

        # Dibujar onda sintética decorativa estilo Premiere/Resolve
        p.setPen(QPen(QColor("#34d399"), 1))
        centro_y = y0 + (h / 2.0)
        paso_px = 3
        inicio_px = int(x1 + 5)
        fin_px = int(x2 - 5)

        for px in range(inicio_px, min(fin_px, self.width()), paso_px):
            # onda sintética suave
            val = math.sin((px - inicio_px) * 0.15) * 0.5 + math.sin((px - inicio_px) * 0.05) * 0.4
            altura_onda = max(2.0, abs(val) * (h * 0.35))
            p.drawLine(px, int(centro_y - altura_onda), px, int(centro_y + altura_onda))

        # Nombre del audio
        p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
        p.setPen(QColor("#d1fae5"))
        p.drawText(QRectF(x1 + 8, y0 + 5, w - 16, 16),
                   Qt.AlignmentFlag.AlignLeft, f"🎵 {self._nombre_audio} ({self._duracion_total:.1f}s)")

    def _dibujar_playhead(self, p: QPainter) -> None:
        x = int(self._segundo_a_x(self._tiempo_actual))
        if x < self._ancho_cabecera or x > self.width():
            return

        # Línea vertical del cabezal
        p.setPen(QPen(QColor("#ef4444"), 2))
        p.drawLine(x, 0, x, self.height())

        # Cabezal triangular superior en la regla
        p.setBrush(QBrush(QColor("#ef4444")))
        p.setPen(QPen(QColor("#b91c1c"), 1))
        poligono = QPolygon([
            QPoint(x - 6, 0),
            QPoint(x + 6, 0),
            QPoint(x + 6, self._alto_regla - 8),
            QPoint(x, self._alto_regla - 1),
            QPoint(x - 6, self._alto_regla - 8),
        ])
        p.drawPolygon(poligono)

    def _dibujar_manija_trim(self, p: QPainter) -> None:
        """Dibuja manijas de recorte / trim estilo Premiere en el borde activo."""
        idx = self._arrastrando_borde if self._arrastrando_borde is not None else self._borde_hover_idx
        if idx is not None and 0 <= idx < len(self._marcas):
            t = float(self._marcas[idx].get("tiempo", 0.0))
            x = int(self._segundo_a_x(t))
            y_v = self._alto_regla + self._alto_marcas + self._espacio_pistas
            h_v = self._alto_video
            p.setPen(QPen(QColor("#f59e0b"), 2))
            p.drawLine(x, y_v, x, y_v + h_v)
            # Brackets de trim estilo Premiere / DaVinci
            p.fillRect(QRect(x - 4, y_v + 4, 3, h_v - 8), QColor("#f59e0b"))
            p.fillRect(QRect(x + 2, y_v + 4, 3, h_v - 8), QColor("#f59e0b"))

    def _dibujar_guia_razor(self, p: QPainter) -> None:
        """Dibuja la línea de corte y la tijera en tiempo real con la herramienta Cuchilla."""
        if self._herramienta == "razor" and self._mouse_x_razor is not None:
            x = int(self._mouse_x_razor)
            t = self._x_a_segundo(x)
            p.setPen(QPen(QColor("#ef4444"), 1, Qt.PenStyle.DashLine))
            p.drawLine(x, self._alto_regla, x, self.height())
            rect_et = QRectF(x - 28, self._alto_regla + 2, 56, 16)
            p.fillRect(rect_et, QColor("#b91c1c"))
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            p.setPen(QColor("#ffffff"))
            p.drawText(rect_et, Qt.AlignmentFlag.AlignCenter, f"✂ {t:.1f}s")

    # ------------------------------------------------------------- Herramientas NLE
    def set_herramienta(self, modo: str) -> None:
        """Cambia entre 'select' (V) y 'razor' (C)."""
        self._herramienta = modo
        if modo == "razor":
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.herramienta_cambiada.emit(modo)
        self.update()

    def set_snap(self, activo: bool) -> None:
        self._snap_activo = activo
        self.update()

    def set_zoom_factor(self, factor: float) -> None:
        self._zoom = max(0.5, min(5.0, float(factor)))
        self.update()

    def dividir_en_playhead(self) -> None:
        """Corta el clip activo exactamente en la posición del cabezal de reproducción."""
        if 0.0 < self._tiempo_actual < self._duracion_total:
            self.solicitud_cortar_en_tiempo.emit(self._tiempo_actual)

    def _cerca_de_borde_clip(self, x: float, y: float) -> int | None:
        """Devuelve el índice de la marca cuyo borde de corte está cerca de x."""
        y_video = self._alto_regla + self._alto_marcas + self._espacio_pistas
        y_fin_video = y_video + self._alto_video
        if not (self._alto_regla <= y <= y_fin_video):
            return None
        for idx, m in enumerate(self._marcas):
            t = float(m.get("tiempo", 0.0))
            if t <= 0.0 or t >= self._duracion_total:
                continue
            mx = self._segundo_a_x(t)
            if abs(x - mx) <= 7:
                return idx
        return None

    def _aplicar_snap(self, t: float, tolerancia_px: float = 8.0) -> float:
        """Si el tiempo está cerca del playhead u otro corte, lo magnetiza."""
        if not self._snap_activo:
            return t
        px_t = self._segundo_a_x(t)
        # Snap al playhead
        px_play = self._segundo_a_x(self._tiempo_actual)
        if abs(px_t - px_play) <= tolerancia_px:
            return self._tiempo_actual
        # Snap a otras marcas
        for m in self._marcas:
            mt = float(m.get("tiempo", 0.0))
            if abs(t - mt) > 0.01:
                px_m = self._segundo_a_x(mt)
                if abs(px_t - px_m) <= tolerancia_px:
                    return mt
        return t

    # ------------------------------------------------------------- Interacción
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            x = event.position().x()
            y = event.position().y()

            # 1. MODO CUCHILLA (RAZOR / C)
            if self._herramienta == "razor":
                if x >= self._ancho_cabecera:
                    t_corte = self._x_a_segundo(x)
                    t_corte = self._aplicar_snap(t_corte)
                    self.solicitud_cortar_en_tiempo.emit(t_corte)
                return

            # 2. MODO SELECCIÓN / ESTIRAR (TRIM)
            # Revisar si hizo clic sobre un borde para estirar / trim
            borde_idx = self._cerca_de_borde_clip(x, y)
            if borde_idx is not None:
                self._arrastrando_borde = borde_idx
                self.marca_seleccionada.emit(borde_idx)
                return

            # Revisar si hizo clic en una marca para moverla
            if self._alto_regla <= y <= self._alto_regla + self._alto_marcas:
                for idx, m in enumerate(self._marcas):
                    mx = self._segundo_a_x(float(m.get("tiempo", 0.0)))
                    if abs(x - mx) <= 20:
                        self._arrastrando_marca = idx
                        self.marca_seleccionada.emit(idx)
                        return

            # Click en la regla o pista para posicionar playhead
            if x >= self._ancho_cabecera:
                self._arrastrando_playhead = True
                nuevo_t = self._x_a_segundo(x)
                nuevo_t = self._aplicar_snap(nuevo_t)
                self.set_tiempo_actual(nuevo_t)
                self.tiempo_cambiado.emit(nuevo_t)

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        y = event.position().y()

        # Si la herramienta es cuchilla, guardar mouse_x para guía visual
        if self._herramienta == "razor":
            self._mouse_x_razor = x if x >= self._ancho_cabecera else None
            self.update()
            return

        # Arrastrando Playhead
        if self._arrastrando_playhead:
            nuevo_t = self._x_a_segundo(x)
            nuevo_t = self._aplicar_snap(nuevo_t)
            self.set_tiempo_actual(nuevo_t)
            self.tiempo_cambiado.emit(nuevo_t)
            return

        # Arrastrando borde (Estirar / Trim)
        if self._arrastrando_borde is not None and 0 <= self._arrastrando_borde < len(self._marcas):
            nuevo_t = self._x_a_segundo(x)
            nuevo_t = self._aplicar_snap(nuevo_t)
            nuevo_t = max(0.5, min(nuevo_t, self._duracion_total - 0.5))
            self._marcas[self._arrastrando_borde]["tiempo"] = nuevo_t
            self.marca_tiempo_modificada.emit(self._arrastrando_borde, nuevo_t)
            self.update()
            return

        # Arrastrando marca completa
        if self._arrastrando_marca is not None and 0 <= self._arrastrando_marca < len(self._marcas):
            nuevo_t = self._x_a_segundo(x)
            nuevo_t = self._aplicar_snap(nuevo_t)
            self._marcas[self._arrastrando_marca]["tiempo"] = nuevo_t
            self.marca_tiempo_modificada.emit(self._arrastrando_marca, nuevo_t)
            self.update()
            return

        # Hover pasivo: detectar si el cursor está sobre un borde de corte para mostrar cursor SizeHor
        borde_idx = self._cerca_de_borde_clip(x, y)
        if borde_idx is not None:
            self._borde_hover_idx = borde_idx
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        else:
            self._borde_hover_idx = None
            if self.cursor().shape() == Qt.CursorShape.SizeHorCursor:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._arrastrando_playhead = False
        self._arrastrando_marca = None
        self._arrastrando_borde = None
        if self._herramienta != "razor" and self.cursor().shape() != Qt.CursorShape.SizeHorCursor:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        self.update()

    def leaveEvent(self, event) -> None:
        self._mouse_x_razor = None
        self._borde_hover_idx = None
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event) -> None:
        # Ctrl + Rueda o rueda normal para zoom horizontal
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(5.0, self._zoom * 1.15)
        else:
            self._zoom = max(0.5, self._zoom / 1.15)
        self.update()

    def _indice_segmento_en_tiempo(self, t: float) -> int:
        if not self._marcas:
            return 0
        marcas_ordenadas = sorted(self._marcas, key=lambda m: float(m.get("tiempo", 0.0)))
        tiempos = [float(m.get("tiempo", 0.0)) for m in marcas_ordenadas]
        if tiempos[0] > 0.0:
            tiempos = [0.0] + tiempos
        tiempos.append(self._duracion_total)

        for i in range(len(tiempos) - 1):
            if tiempos[i] <= t <= tiempos[i + 1]:
                return i
        return max(0, len(self._marcas) - 1)

    # ------------------------------------------------------------- Drag & Drop
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        rutas: list[str] = []
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.isLocalFile():
                    rutas.append(u.toLocalFile())
        elif event.mimeData().hasText():
            txt = event.mimeData().text().strip()
            for linea in txt.splitlines():
                linea = linea.strip().strip('"').strip("'")
                if linea and Path(linea).exists():
                    rutas.append(linea)

        if not rutas:
            event.ignore()
            return

        x = event.position().x()
        t = self._x_a_segundo(x)
        idx_seg = self._indice_segmento_en_tiempo(t)

        exts_img = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        if len(rutas) == 1 and Path(rutas[0]).suffix.lower() in exts_img:
            self.solicitud_asignar_imagen.emit(idx_seg, rutas[0])
            event.acceptProposedAction()
            return

        self.solicitud_importar_archivos.emit(rutas)
        event.acceptProposedAction()
