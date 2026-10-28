"""Flujo «casi automático»: idea → guion → imágenes → voz.

La interfaz solo tiene que llamar a :func:`ejecutar_pipeline` en un hilo y
recoger los resultados. Todo lo específico de cada proveedor queda aquí.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .guion import Guion, generar_guion, marcas_estimadas
from .imagenes import ESTILOS, generar_imagenes
from .proveedores import (
    BASE_OPENROUTER,
    MODELO_IMAGEN_DEFECTO,
    MODELO_TEXTO_DEFECTO,
    ErrorIA,
)
from .voz import generar_voz_ia


@dataclass
class ConfigIA:
    """Ajustes del panel de IA (nunca incluye claves)."""

    proveedor_texto: str = "openrouter"
    base_url_texto: str = BASE_OPENROUTER
    modelo_texto: str = MODELO_TEXTO_DEFECTO
    modelos_respaldo: list[str] = field(default_factory=list)

    proveedor_imagen: str = "pollinations"
    modelo_imagen: str = "flux"
    estilo_imagen: str = "cinematografico"
    generar_imagenes: bool = True
    respaldo_imagenes_gratis: bool = True

    proveedor_voz: str = "edge"
    voz_edge: str = ""
    voz_elevenlabs: str = ""
    generar_voz: bool = True
    insertar_guion: bool = True

    duracion_min: int = 5
    n_capitulos: int = 3
    tono: str = "cercano y directo"
    publico: str = "público general"
    idioma: str = "español neutro"

    def a_dict(self) -> dict:
        return {
            "proveedor_texto": self.proveedor_texto,
            "base_url_texto": self.base_url_texto,
            "modelo_texto": self.modelo_texto,
            "modelos_respaldo": list(self.modelos_respaldo),
            "proveedor_imagen": self.proveedor_imagen,
            "modelo_imagen": self.modelo_imagen,
            "estilo_imagen": self.estilo_imagen,
            "generar_imagenes": bool(self.generar_imagenes),
            "respaldo_imagenes_gratis": bool(self.respaldo_imagenes_gratis),
            "proveedor_voz": self.proveedor_voz,
            "voz_edge": self.voz_edge,
            "voz_elevenlabs": self.voz_elevenlabs,
            "generar_voz": bool(self.generar_voz),
            "insertar_guion": bool(self.insertar_guion),
            "duracion_min": int(self.duracion_min),
            "n_capitulos": int(self.n_capitulos),
            "tono": self.tono,
            "publico": self.publico,
            "idioma": self.idioma,
        }

    @classmethod
    def desde_dict(cls, datos: dict | None) -> "ConfigIA":
        datos = datos or {}
        cfg = cls()
        for campo in (
            "proveedor_texto", "base_url_texto", "modelo_texto",
            "proveedor_imagen", "modelo_imagen", "estilo_imagen",
            "proveedor_voz", "voz_edge", "voz_elevenlabs",
            "tono", "publico", "idioma",
        ):
            if datos.get(campo) not in (None, ""):
                setattr(cfg, campo, str(datos[campo]))
        for campo in ("generar_imagenes", "respaldo_imagenes_gratis", "generar_voz",
                      "insertar_guion"):
            if campo in datos:
                setattr(cfg, campo, bool(datos[campo]))
        for campo in ("duracion_min", "n_capitulos"):
            if datos.get(campo) is not None:
                try:
                    setattr(cfg, campo, int(datos[campo]))
                except (TypeError, ValueError):
                    pass
        if isinstance(datos.get("modelos_respaldo"), list):
            cfg.modelos_respaldo = [str(m) for m in datos["modelos_respaldo"] if m]
        if cfg.estilo_imagen not in ESTILOS:
            cfg.estilo_imagen = "cinematografico"
        return cfg


@dataclass
class ResultadoIA:
    guion: Guion
    imagenes: list[str] = field(default_factory=list)
    voz: str = ""
    marcas: list[dict] = field(default_factory=list)

    def a_dict(self) -> dict:
        return {
            "guion": self.guion.a_dict(),
            "imagenes": list(self.imagenes),
            "voz": self.voz,
            "marcas": list(self.marcas),
        }


def _cancelado(cancelar) -> bool:
    return cancelar is not None and cancelar.is_set()


def ejecutar_pipeline(
    idea: str,
    cfg: ConfigIA,
    claves: dict,
    carpeta_imagenes: str | Path,
    *,
    al_progreso=None,
    al_log=None,
    cancelar=None,
    al_guion=None,
    al_imagenes=None,
    al_voz=None,
) -> ResultadoIA:
    """Ejecuta el flujo completo y avisa por callbacks según avanza."""
    clave_texto = _clave(claves, cfg.proveedor_texto)
    clave_imagen = _clave(claves, cfg.proveedor_imagen)
    clave_eleven = _clave(claves, "elevenlabs")

    if cfg.proveedor_texto == "openrouter" and not clave_texto:
        raise ErrorIA(
            "Falta la clave de OpenRouter. Pégala en la pestaña «🤖 IA»."
        )

    _progreso(al_progreso, 3, "preparando")
    if _cancelado(cancelar):
        raise ErrorIA("Operación cancelada.")

    # 1. Guion -------------------------------------------------------------
    _progreso(al_progreso, 8, "generando guion")
    guion = generar_guion(
        idea,
        clave=clave_texto,
        modelo=cfg.modelo_texto,
        base_url=cfg.base_url_texto,
        duracion_min=cfg.duracion_min,
        n_capitulos=cfg.n_capitulos,
        tono=cfg.tono,
        publico=cfg.publico,
        idioma=cfg.idioma,
        al_log=al_log,
    )
    if _cancelado(cancelar):
        raise ErrorIA("Operación cancelada.")
    _progreso(al_progreso, 38, "guion listo")
    if al_guion is not None:
        al_guion(guion.texto, guion)

    # 2. Imágenes ----------------------------------------------------------
    imagenes: list[str] = []
    if cfg.generar_imagenes:
        _progreso(al_progreso, 42, "generando imágenes")
        try:
            imagenes = generar_imagenes(
                guion,
                carpeta_imagenes,
                proveedor=cfg.proveedor_imagen,
                clave=clave_imagen,
                modelo=cfg.modelo_imagen,
                estilo=cfg.estilo_imagen,
                al_progreso=lambda p, etiqueta: _progreso(
                    al_progreso, 42 + int(p * 0.36), f"imágenes: {etiqueta}"
                ),
                al_log=al_log,
                cancelar=cancelar,
            )
        except ErrorIA as exc:
            if (cfg.respaldo_imagenes_gratis
                    and cfg.proveedor_imagen not in ("pollinations", "ninguno")):
                if al_log:
                    al_log(
                        f"[Imágenes] {cfg.proveedor_imagen} falló ({exc}). "
                        "Se reintenta con Pollinations (gratis)."
                    )
                imagenes = generar_imagenes(
                    guion,
                    carpeta_imagenes,
                    proveedor="pollinations",
                    clave="",
                    modelo="",
                    estilo=cfg.estilo_imagen,
                    al_progreso=lambda p, etiqueta: _progreso(
                        al_progreso, 42 + int(p * 0.36), f"imágenes: {etiqueta}"
                    ),
                    al_log=al_log,
                    cancelar=cancelar,
                )
            else:
                raise
        if _cancelado(cancelar):
            raise ErrorIA("Operación cancelada.")
        _progreso(al_progreso, 80, "imágenes listas")
        if al_imagenes is not None:
            al_imagenes(list(imagenes))
    else:
        _progreso(al_progreso, 80, "imágenes desactivadas")

    # 3. Voz ---------------------------------------------------------------
    voz = ""
    if cfg.generar_voz:
        _progreso(al_progreso, 84, "generando voz")
        carpeta_voz = Path(carpeta_imagenes).parent / "audios_generados"
        carpeta_voz.mkdir(parents=True, exist_ok=True)
        import time as _time

        salida = carpeta_voz / f"voz_ia_{_time.strftime('%Y%m%d_%H%M%S')}.wav"
        voz = generar_voz_ia(
            guion.texto,
            salida,
            proveedor=cfg.proveedor_voz,
            clave_elevenlabs=clave_eleven,
            voice_id=cfg.voz_elevenlabs,
            voz_edge=cfg.voz_edge,
            al_log=al_log,
            cancelar_ev=cancelar,
        )
        if _cancelado(cancelar):
            raise ErrorIA("Operación cancelada.")
        _progreso(al_progreso, 97, "voz lista")
        if al_voz is not None:
            al_voz(voz)
    else:
        _progreso(al_progreso, 97, "voz desactivada")

    marcas = marcas_estimadas(guion, 0.0)
    resultado = ResultadoIA(guion=guion, imagenes=imagenes, voz=voz, marcas=marcas)
    _progreso(al_progreso, 100, "completado")
    return resultado


def _clave(claves: dict | None, proveedor: str) -> str:
    if not claves:
        return ""
    return str(claves.get((proveedor or "").lower()) or "")


def _progreso(callback, valor: int, etapa: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(valor))), etapa)
