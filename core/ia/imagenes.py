"""Generación de las imágenes de cada capítulo."""
from __future__ import annotations

import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    cuenta_flow: str = "",
    local: dict | None = None,
    prompts_personalizados: list[dict] | None = None,
    al_progreso=None,
    al_log=None,
    cancelar=None,
    max_paralelo: int | None = None,
) -> list[str]:
    """Genera una imagen por bloque y devuelve las rutas en orden.

    Las imágenes se generan en paralelo: cada una tarda varios segundos y de
    forma secuencial un guion de 10 capítulos podía superar los 5 minutos.
    El orden de las rutas devueltas sigue siendo el de los bloques.
    """
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    bloques = [b for b in guion.bloques if b.texto.strip()]
    if not bloques:
        raise ErrorIA("El guion no tiene secciones para ilustrar.")

    sello = time.strftime("%Y%m%d_%H%M%S")
    trabajadores = _paralelo(proveedor, max_paralelo)
    candado = threading.Lock()

    def _log(mensaje: str) -> None:
        if al_log is not None:
            with candado:
                al_log(mensaje)

    def _avance(valor: int, etiqueta: str) -> None:
        if al_progreso is not None:
            with candado:
                al_progreso(valor, etiqueta)

    # Prompts ya resueltos y aviso de inicio (en orden de capítulo)
    prompts: list[str] = []
    for i, bloque in enumerate(bloques):
        prompt = ""
        if prompts_personalizados and i < len(prompts_personalizados):
            entrada = prompts_personalizados[i]
            if isinstance(entrada, dict):
                prompt = str(entrada.get("prompt") or "").strip()
            elif isinstance(entrada, str):
                prompt = entrada.strip()
        if not prompt:
            prompt = construir_prompt_imagen(
                bloque, tema=guion.idea, estilo=estilo, ancho=ancho, alto=alto
            )
        prompts.append(prompt)
        _log(f"[Imágenes] {bloque.encabezado} → {proveedor}")

    if trabajadores > 1:
        _log(
            f"[Imágenes] Generando {len(bloques)} imágenes en paralelo "
            f"({trabajadores} a la vez)…"
        )

    def _generar_una(indice: int, prompt: str) -> tuple[int, str, str]:
        if cancelar is not None and cancelar.is_set():
            return indice, "", "cancelado"
        bloque = bloques[indice]
        datos: bytes | None = None
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
                    semilla=1000 + indice,
                    cuenta_flow=cuenta_flow,
                    local_backend=str((local or {}).get("backend") or ""),
                    local_url=str((local or {}).get("url") or ""),
                    local_pasos=int((local or {}).get("pasos") or 0),
                    local_workflow=str((local or {}).get("workflow") or ""),
                    negativo=str((local or {}).get("negativo") or ""),
                )
                if len(datos) < 1024:
                    raise ErrorIA("la imagen llegó vacía")
                break
            except ErrorIA as exc:
                ultimo_error = str(exc)
                espera = _espera_limite(ultimo_error)
                if (proveedor == "pollinations" and espera is not None
                        and intento < 2):
                    _log(
                        f"[Imágenes] Límite gratuito de Pollinations: "
                        f"esperando {espera} s antes de reintentar…"
                    )
                    if _dormir(espera, cancelar):
                        return indice, "", "cancelado"
                    continue
                break

        if datos is None:
            return indice, "", ultimo_error
        ruta = carpeta / (_nombre_archivo(bloque, sello) + _extension(datos))
        ruta.write_bytes(datos)
        _log(f"[Imágenes] Guardada {ruta.name} ({len(datos) // 1024} KB)")
        return indice, str(ruta), ""

    resultados: dict[int, str] = {}
    errores: list[str] = []
    cancelada = False
    completadas = 0
    total = len(bloques)
    pool = ThreadPoolExecutor(max_workers=trabajadores, thread_name_prefix="img-ia")
    try:
        futuros = {
            pool.submit(_generar_una, i, prompts[i]): i for i in range(total)
        }
        for futuro in as_completed(futuros):
            if cancelar is not None and cancelar.is_set():
                cancelada = True
                for pendiente in futuros:
                    pendiente.cancel()
                break
            indice = futuros[futuro]
            try:
                _, ruta, error = futuro.result()
            except Exception as exc:  # noqa: BLE001 - un fallo no debe tumbar el resto
                ruta, error = "", str(exc)
            completadas += 1
            if ruta:
                resultados[indice] = ruta
            elif error == "cancelado":
                cancelada = True
                for pendiente in futuros:
                    pendiente.cancel()
                break
            else:
                errores.append(f"{bloques[indice].encabezado}: {error}")
                _log(f"[Imágenes] Falló {bloques[indice].encabezado}: {error}")
            _avance(
                int(completadas * 100 / total),
                f"{completadas}/{total} listas",
            )
    finally:
        pool.shutdown(wait=not cancelada, cancel_futures=True)

    if cancelada:
        raise ErrorIA("Generación de imágenes cancelada.")

    rutas = [resultados[i] for i in sorted(resultados)]
    if not rutas:
        detalle = "\n".join(errores[-4:]) if errores else "sin detalle"
        raise ErrorIA("No se pudo generar ninguna imagen.\n" + detalle)
    if errores:
        _log(f"[Imágenes] {len(errores)} imagen(es) fallaron; se usarán las demás.")
    _avance(100, "imágenes listas")
    return rutas


def _paralelo(proveedor: str, solicitado: int | None) -> int:
    """Número de imágenes simultáneas según el proveedor.

    Pollinations (gratis) corta si se le piden demasiadas a la vez, así que
    se queda en 2; los proveedores de pago suelen admitir 3-4 sin problemas.
    La IA local va siempre de una en una: comparte una sola GPU y lanzar dos
    trabajos a la vez solo consigue quedarse sin VRAM.
    """
    prov = (proveedor or "").strip().lower()
    if prov in {"local", "ia_local", "comfyui", "forge", "a1111"}:
        return 1
    if solicitado is not None:
        return max(1, min(8, int(solicitado)))
    if prov == "pollinations":
        return 2
    if prov in {"useapi", "google_flow", "google-flow"}:
        return 3
    return 3


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
