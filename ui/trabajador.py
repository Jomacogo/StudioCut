"""Trabajadores en segundo plano: render + (opcional) subida a YouTube y
detección de capítulos por voz. Nunca bloquean la UI: comunican por señales.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from core.renderer import (
    ConfigRender, construir_comando, construir_comando_un_audio, ejecutar_ffmpeg,
)
from core.media import duracion_audio, ErrorMedia


class Trabajador(QThread):
    progreso = pyqtSignal(int)
    log = pyqtSignal(str)
    listo = pyqtSignal(str)
    fallo = pyqtSignal(str)

    def __init__(self, salida: str, cfg: ConfigRender, parent=None):
        super().__init__(parent)
        self._salida = salida
        self._cfg = cfg
        self._audios: list[str] = []
        self._imagenes: list[str] = []
        self._marcas: list[float] = []
        self._modo = "multi"  # "multi" | "single" (un audio, con o sin voz TTS)
        self._solo_subir = False
        self._subir = False
        self._yt: dict = {}
        self._limitar: float | None = None
        self._cancelar_ev = threading.Event()

    def cancelar(self) -> None:
        self._cancelar_ev.set()

    def preparar(self, audios: list[str], imagenes: list[str]) -> None:
        self._audios = list(audios)
        self._imagenes = list(imagenes)

    def modo_un_audio(self, marcas: list[float]) -> None:
        self._modo = "single"
        self._marcas = list(marcas)

    def subir_a_youtube(self, opciones: dict) -> None:
        self._subir = True
        self._yt = dict(opciones)

    def solo_subir(self, video: str, opciones: dict) -> None:
        """Sube un video ya renderizado que quedó pendiente."""
        self._solo_subir = True
        self._salida = video
        self._subir = True
        self._yt = dict(opciones)

    def como_previsualizacion(self) -> None:
        self._limitar = 4.0

    def run(self) -> None:  # pragma: no cover - hilo
        try:
            if self._solo_subir:
                url = self._subir_youtube()
                self.listo.emit(url)
                return
            faltas = [i for i, img in enumerate(self._imagenes) if not img]
            if faltas:
                raise ErrorMedia(
                    f"Falta imagen para el segmento "
                    f"{', '.join(str(i + 1) for i in faltas)}."
                )
            if self._modo == "single":
                self._render_single()
            else:
                self._render_multi()
            url = ""
            if self._subir:
                url = self._subir_youtube()
            self.listo.emit(url or self._salida)
        except Exception as exc:  # noqa: BLE001
            self.fallo.emit(str(exc))

    def _render_multi(self) -> None:
        self.log.emit("Leyendo duración de los audios...")
        duraciones = []
        for i, a in enumerate(self._audios):
            if self._cancelar_ev.is_set():
                raise ErrorMedia("Operación cancelada.")
            d = duracion_audio(a)
            duraciones.append(d)
            self.log.emit(f"  {Path(a).name}: {d:.1f} s")
        total = sum(duraciones) - self._cfg.transicion_seg * (len(duraciones) - 1)
        self._renderar(
            lambda: construir_comando(
                self._imagenes, self._audios, duraciones,
                self._salida, self._cfg, limitar_seg=self._limitar),
            total, "video (audio por capítulos)",
        )

    def _render_single(self) -> None:
        if not self._marcas:
            raise ErrorMedia(
                "No hay marcas de capítulos: ejecuta «Detectar capítulos» "
                "o añádelas a mano en la tabla.")
        d = duracion_audio(self._audios[0])
        audio = self._audios[0]
        self.log.emit(f"Audio completo: {Path(audio).name} ({d / 60:.1f} min), "
                      f"{len(self._marcas)} marcas.")
        self._renderar(
            lambda: construir_comando_un_audio(
                self._imagenes, audio, self._marcas, d, self._salida,
                self._cfg, limitar_seg=self._limitar),
            d, "video (un solo audio con marcas)",
        )

    def _renderar(self, crear_cmd, total: float, etiqueta: str) -> None:
        if self._cancelar_ev.is_set():
            raise ErrorMedia("Operación cancelada.")
        try:
            cmd = crear_cmd()
            self.log.emit(f"Renderizando {etiqueta} (puede tardar varios minutos)...")
            ejecutar_ffmpeg(
                cmd, total, self.progreso.emit, self.log.emit,
                cancelar=self._cancelar_ev, limitar_seg=self._limitar,
            )
        finally:
            self._cfg.limpiar_temporales()

    def _subir_youtube(self) -> str:
        self.log.emit(
            "Conectando con YouTube (abrirá el navegador la primera vez)...")
        from core import youtube_uploader as yt

        vid = yt.subir_video(
            self._salida,
            self._yt.get("titulo", ""),
            self._yt.get("descripcion", ""),
            self._yt.get("etiquetas"),
            self._yt.get("client_secret", ""),
            Path(self._yt.get("token_path", "")),
            miniatura=self._yt.get("miniatura"),
            al_progreso=self.progreso.emit,
            al_log=self.log.emit,
        )
        url_final = f"https://youtu.be/{vid}"
        return url_final


class TrabajadorVoz(QThread):
    """Genera el audio del texto con el TTS de Windows (voz «Jorge», es-MX)."""

    log = pyqtSignal(str)
    listo = pyqtSignal(str)   # ruta del WAV generado
    fallo = pyqtSignal(str)

    def __init__(self, texto: str, salida_wav: str, voz: str | None,
                 velocidad: int, parent=None):
        super().__init__(parent)
        self._texto = texto
        self._salida = salida_wav
        self._voz = voz
        self._velocidad = velocidad
        self._cancelar_ev = threading.Event()

    def cancelar(self) -> None:
        self._cancelar_ev.set()

    def run(self) -> None:  # pragma: no cover - hilo
        try:
            from core import voz_tts

            wav = voz_tts.generar_voz(
                self._texto, self._salida, voz=self._voz,
                velocidad=self._velocidad,
                al_log=lambda m: self.log.emit(m),
                cancelar_ev=self._cancelar_ev,
            )
            self.listo.emit(wav)
        except Exception as exc:  # noqa: BLE001
            self.fallo.emit(str(exc))


class TrabajadorIA(QThread):
    """Ejecuta el flujo de IA (guion → imágenes → voz) sin bloquear la UI."""

    progreso = pyqtSignal(int, str)   # porcentaje, etapa
    log = pyqtSignal(str)
    guion = pyqtSignal(str, object)   # texto, Guion
    imagenes = pyqtSignal(list)       # rutas generadas
    voz = pyqtSignal(str)             # ruta del audio
    listo = pyqtSignal(object)        # ResultadoIA
    fallo = pyqtSignal(str)

    def __init__(self, idea: str, cfg, claves: dict, carpeta: str, parent=None):
        super().__init__(parent)
        self._idea = idea
        self._cfg = cfg
        self._claves = dict(claves or {})
        self._carpeta = carpeta
        self._cancelar_ev = threading.Event()

    def cancelar(self) -> None:
        self._cancelar_ev.set()

    def run(self) -> None:  # pragma: no cover - hilo
        try:
            from core.ia.pipeline import ejecutar_pipeline

            resultado = ejecutar_pipeline(
                self._idea,
                self._cfg,
                self._claves,
                self._carpeta,
                al_progreso=lambda p, e: self.progreso.emit(p, e),
                al_log=lambda m: self.log.emit(m),
                cancelar=self._cancelar_ev,
                al_guion=lambda texto, guion: self.guion.emit(texto, guion),
                al_imagenes=lambda rutas: self.imagenes.emit(list(rutas)),
                al_voz=lambda ruta: self.voz.emit(ruta),
            )
            self.listo.emit(resultado)
        except Exception as exc:  # noqa: BLE001
            self.fallo.emit(str(exc))


class DetectorCapitulos(QThread):
    """Transcribe el audio (Vosk offline) y localiza los capítulos."""

    progreso = pyqtSignal(int, str)   # porcentaje, etapa
    log = pyqtSignal(str)
    listo = pyqtSignal(list)          # [{titulo, tiempo}]
    palabras = pyqtSignal(list)       # [{palabra, inicio, fin, conf}]
    fallo = pyqtSignal(str)

    def __init__(self, audio: str, cfg=None, parent=None):
        super().__init__(parent)
        self._audio = audio
        self._cfg = cfg
        self._cancelar_ev = threading.Event()

    def cancelar(self) -> None:
        self._cancelar_ev.set()

    def run(self) -> None:  # pragma: no cover - hilo
        try:
            from core import chapter_detection as cd

            if self._cancelar_ev.is_set():
                raise ErrorMedia("Detección cancelada.")
            marcas = cd.detectar_de_audio(
                self._audio,
                lambda p, etapa: self.progreso.emit(
                    p if etapa != "descarga" else p, etapa),
                lambda m: self.log.emit(m),
                cfg=self._cfg,
                al_palabras=lambda palabras: self.palabras.emit(palabras),
            )
            if self._cancelar_ev.is_set():
                raise ErrorMedia("Detección cancelada.")
            self.listo.emit(marcas)
        except Exception as exc:  # noqa: BLE001
            self.fallo.emit(str(exc))
