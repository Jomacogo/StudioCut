"""Ordenamiento inteligente de audios/imágenes por nombre de archivo.

Sin dependencias de Qt: lógica pura reutilizable y testeable.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

# Palabras que indican la introducción (van primero, orden -1).
# Se usan lookarounds en vez de \b para que también funcionen con guiones
# bajos: "intro_20260101_120000" debe clasificarse como introducción.
_INTRO_PATRON = re.compile(
    r"(?<![a-z0-9])(intro|intro\d*|introduc\w*|prologo|prefacio|presentacion|incipit)(?![a-z0-9])"
)

# Palabras que indican el cierre (van al final)
_CIERRE_PATRON = re.compile(
    r"(?<![a-z0-9])(cierre|outro|final|fin|despedida|conclusion|epilogo)(?![a-z0-9])"
)

# Extrae número de capítulo: "capítulo 3", "capitulo3", "cap_03", "3"
_CAPITULO_NUM = re.compile(
    r"(?:cap(?:i|í)tulo|capitulo|cap|chapter|ch)[_\s.-]*(\d+)", re.IGNORECASE
)
# Número suelto si no hay texto de capítulo
_NUMERO_SUELTO = re.compile(r"^[_\s]*(\d+)[_\s]*$")


def _normalizar(nombre: str) -> str:
    """Minúsculas, sin acentos: 'Capítulo_02' -> 'capitulo_02'."""
    texto = unicodedata.normalize("NFKD", nombre)
    texto = texto.encode("ascii", "ignore").decode("ascii")
    return texto.lower()


def _clave_natural(nombre: str) -> tuple:
    """Clave de orden natural: 'cap10' < 'cap2' se ordena como 2, 10."""
    partes = re.split(r"(\d+)", nombre)
    return tuple(int(p) if p.isdigit() else p for p in partes)


def _clasificar_imagen(ruta: str | Path) -> tuple[str, int]:
    """Clasifica una imagen por nombre de archivo.

    Retorna:
        ("intro", 0)          si es introducción
        ("capitulo", N)       si contiene capítulo N (N >= 1)
        ("cierre", 0)         si es cierre/outro
        ("otro", 0)           si no se reconoce
    """
    nombre = _normalizar(Path(str(ruta)).stem)

    if _INTRO_PATRON.search(nombre):
        return ("intro", 0)

    if _CIERRE_PATRON.search(nombre):
        return ("cierre", 0)

    m = _CAPITULO_NUM.search(nombre)
    if m:
        return ("capitulo", int(m.group(1)))

    # Si el nombre es solo un número: "1", "02", etc.
    m = _NUMERO_SUELTO.search(nombre)
    if m:
        return ("capitulo", int(m.group(1)))

    return ("otro", 0)


def clave_audio(ruta: str | Path) -> tuple:
    """Clave de orden para un audio.

    (prioridad, clave_natural)
    - prioridad -1: intro / prólogo / introducción / presentación
    - prioridad  1: capitulos ordenados por número natural si lo tienen,
                    si no por orden alfabético-natural.
    """
    nombre = _normalizar(Path(str(ruta)).stem)
    if _INTRO_PATRON.search(nombre):
        return (-1, _clave_natural(nombre))
    return (1, _clave_natural(nombre))


def ordenar_audios(rutas: list[str] | list[Path]) -> list[str]:
    """Devuelve las rutas de audio ordenadas: intro primero, luego capítulos."""
    items = [(clave_audio(r), str(r)) for r in rutas]
    items.sort(key=lambda par: par[0])
    return [ruta for _clave, ruta in items]


def clave_imagen(ruta: str | Path) -> tuple:
    """Clave de orden para imágenes.

    - prioridad -1: introducción / intro / prólogo (siempre primera)
    - prioridad  1: capítulos con número explícito (capítulo 1, 2, 3...)
    - prioridad  2: otras imágenes en orden natural
    - prioridad 999: cierre / outro / despedida / final (siempre última)
    """
    tipo, num = _clasificar_imagen(ruta)
    nombre = _normalizar(Path(str(ruta)).stem)
    if tipo == "intro":
        return (-1, 0, _clave_natural(nombre))
    if tipo == "cierre":
        return (999, 0, _clave_natural(nombre))
    if tipo == "capitulo" and num > 0:
        return (1, num, _clave_natural(nombre))
    return (2, 0, _clave_natural(nombre))


def ordenar_imagenes(rutas: list[str] | list[Path]) -> list[str]:
    """Devuelve las imágenes ordenadas: intro primero, capítulos en medio, cierre al final."""
    items = [(clave_imagen(r), str(r)) for r in rutas]
    items.sort(key=lambda par: par[0])
    return [ruta for _clave, ruta in items]


def emparejar_imagenes(
    audios: list[str], imagenes: list[str]
) -> list[str | None]:
    """Empareja cada audio con una imagen.

    1º intento: coincidencia de número/intro en el nombre (intro→intro,
    cap 2→cap 2). 2º intento: posición (imagen i va con audio i).
    Puede devolver None si faltan imágenes.
    """
    if not imagenes:
        return [None] * len(audios)

    # Agrupa imágenes por clave de "capítulo" o intro.
    por_clave: dict[tuple, list[str]] = {}
    for img in imagenes:
        por_clave.setdefault(clave_imagen(img), []).append(img)
    for lista in por_clave.values():
        lista.sort()

    imagenes_ordenadas = sorted(imagenes, key=clave_imagen)
    restantes = list(imagenes_ordenadas)
    resultado: list[str | None] = []

    for audio in audios:
        clave = clave_audio(audio)
        asignada: str | None = None
        if clave in por_clave and por_clave[clave]:
            asignada = por_clave[clave].pop(0)
            restantes.remove(asignada)
        resultado.append(asignada)

    # Rellena los audios sin imagen usando las restantes por posición.
    restantes_iter = iter(restantes)
    for i, asignada in enumerate(resultado):
        if asignada is None:
            resultado[i] = next(restantes_iter, None)
    return resultado


def emparejar_imagenes_por_capitulo(
    marcas: list[dict], imagenes: list[str]
) -> dict[int, str]:
    """Empareja imágenes con marcas de capítulo por nombre de archivo y posición.

    Reglas automáticas:
    - Imagen 'introducción' / 'intro' -> primera marca (o marca con título Introducción)
    - Imagen 'capítulo N' -> marca del Capítulo N
    - Imagen 'cierre' / 'outro' -> última marca (o marca con título Cierre)
    - Imágenes restantes -> se asignan a las marcas libres según el orden natural

    Args:
        marcas: Lista de marcas [{"titulo": str, "tiempo": float}, ...]
        imagenes: Lista de rutas de imágenes disponibles

    Returns:
        Diccionario {indice_marca: ruta_imagen}
    """
    if not marcas or not imagenes:
        return {}

    n_marcas = len(marcas)
    asignaciones: dict[int, str] = {}
    usadas: set[str] = set()

    # Clasificar imágenes
    img_intro: list[str] = []
    img_cierre: list[str] = []
    img_capitulo: dict[int, list[str]] = {}
    img_otros: list[str] = []

    # Lista ordenada globalmente (intro primero, caps ordenados, cierre al final)
    imagenes_ordenadas = ordenar_imagenes(imagenes)

    for ruta in imagenes_ordenadas:
        tipo, num = _clasificar_imagen(ruta)
        if tipo == "intro":
            img_intro.append(ruta)
        elif tipo == "cierre":
            img_cierre.append(ruta)
        elif tipo == "capitulo":
            img_capitulo.setdefault(num, []).append(ruta)
        else:
            img_otros.append(ruta)

    # 1. Emparejar Intro
    if img_intro:
        idx_intro = 0
        for i, m in enumerate(marcas):
            if _INTRO_PATRON.search(_normalizar(m.get("titulo", ""))):
                idx_intro = i
                break
        asignaciones[idx_intro] = img_intro[0]
        usadas.add(img_intro[0])

    # 2. Emparejar Cierre
    if img_cierre and n_marcas > 1:
        idx_cierre = n_marcas - 1
        for i in reversed(range(n_marcas)):
            if _CIERRE_PATRON.search(_normalizar(marcas[i].get("titulo", ""))):
                idx_cierre = i
                break
        if idx_cierre not in asignaciones:
            asignaciones[idx_cierre] = img_cierre[0]
            usadas.add(img_cierre[0])

    # 3. Emparejar Capítulos por número
    for num_cap, rutas_cap in sorted(img_capitulo.items()):
        if not rutas_cap:
            continue
        # Buscar marca que contenga este número
        idx_marca = None
        for i, m in enumerate(marcas):
            t_norm = _normalizar(m.get("titulo", ""))
            m_cap = _CAPITULO_NUM.search(t_norm) or _NUMERO_SUELTO.search(t_norm)
            if m_cap and int(m_cap.group(1)) == num_cap:
                idx_marca = i
                break
        if idx_marca is None:
            # Fallback a posición
            tiene_intro_marca = any(_INTRO_PATRON.search(_normalizar(m.get("titulo", ""))) for m in marcas)
            idx_marca = num_cap if tiene_intro_marca else num_cap - 1

        if idx_marca is not None and 0 <= idx_marca < n_marcas and idx_marca not in asignaciones:
            asignaciones[idx_marca] = rutas_cap[0]
            usadas.add(rutas_cap[0])

    # 4. Rellenar marcas vacías con imágenes restantes en orden
    sin_usar = [r for r in imagenes_ordenadas if r not in usadas]
    sin_usar_iter = iter(sin_usar)

    for idx in range(n_marcas):
        if idx not in asignaciones:
            siguiente = next(sin_usar_iter, None)
            if siguiente:
                asignaciones[idx] = siguiente
                usadas.add(siguiente)

    return asignaciones


