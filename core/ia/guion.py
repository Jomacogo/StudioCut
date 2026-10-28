"""Generación y análisis de guiones para los videos de Studio Cut.

El guion se escribe en un formato que el detector de capítulos de la app ya
entiende: las secciones empiezan literalmente por «Introducción», «Capítulo
uno», «Capítulo dos»… y «Cierre». Así el TTS lo lee y Vosk encuentra los
cortes sin tocar nada más.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .proveedores import (
    BASE_OPENROUTER,
    MODELO_TEXTO_DEFECTO,
    ErrorIA,
    generar_texto,
)

_NUMEROS_PALABRA = [
    "cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho",
    "nueve", "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis",
    "diecisiete", "dieciocho", "diecinueve", "veinte", "veintiuno",
    "veintidós", "veintitrés", "veinticuatro", "veinticinco", "veintiséis",
    "veintisiete", "veintiocho", "veintinueve", "treinta",
]

_NUMEROS_A_VALOR = {
    "un": 1, "uno": 1, "una": 1, "primer": 1, "primero": 1, "primera": 1,
    "dos": 2, "segundo": 2, "segunda": 2,
    "tres": 3, "tercer": 3, "tercero": 3, "tercera": 3,
    "cuatro": 4, "cuarto": 4, "cuarta": 4,
    "cinco": 5, "quinto": 5, "quinta": 5,
    "seis": 6, "sexto": 6, "sexta": 6,
    "siete": 7, "séptimo": 7, "septimo": 7, "séptima": 7,
    "ocho": 8, "octavo": 8, "octava": 8,
    "nueve": 9, "noveno": 9, "novena": 9,
    "diez": 10, "décimo": 10, "decimo": 10, "décima": 10,
    "once": 11, "doce": 12, "trece": 13, "catorce": 14, "quince": 15,
    "dieciséis": 16, "dieciseis": 16, "diecisiete": 17, "dieciocho": 18,
    "diecinueve": 19, "veinte": 20,
}

_RE_INTRO = re.compile(r"^introducci[oó]n\b[\s:.\-–—]*$", re.IGNORECASE)
_RE_CIERRE = re.compile(
    r"^(cierre|conclusi[oó]n|despedida|final|outro)\b[\s:.\-–—]*$", re.IGNORECASE
)
_RE_CAPITULO = re.compile(
    r"^cap[ií]tulo\s+([a-záéíóúüñ]+|\d{1,3})\s*(?:[:.\-–—]\s*(.*))?$",
    re.IGNORECASE,
)
_RE_CAPITULO_INLINE = re.compile(
    r"(?:^|(?<=[.!?])\s+)(cap[ií]tulo\s+(?:[a-záéíóúüñ]+|\d{1,3})"
    r"|introducci[oó]n|cierre|conclusi[oó]n|despedida)\s*[:.\-–—]?\s*",
    re.IGNORECASE,
)
_RE_PREAMBULO = re.compile(
    r"^(aqu[ií]\s+(tienes|est[aá])|claro[,:]|por supuesto[,:]|"
    r"gu[ií]on\s*:|respuesta\s*:|texto\s*:)",
    re.IGNORECASE,
)
_RE_MARKDOWN = re.compile(r"(\*\*|__|\*|`)")


@dataclass
class Bloque:
    """Una sección del guion: introducción, capítulo o cierre."""

    tipo: str            # "intro" | "capitulo" | "cierre"
    numero: int = 0
    titulo: str = ""
    texto: str = ""

    @property
    def es_intro(self) -> bool:
        return self.tipo == "intro"

    @property
    def es_cierre(self) -> bool:
        return self.tipo == "cierre"

    @property
    def encabezado(self) -> str:
        if self.es_intro:
            return "Introducción."
        if self.es_cierre:
            return "Cierre."
        numero = _NUMEROS_PALABRA[self.numero] if 0 < self.numero < len(_NUMEROS_PALABRA) else str(self.numero)
        titulo = f": {self.titulo}" if self.titulo else "."
        return f"Capítulo {numero}{titulo}"

    def a_dict(self) -> dict:
        return {
            "tipo": self.tipo,
            "numero": self.numero,
            "titulo": self.titulo,
            "texto": self.texto,
        }

    @classmethod
    def desde_dict(cls, datos: dict) -> "Bloque":
        return cls(
            tipo=str(datos.get("tipo") or "capitulo"),
            numero=int(datos.get("numero") or 0),
            titulo=str(datos.get("titulo") or ""),
            texto=str(datos.get("texto") or ""),
        )


@dataclass
class Guion:
    idea: str = ""
    bloques: list[Bloque] = field(default_factory=list)
    texto: str = ""

    @property
    def capitulos(self) -> list[Bloque]:
        return [b for b in self.bloques if b.tipo == "capitulo"]

    @property
    def palabras(self) -> int:
        return len(self.texto.split())

    def bloque(self, tipo: str) -> Bloque | None:
        for b in self.bloques:
            if b.tipo == tipo:
                return b
        return None

    def a_dict(self) -> dict:
        return {
            "idea": self.idea,
            "texto": self.texto,
            "bloques": [b.a_dict() for b in self.bloques],
        }

    @classmethod
    def desde_dict(cls, datos: dict) -> "Guion":
        bloques = [Bloque.desde_dict(b) for b in (datos.get("bloques") or [])]
        return cls(
            idea=str(datos.get("idea") or ""),
            bloques=bloques,
            texto=str(datos.get("texto") or ""),
        )


# ------------------------------------------------------------------ Prompt
def _sistema() -> str:
    return (
        "Eres guionista profesional de videos de YouTube en español. Escribes "
        "texto natural para leer en voz alta: frases claras, sin listas, sin "
        "acotaciones, sin markdown y sin emojis. Sigues el formato pedido al pie "
        "de la letra."
    )


def construir_prompt(
    idea: str,
    *,
    duracion_min: int = 5,
    n_capitulos: int = 3,
    tono: str = "cercano y directo",
    publico: str = "público general",
    idioma: str = "español neutro",
) -> str:
    """Prompt estricto para que el guion encaje con el detector de capítulos."""
    idea = (idea or "").strip()
    n_capitulos = max(1, min(int(n_capitulos or 3), 20))
    duracion_min = max(1, min(int(duracion_min or 5), 120))
    palabras_objetivo = duracion_min * 150
    numeros = ", ".join(_NUMEROS_PALABRA[1: n_capitulos + 1])

    lineas = [
        f"Escribe un guion completo en {idioma} para un video de YouTube de "
        f"unos {duracion_min} minutos (aproximadamente {palabras_objetivo} palabras).",
        f"Tema: {idea}",
        f"Tono: {tono}. Público: {publico}.",
        "",
        "ESTRUCTURA OBLIGATORIA (respétala exactamente):",
        "",
        "Introducción.",
        "Dos o tres frases que presenten el tema y enganchen al espectador.",
        "",
    ]
    for numero in range(1, n_capitulos + 1):
        palabra = _NUMEROS_PALABRA[numero]
        lineas += [
            f"Capítulo {palabra}: Título breve del capítulo.",
            "Cuatro a ocho frases desarrollando la idea, con un ejemplo concreto.",
            "",
        ]
    lineas += [
        "Cierre.",
        "Dos o tres frases que resuman y llamen a la acción.",
        "",
        "REGLAS ESTRICTAS:",
        "- Escribe los números de los capítulos con letra: "
        f"{numeros}.",
        "- Cada encabezado va solo en su línea, tal como se muestra.",
        "- No uses markdown, viñetas, negritas, títulos con #, ni notas entre paréntesis.",
        "- No escribas nada antes de «Introducción.» ni después del cierre.",
        "- No inventes datos médicos, legales ni financieros peligrosos.",
    ]
    return "\n".join(lineas)


# ----------------------------------------------------------------- Parser
def _limpiar_linea(linea: str) -> str:
    texto = linea.strip()
    texto = re.sub(r"^#{1,6}\s*", "", texto)
    texto = _RE_MARKDOWN.sub("", texto)
    return texto.strip()


def _sin_acentos(texto: str) -> str:
    normal = unicodedata.normalize("NFKD", texto)
    return normal.encode("ascii", "ignore").decode("ascii").lower()


def _numero_capitulo(token: str, siguiente: int) -> int:
    token = _sin_acentos(token).strip()
    if token.isdigit():
        return int(token)
    return _NUMEROS_A_VALOR.get(token, siguiente)


def _clasificar_encabezado(linea: str) -> tuple[str, int, str] | None:
    limpia = _limpiar_linea(linea).strip(" .:;")
    if not limpia:
        return None
    if _RE_INTRO.match(limpia):
        return ("intro", 0, "")
    if _RE_CIERRE.match(limpia):
        return ("cierre", 0, "")
    m = _RE_CAPITULO.match(limpia)
    if m:
        numero = _numero_capitulo(m.group(1), 0)
        titulo = _limpiar_linea(m.group(2) or "").strip(" .:;")
        return ("capitulo", numero, titulo)
    return None


def _limpiar_texto_bloque(texto: str) -> str:
    lineas = []
    for linea in texto.replace("\r", "").split("\n"):
        limpia = _limpiar_linea(linea)
        if limpia:
            lineas.append(limpia)
    texto = " ".join(lineas)
    texto = re.sub(r"\s+", " ", texto).strip()
    texto = re.sub(r"^[-–—•*]\s*", "", texto)
    return texto


def parsear_guion(texto: str) -> Guion:
    """Convierte la respuesta del modelo en bloques ordenados."""
    crudo = (texto or "").replace("\r\n", "\n").replace("\r", "\n")
    crudo = re.sub(r"^```[a-zA-Z]*\s*", "", crudo.strip())
    crudo = re.sub(r"\s*```$", "", crudo).strip()
    if not crudo:
        return Guion()

    bloques: list[Bloque] = []
    preambulo: list[str] = []
    actual: Bloque | None = None

    for linea in crudo.split("\n"):
        clasificado = _clasificar_encabezado(linea)
        if clasificado is not None:
            tipo, numero, titulo = clasificado
            actual = Bloque(tipo=tipo, numero=numero, titulo=titulo)
            bloques.append(actual)
            continue
        if actual is None:
            if linea.strip() and not _RE_PREAMBULO.match(_limpiar_linea(linea)):
                preambulo.append(linea)
        else:
            actual.texto += "\n" + linea

    # Respaldo: guiones escritos en un solo párrafo.
    # Se quita el markdown antes de buscar encabezados porque los modelos
    # suelen responder «**Capítulo uno:** …» en la misma línea.
    if len(bloques) < 2:
        alternos = _parsear_inline(_RE_MARKDOWN.sub("", crudo))
        if alternos:
            bloques = alternos

    if not bloques:
        return Guion(texto=_limpiar_texto_bloque(crudo))

    # El texto previo al primer encabezado suele ser relleno del modelo;
    # si es largo, se añade a la introducción para no perder contenido.
    sobrante = _limpiar_texto_bloque("\n".join(preambulo))
    if sobrante and len(sobrante.split()) > 12:
        intro = next((b for b in bloques if b.tipo == "intro"), None)
        if intro is None:
            intro = Bloque(tipo="intro")
            bloques.insert(0, intro)
        intro.texto = (sobrante + " " + intro.texto).strip()

    for bloque in bloques:
        bloque.texto = _limpiar_texto_bloque(bloque.texto)

    guion = Guion(bloques=bloques)
    normalizar_guion(guion)
    return guion


def _parsear_inline(texto: str) -> list[Bloque]:
    coincidencias = list(_RE_CAPITULO_INLINE.finditer(texto))
    if len(coincidencias) < 2:
        return []
    bloques: list[Bloque] = []
    for i, match in enumerate(coincidencias):
        inicio = match.end()
        fin = coincidencias[i + 1].start() if i + 1 < len(coincidencias) else len(texto)
        cuerpo = texto[inicio:fin].strip()
        encabezado = match.group(1)
        clasificado = _clasificar_encabezado(encabezado)
        if clasificado is None:
            continue
        tipo, numero, titulo = clasificado
        bloques.append(Bloque(tipo=tipo, numero=numero, titulo=titulo, texto=cuerpo))
    return bloques


def normalizar_guion(guion: Guion) -> Guion:
    """Ordena intro → capítulos → cierre y renumera los capítulos."""
    intro = next((b for b in guion.bloques if b.tipo == "intro"), None)
    cierre = next((b for b in guion.bloques if b.tipo == "cierre"), None)
    capitulos = [b for b in guion.bloques if b.tipo == "capitulo"]

    ordenados: list[Bloque] = []
    if intro is not None:
        intro.numero = 0
        ordenados.append(intro)
    for i, capitulo in enumerate(capitulos, start=1):
        capitulo.numero = i
        ordenados.append(capitulo)
    if cierre is not None:
        cierre.numero = 0
        ordenados.append(cierre)
    guion.bloques = ordenados
    guion.texto = construir_texto(guion.bloques)
    return guion


def construir_texto(bloques: list[Bloque]) -> str:
    partes = []
    for bloque in bloques:
        if not bloque.texto.strip():
            continue
        partes.append(f"{bloque.encabezado}\n{bloque.texto.strip()}")
    return "\n\n".join(partes).strip()


def validar_guion(guion: Guion) -> list[str]:
    """Devuelve los problemas que impiden usar el guion (lista vacía = OK)."""
    problemas: list[str] = []
    if not guion.bloques:
        return ["el guion está vacío"]
    if guion.bloque("intro") is None:
        problemas.append("falta la introducción")
    if guion.bloque("cierre") is None:
        problemas.append("falta el cierre")
    if not guion.capitulos:
        problemas.append("no hay ningún capítulo")
    for bloque in guion.bloques:
        if len(bloque.texto.split()) < 8:
            etiqueta = bloque.encabezado
            problemas.append(f"{etiqueta} tiene muy poco texto")
    if guion.palabras < 60:
        problemas.append(f"el guion es demasiado corto ({guion.palabras} palabras)")
    return problemas


# -------------------------------------------------------------- Generación
def generar_guion(
    idea: str,
    *,
    clave: str,
    modelo: str = MODELO_TEXTO_DEFECTO,
    base_url: str = BASE_OPENROUTER,
    duracion_min: int = 5,
    n_capitulos: int = 3,
    tono: str = "cercano y directo",
    publico: str = "público general",
    idioma: str = "español neutro",
    al_log=None,
    timeout: float = 240.0,
) -> Guion:
    """Pide el guion al modelo y lo repara una vez si llega mal formado."""
    if not (idea or "").strip():
        raise ErrorIA("Escribe primero el tema o la idea del video.")

    prompt = construir_prompt(
        idea,
        duracion_min=duracion_min,
        n_capitulos=n_capitulos,
        tono=tono,
        publico=publico,
        idioma=idioma,
    )
    mensajes = [
        {"role": "system", "content": _sistema()},
        {"role": "user", "content": prompt},
    ]
    if al_log:
        al_log(f"[IA] Generando guion con {modelo}…")
    respuesta = generar_texto(
        mensajes,
        clave=clave,
        modelo=modelo,
        base_url=base_url,
        temperatura=0.8,
        max_tokens=max(2000, duracion_min * 260),
        timeout=timeout,
        al_log=al_log,
    )
    guion = parsear_guion(respuesta["texto"])
    guion.idea = idea
    problemas = validar_guion(guion)
    if not problemas:
        return guion

    if al_log:
        al_log("[IA] El guion llegó incompleto (" + "; ".join(problemas) + "). Reparando…")
    reparacion = (
        prompt
        + "\n\nTu respuesta anterior fue:\n---\n"
        + respuesta["texto"][:6000]
        + "\n---\n\nReescribe el guion completo corrigiendo estos problemas: "
        + "; ".join(problemas)
        + ". Devuelve solo el guion corregido."
    )
    segunda = generar_texto(
        [{"role": "system", "content": _sistema()},
         {"role": "user", "content": reparacion}],
        clave=clave,
        modelo=modelo,
        base_url=base_url,
        temperatura=0.55,
        max_tokens=max(2000, duracion_min * 260),
        timeout=timeout,
        al_log=al_log,
    )
    guion = parsear_guion(segunda["texto"])
    guion.idea = idea
    problemas = validar_guion(guion)
    if problemas:
        raise ErrorIA(
            "El modelo no devolvió un guion usable: " + "; ".join(problemas)
            + ". Prueba otro modelo o vuelve a intentarlo."
        )
    return guion


# --------------------------------------------------------------- Utilidades
def resumen_para_imagen(bloque: Bloque, maximo: int = 320) -> str:
    """Frase corta que describe la escena de un bloque."""
    texto = bloque.texto.strip()
    if not texto:
        return bloque.titulo or bloque.encabezado
    # Primera o segunda frase, la que tenga más sustancia.
    frases = re.split(r"(?<=[.!?])\s+", texto)
    elegida = frases[0]
    if len(elegida.split()) < 8 and len(frases) > 1:
        elegida = frases[1]
    return elegida[:maximo].strip()


def marcas_estimadas(guion: Guion, duracion: float) -> list[dict]:
    """Marcas de capítulo aproximadas repartiendo por número de palabras.

    Sirve de respaldo cuando Vosk no reconoce bien los encabezados. El audio
    del TTS es bastante lineal, así que la estimación es razonable.
    """
    duracion = max(1.0, float(duracion or 0.0))
    bloques = [b for b in guion.bloques if b.texto.strip()]
    if not bloques:
        return []
    pesos = [max(1, len(b.texto.split())) for b in bloques]
    total = sum(pesos)
    marcas: list[dict] = []
    acumulado = 0
    for bloque, peso in zip(bloques, pesos):
        inicio = duracion * acumulado / total
        if bloque.tipo == "intro":
            titulo = "Introducción"
        elif bloque.tipo == "cierre":
            titulo = "Cierre"
        else:
            titulo = f"Capítulo {bloque.numero}"
        marcas.append({"titulo": titulo, "tiempo": round(inicio, 2)})
        acumulado += peso
    # La intro siempre empieza en 0.
    if marcas and marcas[0]["titulo"] == "Introducción":
        marcas[0]["tiempo"] = 0.0
    return marcas
