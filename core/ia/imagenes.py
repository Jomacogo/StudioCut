"""Generación de las imágenes de cada capítulo."""
from __future__ import annotations

import re
import time
import unicodedata
from pathlib import Path

from .guion import Bloque, Guion, resumen_para_imagen
from .proveedores import ErrorIA, generar_imagen_bytes

ESTILOS = {
    "cinematografico": (
        "fotografía cinematográfica hiperrealista, iluminación dramática, "
        "lente de 35 mm, profundidad de campo, colores ricos"
    ),
    "ilustracion": (
        "ilustración digital editorial, colores vivos, líneas limpias, "
        "estilo moderno de revista"
    ),
    "3d": (
        "render 3D estilo animación moderna, iluminación suave, materiales "
        "detallados, aspecto amigable"
    ),
    "minimalista": (
        "diseño minimalista, fondo limpio, formas geométricas, mucho espacio "
        "negativo, paleta reducida"
    ),
    "acuarela": (
        "ilustración en acuarela, textura de papel, tonos suaves, trazos "
        "artísticos"
    ),
    "futurista": (
        "concepto futurista, tecnología y luz neón, atmósfera sci-fi, "
        "composición épica"
    ),
}

_ESTILO_DEFECTO = "cinematografico"


def _sin_acentos(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto)
    return normal.encode("ascii", "ignore").decode("ascii")


def construir_prompt_imagen(
    bloque: Bloque,
    *,
    tema: str = "",
    estilo: str = _ESTILO_DEFECTO,
    ancho: int = 1920,
    alto: int = 1080,
    con_personas: bool = True,
) -> str:
    """Prompt visual coherente para todos los capítulos."""
    clave_estilo = (estilo or _ESTILO_DEFECTO).strip().lower()
    descripcion_estilo = ESTILOS.get(clave_estilo, ESTILOS[_ESTILO_DEFECTO])
    escena = resumen_para_imagen(bloque)
    tema = (tema or "").strip()
    encuadre = "horizontal 16:9" if ancho >= alto else "vertical 9:16"

    partes = [
        descripcion_estilo,
        f"Escena principal: {escena}",
    ]
    if tema:
        partes.append(f"Forma parte de un video sobre: {tema}")
    if bloque.tipo == "intro":
        partes.append("Imagen de portada atractiva que resume el tema")
    elif bloque.tipo == "cierre":
        partes.append("Imagen de cierre tranquila y esperanzadora")
    else:
        partes.append(f"Ilustra el capítulo «{bloque.titulo or bloque.numero}»")
    if con_personas:
        partes.append(
            "Si aparecen personas, que sean diversas y naturales, mirando a cámara "
            "o realizando la acción descrita"
        )
    partes.append(f"Composición {encuadre}")
    partes.append(
        "Sin texto, sin letras, sin logotipos, sin marcas de agua, sin bordes"
    )
    return ". ".join(p.strip(" .") for p in partes if p.strip()) + "."


def _extension(datos: bytes) -> str:
    if datos.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if datos.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return ".webp"
    return ".png"


def _nombre_archivo(bloque: Bloque, sello: str) -> str:
    if bloque.tipo == "intro":
        base = "intro"
    elif bloque.tipo == "cierre":
        base = "cierre"
    else:
        base = f"capitulo_{bloque.numero:02d}"
    return f"{base}_{sello}"


def generar_imagenes(
    guion: Guion,
    carpeta: str | Path,
    *,
    proveedor: str = "openrouter",
    clave: str = "",
    modelo: str = "",
    ancho: int = 1920,
    alto: int = 1080,
    estilo: str = _ESTILO_DEFECTO,
    al_progreso=None,
    al_log=None,
    cancelar=None,
) -> list[str]:
    """Genera una imagen por bloque y devuelve las rutas en orden."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    bloques = [b for b in guion.bloques if b.texto.strip()]
    if not bloques:
        raise ErrorIA("El guion no tiene secciones para ilustrar.")

    sello = time.strftime("%Y%m%d_%H%M%S")
    rutas: list[str] = []
    errores: list[str] = []

    for i, bloque in enumerate(bloques):
        if cancelar is not None and cancelar.is_set():
            raise ErrorIA("Generación de imágenes cancelada.")
        if al_progreso:
            al_progreso(int(i * 100 / len(bloques)), bloque.encabezado)
        prompt = construir_prompt_imagen(
            bloque, tema=guion.idea, estilo=estilo, ancho=ancho, alto=alto
        )
        if al_log:
            al_log(f"[Imágenes] {bloque.encabezado} → {proveedor}")
        datos = None
        ultimo_error = ""
        for intento in range(3):
            try:
                datos = generar_imagen_bytes(
                    prompt,
                    proveedor=proveedor,
                    clave=clave,
                    modelo=modelo,
                    ancho=ancho,
                    alto=alto,
                    semilla=1000 + i,
                )
                if len(datos) < 1024:
                    raise ErrorIA("la imagen llegó vacía")
                break
            except ErrorIA as exc:
                ultimo_error = str(exc)
                espera = _espera_limite(ultimo_error)
                if (proveedor == "pollinations" and espera is not None
                        and intento < 2):
                    if al_log:
                        al_log(
                            f"[Imágenes] Límite gratuito de Pollinations: "
                            f"esperando {espera} s antes de reintentar…"
                        )
                    if _dormir(espera, cancelar):
                        raise ErrorIA("Generación de imágenes cancelada.")
                    continue
                break

        if datos is None:
            errores.append(f"{bloque.encabezado}: {ultimo_error}")
            if al_log:
                al_log(f"[Imágenes] Falló {bloque.encabezado}: {ultimo_error}")
            continue

        ruta = carpeta / (_nombre_archivo(bloque, sello) + _extension(datos))
        ruta.write_bytes(datos)
        rutas.append(str(ruta))
        if al_log:
            al_log(f"[Imágenes] Guardada {ruta.name} ({len(datos) // 1024} KB)")

    if not rutas:
        detalle = "\n".join(errores[-4:]) if errores else "sin detalle"
        raise ErrorIA("No se pudo generar ninguna imagen.\n" + detalle)
    if errores and al_log:
        al_log(f"[Imágenes] {len(errores)} imagen(es) fallaron; se usarán las demás.")
    if al_progreso:
        al_progreso(100, "imágenes listas")
    return rutas


def _espera_limite(mensaje: str) -> float | None:
    """Segundos de espera si el proveedor avisa de un límite gratuito."""
    texto = (mensaje or "").lower()
    if "free_limit" not in texto and "retry after" not in texto and "límite" not in texto:
        return None
    m = re.search(r"retry after\s+(\d+)\s*s", texto)
    if not m:
        m = re.search(r'"retry_after"\s*:\s*(\d+)', texto)
    if not m:
        m = re.search(r"(\d+)\s*(?:s|segundos)", texto)
    try:
        return max(5.0, min(120.0, float(m.group(1)) if m else 45.0))
    except (TypeError, ValueError):
        return 45.0


def _dormir(segundos: float, cancelar) -> bool:
    """Duerme en tramos de 1 s. Devuelve True si se canceló."""
    restante = float(segundos)
    while restante > 0:
        if cancelar is not None and cancelar.is_set():
            return True
        tramo = min(1.0, restante)
        time.sleep(tramo)
        restante -= tramo
    return False


def nombre_seguro(texto: str, maximo: int = 40) -> str:
    """Nombre de archivo sin acentos ni caracteres problemáticos."""
    base = _sin_acentos(texto or "").lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return (base[:maximo] or "imagen").strip("_")
