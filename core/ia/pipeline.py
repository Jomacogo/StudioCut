"""Flujo «casi automático»: idea → guion → imágenes → voz.

La interfaz solo tiene que llamar a :func:`ejecutar_pipeline` en un hilo y
recoger los resultados. Todo lo específico de cada proveedor queda aquí.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .guion import (
    PROMPT_CANAL_DEFECTO,
    Guion,
    generar_guion,
    generar_paquete_editorial,
    marcas_estimadas,
)
from .imagenes import ESTILOS, generar_imagenes
from .proveedores import (
    BASE_OPENROUTER,
    MODELO_IMAGEN_DEFECTO,
    MODELO_TEXTO_DEFECTO,
    ErrorIA,
    generar_imagen_bytes,
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
    cuenta_flow: str = ""
    estilo_imagen: str = "cinematografico"
    generar_imagenes: bool = True
    respaldo_imagenes_gratis: bool = True

    # IA local (ComfyUI / Forge en este mismo equipo)
    backend_local: str = "comfyui"
    url_local: str = ""
    modelo_local: str = ""
    pasos_local: int = 25
    workflow_local: str = ""

    # Plantilla propia de miniatura y visor de prompts
    prompt_miniatura: str = ""
    mostrar_prompts: bool = False

    proveedor_voz: str = "edge"
    voz_edge: str = ""
    voz_elevenlabs: str = ""
    generar_voz: bool = True
    insertar_guion: bool = True

    duracion_min: int = 30
    n_capitulos: int = 3
    tono: str = "cercano y directo"
    publico: str = "público general"
    idioma: str = "español neutro"
    prompt_canal: str = PROMPT_CANAL_DEFECTO
    generar_miniatura: bool = True
    generar_paquete: bool = True
    paquete_editorial: dict = field(default_factory=dict)

    def a_dict(self) -> dict:
        return {
            "proveedor_texto": self.proveedor_texto,
            "base_url_texto": self.base_url_texto,
            "modelo_texto": self.modelo_texto,
            "modelos_respaldo": list(self.modelos_respaldo),
            "proveedor_imagen": self.proveedor_imagen,
            "modelo_imagen": self.modelo_imagen,
            "cuenta_flow": self.cuenta_flow,
            "estilo_imagen": self.estilo_imagen,
            "generar_imagenes": bool(self.generar_imagenes),
            "respaldo_imagenes_gratis": bool(self.respaldo_imagenes_gratis),
            "backend_local": self.backend_local,
            "url_local": self.url_local,
            "modelo_local": self.modelo_local,
            "pasos_local": int(self.pasos_local),
            "workflow_local": self.workflow_local,
            "prompt_miniatura": self.prompt_miniatura,
            "mostrar_prompts": bool(self.mostrar_prompts),
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
            "prompt_canal": self.prompt_canal,
            "generar_miniatura": bool(self.generar_miniatura),
            "generar_paquete": bool(self.generar_paquete),
            "paquete_editorial": dict(self.paquete_editorial),
        }

    @classmethod
    def desde_dict(cls, datos: dict | None) -> "ConfigIA":
        datos = datos or {}
        cfg = cls()
        for campo in (
            "proveedor_texto", "base_url_texto", "modelo_texto",
            "proveedor_imagen", "modelo_imagen", "cuenta_flow", "estilo_imagen",
            "proveedor_voz", "voz_edge", "voz_elevenlabs",
            "tono", "publico", "idioma", "prompt_canal",
            "backend_local", "url_local", "modelo_local", "workflow_local",
            "prompt_miniatura",
        ):
            if datos.get(campo) not in (None, ""):
                setattr(cfg, campo, str(datos[campo]))
        for campo in ("generar_imagenes", "respaldo_imagenes_gratis", "generar_voz",
                      "insertar_guion", "generar_miniatura", "generar_paquete",
                      "mostrar_prompts"):
            if campo in datos:
                setattr(cfg, campo, bool(datos[campo]))
        for campo in ("duracion_min", "n_capitulos", "pasos_local"):
            if datos.get(campo) is not None:
                try:
                    setattr(cfg, campo, int(datos[campo]))
                except (TypeError, ValueError):
                    pass
        if isinstance(datos.get("paquete_editorial"), dict):
            cfg.paquete_editorial = dict(datos["paquete_editorial"])
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
    paquete: dict = field(default_factory=dict)

    def a_dict(self) -> dict:
        return {
            "guion": self.guion.a_dict(),
            "imagenes": list(self.imagenes),
            "voz": self.voz,
            "marcas": list(self.marcas),
            "paquete": dict(self.paquete),
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
    al_paquete=None,
    guion_previo: Guion | None = None,
) -> ResultadoIA:
    """Ejecuta el flujo completo y avisa por callbacks según avanza."""
    clave_texto = _clave(claves, cfg.proveedor_texto)
    clave_imagen = _clave(claves, cfg.proveedor_imagen)
    clave_eleven = _clave(claves, "elevenlabs")

    if cfg.proveedor_texto == "openrouter" and not clave_texto:
        raise ErrorIA(
            "Falta la clave de OpenRouter. Pégala en el asistente de IA "
            "(menú Ventanas → Asistente de IA)."
        )

    _progreso(al_progreso, 3, "preparando")
    if _cancelado(cancelar):
        raise ErrorIA("Operación cancelada.")

    # 1. Guion -------------------------------------------------------------
    if guion_previo is not None:
        guion = guion_previo
        if al_log:
            al_log("[Guion] Usando el guion existente para preparar el paquete.")
    else:
        _progreso(al_progreso, 8, "generando guion")
        if al_log and cfg.duracion_min >= 30:
            al_log(
                "[Guion] Los guiones de larga duración pueden superar el límite "
                "de salida de algunos modelos; revisa la longitud antes de publicar."
            )
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
            instrucciones_canal=cfg.prompt_canal,
            al_log=al_log,
        )
    if _cancelado(cancelar):
        raise ErrorIA("Operación cancelada.")
    _progreso(al_progreso, 38, "guion listo")
    if al_guion is not None:
        al_guion(guion.texto, guion)

    if guion_previo is not None and cfg.generar_paquete:
        _progreso(al_progreso, 39, "usando guion existente")

    # 2. Metadatos de publicación y prompts visuales -----------------------
    paquete = {}
    if cfg.generar_paquete:
        if _cancelado(cancelar):
            raise ErrorIA("Operación cancelada.")
        _progreso(al_progreso, 40, "preparando paquete de publicación")
        paquete = generar_paquete_editorial(
            idea,
            guion,
            clave=clave_texto,
            modelo=cfg.modelo_texto,
            base_url=cfg.base_url_texto,
            instrucciones_canal=cfg.prompt_canal,
            al_log=al_log,
        )
    ruta_miniatura = ""
    prompt_mini = prompt_de_miniatura(paquete, cfg, idea)
    if prompt_mini:
        paquete["prompt_miniatura"] = prompt_mini
        paquete["prompt_miniatura_propio"] = bool((cfg.prompt_miniatura or "").strip())
    if cfg.generar_paquete and cfg.generar_miniatura and prompt_mini:
        proveedor_miniatura = cfg.proveedor_imagen
        clave_miniatura = clave_imagen
        if proveedor_miniatura == "ninguno":
            proveedor_miniatura = "pollinations"
            clave_miniatura = ""
        elif proveedor_miniatura == "openai" and not clave_miniatura:
            proveedor_miniatura = "pollinations"
            clave_miniatura = ""
        if proveedor_miniatura != "ninguno":
            try:
                if _cancelado(cancelar):
                    raise ErrorIA("Operación cancelada.")
                _progreso(al_progreso, 42, "generando miniatura")
                datos_miniatura = generar_imagen_bytes(
                    prompt_mini,
                    proveedor=proveedor_miniatura,
                    clave=clave_miniatura,
                    modelo=(
                        cfg.modelo_local if proveedor_miniatura == "local"
                        else cfg.modelo_imagen
                    ),
                    cuenta_flow=cfg.cuenta_flow,
                    ancho=1280,
                    alto=720,
                    semilla=2026,
                    **_kw_local(cfg),
                )
                extension = _extension_imagen(datos_miniatura)
                carpeta = Path(carpeta_imagenes)
                carpeta.mkdir(parents=True, exist_ok=True)
                import time as _time
                ruta = carpeta / f"miniatura_{_time.strftime('%Y%m%d_%H%M%S')}{extension}"
                ruta.write_bytes(datos_miniatura)
                ruta_miniatura = str(ruta)
                if al_log:
                    al_log(f"[YouTube] Miniatura guardada: {ruta.name}")
            except Exception as exc:  # noqa: BLE001
                if al_log:
                    al_log(f"[YouTube] No se pudo generar la miniatura: {exc}")
    paquete["miniatura_ruta"] = ruta_miniatura
    if paquete and al_paquete is not None:
        al_paquete(dict(paquete))

    # 3. Imágenes ----------------------------------------------------------
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
                cuenta_flow=cfg.cuenta_flow,
                estilo=cfg.estilo_imagen,
                local=_kw_local(cfg),
                prompts_personalizados=paquete.get("prompts_imagenes"),
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
                    cuenta_flow=cfg.cuenta_flow,
                    estilo=cfg.estilo_imagen,
                    local=_kw_local(cfg),
                    prompts_personalizados=paquete.get("prompts_imagenes"),
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
    resultado = ResultadoIA(
        guion=guion,
        imagenes=imagenes,
        voz=voz,
        marcas=marcas,
        paquete=paquete,
    )
    _progreso(al_progreso, 100, "completado")
    return resultado


def _kw_local(cfg: ConfigIA) -> dict:
    """Argumentos del backend local, vacíos si el proveedor no es local."""
    return {
        "local_backend": cfg.backend_local,
        "local_url": cfg.url_local,
        "local_pasos": cfg.pasos_local,
        "local_workflow": cfg.workflow_local,
    }


def prompt_de_miniatura(paquete: dict, cfg: ConfigIA, idea: str = "") -> str:
    """Prompt final de la miniatura.

    Si el usuario guardó una plantilla propia se usa esa (con los marcadores
    ``{titulo}`` y ``{tema}`` sustituidos); si no, la que propone la IA.
    """
    plantilla = (cfg.prompt_miniatura or "").strip()
    if not plantilla:
        return str((paquete or {}).get("prompt_miniatura") or "").strip()
    titulo = str(
        (paquete or {}).get("titulo")
        or ((paquete or {}).get("titulos") or [""])[0]
        or idea
    ).strip()
    texto = plantilla.replace("{titulo}", titulo).replace("{tema}", idea.strip())
    return " ".join(texto.split())


def _extension_imagen(datos: bytes) -> str:
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if datos.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _clave(claves: dict | None, proveedor: str) -> str:
    if not claves:
        return ""
    return str(claves.get((proveedor or "").lower()) or "")


def _progreso(callback, valor: int, etapa: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(valor))), etapa)
