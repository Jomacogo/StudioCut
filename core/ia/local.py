"""Generación de imágenes con una IA instalada en el propio equipo.

Dos backends, ambos por HTTP local y sin añadir dependencias a Studio Cut:

* **ComfyUI** (``http://127.0.0.1:8188``). Se encola un workflow en formato
  *API*, se espera a que termine consultando ``/history/{prompt_id}`` y se
  descarga el PNG con ``/view``. Admite un workflow propio exportado por el
  usuario (por ejemplo uno con Flux GGUF), al que se le inyectan prompt,
  tamaño, semilla y pasos.
* **Forge / AUTOMATIC1111** (``http://127.0.0.1:7860``). Se usa
  ``/sdapi/v1/txt2img``, que devuelve la imagen en base64.

Nada de esto descarga modelos: el usuario instala el backend y los modelos
siguiendo la guía de la app. Si no hay servidor escuchando, se devuelve un
error claro en vez de intentar arrancarlo.
"""
from __future__ import annotations

import base64
import copy
import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from .proveedores import ErrorIA, _descargar, peticion_json

URL_COMFYUI = "http://127.0.0.1:8188"
URL_FORGE = "http://127.0.0.1:7860"

# Puertos donde suele escuchar cada backend, por si el usuario lo movió.
PUERTOS_COMFYUI = (8188, 8189)
PUERTOS_FORGE = (7860, 7861)

BACKENDS = {
    "comfyui": "ComfyUI",
    "forge": "Forge / AUTOMATIC1111",
}

# Workflow mínimo de ComfyUI en formato API. Sirve para checkpoints completos
# (SDXL, SD1.5, flux1-schnell-fp8…) cargados con CheckpointLoaderSimple.
_WORKFLOW_BASE = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": ""},
    },
    "2": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["1", 1], "text": ""},
    },
    "3": {
        "class_type": "CLIPTextEncode",
        "inputs": {"clip": ["1", 1], "text": ""},
    },
    "4": {
        "class_type": "EmptyLatentImage",
        "inputs": {"batch_size": 1, "height": 720, "width": 1280},
    },
    "5": {
        "class_type": "KSampler",
        "inputs": {
            "cfg": 7.0,
            "denoise": 1.0,
            "latent_image": ["4", 0],
            "model": ["1", 0],
            "negative": ["3", 0],
            "positive": ["2", 0],
            "sampler_name": "euler",
            "scheduler": "normal",
            "seed": 0,
            "steps": 25,
        },
    },
    "6": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
    },
    "7": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "StudioCut", "images": ["6", 0]},
    },
}

_NEGATIVO_DEFECTO = (
    "texto, letras, marcas de agua, logotipos, firmas, bordes, marcos, "
    "deformidades, manos raras, baja calidad, borroso, ruido, recortado"
)


# ------------------------------------------------------------------ helpers
def _normalizar_url(url: str, backend: str) -> str:
    url = (url or "").strip().rstrip("/")
    if not url:
        return URL_COMFYUI if backend == "comfyui" else URL_FORGE
    if not url.lower().startswith(("http://", "https://")):
        url = "http://" + url
    return url


def _get_json(url: str, timeout: float = 15.0) -> dict:
    datos = peticion_json(url, timeout=timeout)
    return datos if isinstance(datos, dict) else {}


def _es_local(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}


def _error_conexion(url: str, backend: str, exc: Exception) -> ErrorIA:
    nombre = BACKENDS.get(backend, backend)
    return ErrorIA(
        f"No hay respuesta de {nombre} en {url}.\n"
        f"Abre {nombre} en tu equipo y vuelve a intentarlo "
        f"(detalle: {exc})."
    )


# --------------------------------------------------------------- estado
def estado_backend(
    backend: str = "comfyui", url: str = "", timeout: float = 6.0
) -> dict:
    """Comprueba si el backend está escuchando y qué modelos ofrece.

    Devuelve ``{disponible, backend, url, modelos, mensaje}``. Nunca lanza.
    """
    backend = (backend or "comfyui").strip().lower()
    if backend not in BACKENDS:
        return {
            "disponible": False,
            "backend": backend,
            "url": url,
            "modelos": [],
            "mensaje": f"Backend local desconocido: {backend}",
        }
    base = _normalizar_url(url, backend)
    try:
        if backend == "comfyui":
            datos = _get_json(base + "/system_stats", timeout=timeout)
            dispositivos = datos.get("devices") or []
            equipo = ""
            if dispositivos:
                d = dispositivos[0] or {}
                nombre = d.get("name") or ""
                vram = d.get("vram_total") or 0
                if vram:
                    equipo = f"{nombre} · {float(vram) / 1024 ** 3:.1f} GB VRAM".strip()
                elif nombre:
                    equipo = str(nombre)
            modelos = listar_modelos(backend, base, timeout=timeout)
            mensaje = "ComfyUI conectado"
            if equipo:
                mensaje += f" ({equipo})"
            if not modelos:
                mensaje += ". Aviso: no hay checkpoints en models/checkpoints."
            return {
                "disponible": True,
                "backend": backend,
                "url": base,
                "modelos": modelos,
                "mensaje": mensaje,
            }
        opciones = _get_json(base + "/sdapi/v1/options", timeout=timeout)
        actual = str(opciones.get("sd_model_checkpoint") or "")
        modelos = listar_modelos(backend, base, timeout=timeout)
        mensaje = "Forge / A1111 conectado"
        if actual:
            mensaje += f" ({actual})"
        return {
            "disponible": True,
            "backend": backend,
            "url": base,
            "modelos": modelos,
            "mensaje": mensaje,
        }
    except ErrorIA as exc:
        return {
            "disponible": False,
            "backend": backend,
            "url": base,
            "modelos": [],
            "mensaje": str(exc),
        }
    except Exception as exc:  # noqa: BLE001 - nunca debe tumbar la UI
        return {
            "disponible": False,
            "backend": backend,
            "url": base,
            "modelos": [],
            "mensaje": str(exc),
        }


def detectar_servidores(timeout: float = 2.5) -> list[dict]:
    """Prueba los puertos habituales de ComfyUI y Forge."""
    encontrados: list[dict] = []
    for puerto in PUERTOS_COMFYUI:
        estado = estado_backend("comfyui", f"http://127.0.0.1:{puerto}", timeout)
        if estado["disponible"]:
            encontrados.append(estado)
    for puerto in PUERTOS_FORGE:
        estado = estado_backend("forge", f"http://127.0.0.1:{puerto}", timeout)
        if estado["disponible"]:
            encontrados.append(estado)
    return encontrados


def listar_modelos(
    backend: str = "comfyui", url: str = "", timeout: float = 10.0
) -> list[str]:
    """Modelos disponibles en el backend (checkpoints o modelos SD)."""
    backend = (backend or "comfyui").strip().lower()
    base = _normalizar_url(url, backend)
    try:
        if backend == "comfyui":
            datos = _get_json(base + "/object_info/CheckpointLoaderSimple", timeout)
            info = datos.get("CheckpointLoaderSimple") or {}
            requerido = ((info.get("input") or {}).get("required") or {})
            entrada = requerido.get("ckpt_name") or []
            if entrada and isinstance(entrada[0], list):
                return [str(n) for n in entrada[0]]
            return []
        # Este endpoint devuelve una lista, no un objeto.
        datos = peticion_json(base + "/sdapi/v1/sd-models", timeout=timeout)
        if isinstance(datos, list):
            return [
                str(m.get("title") or m.get("model_name") or "")
                for m in datos
                if isinstance(m, dict)
            ]
    except (ErrorIA, ValueError):
        return []
    return []


# ------------------------------------------------------------- ComfyUI
def ajustes_para_modelo(modelo: str, pasos: int) -> dict:
    """CFG, pasos y muestreador que pide cada familia de modelo.

    Un SDXL-Turbo con CFG 7 y 25 pasos sale quemado y tarda 6 veces más de la
    cuenta; un Lightning quiere CFG 2. Por eso los valores del formulario se
    recortan según el nombre del checkpoint.
    """
    nombre = (modelo or "").lower()
    if "flux" in nombre:
        return {
            "cfg": 1.0,
            "steps": max(1, min(int(pasos), 8)),
            "sampler_name": "euler",
            "scheduler": "simple",
        }
    if "turbo" in nombre:
        return {
            "cfg": 1.0,
            "steps": max(1, min(int(pasos), 8)),
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
        }
    if any(marca in nombre for marca in ("lightning", "lcm", "hyper", "dmd")):
        return {
            "cfg": 2.0,
            "steps": max(2, min(int(pasos), 10)),
            "sampler_name": "euler",
            "scheduler": "sgm_uniform",
        }
    return {"steps": max(8, int(pasos))}


def _workflow_comfy(
    prompt: str,
    *,
    ancho: int,
    alto: int,
    modelo: str,
    pasos: int,
    semilla: int,
    negativo: str,
    workflow: dict | None = None,
) -> dict:
    """Arma el workflow (o parchea el del usuario) con los datos actuales."""
    if workflow:
        wf = copy.deepcopy(workflow)
        _parchear_workflow(wf, prompt, ancho, alto, pasos, semilla, negativo)
        return wf

    wf = copy.deepcopy(_WORKFLOW_BASE)
    if modelo:
        wf["1"]["inputs"]["ckpt_name"] = modelo
    wf["2"]["inputs"]["text"] = prompt
    wf["3"]["inputs"]["text"] = negativo or _NEGATIVO_DEFECTO
    wf["4"]["inputs"].update({"width": int(ancho), "height": int(alto)})
    wf["5"]["inputs"].update({"seed": int(semilla), "steps": int(pasos)})
    wf["5"]["inputs"].update(ajustes_para_modelo(modelo, int(pasos)))
    return wf


def _parchear_workflow(
    wf: dict, prompt: str, ancho: int, alto: int, pasos: int, semilla: int,
    negativo: str,
) -> None:
    """Inyecta texto, tamaño, semilla y pasos en un workflow cualquiera."""
    clips: list[tuple[str, dict]] = []
    for clave, nodo in wf.items():
        if not isinstance(nodo, dict):
            continue
        clase = str(nodo.get("class_type") or "")
        entradas = nodo.get("inputs")
        if not isinstance(entradas, dict):
            continue
        if clase in {"CLIPTextEncode", "CLIPTextEncodeSDXL", "BNK_CLIPTextEncodeAdvanced"}:
            clips.append((clave, entradas))
        elif "width" in entradas and "height" in entradas:
            entradas["width"] = int(ancho)
            entradas["height"] = int(alto)
        elif clase in {"KSampler", "KSamplerAdvanced"}:
            if "seed" in entradas:
                entradas["seed"] = int(semilla)
            if "noise_seed" in entradas:
                entradas["noise_seed"] = int(semilla)
            if "steps" in entradas:
                entradas["steps"] = int(pasos)

    # El primer nodo de texto es el positivo y el segundo el negativo.
    clips.sort(key=lambda par: (len(par[0]), par[0]))
    if clips:
        clips[0][1]["text"] = prompt
    if len(clips) > 1:
        clips[1][1]["text"] = negativo or _NEGATIVO_DEFECTO


def cargar_workflow(ruta: str) -> dict:
    """Lee un workflow exportado de ComfyUI (formato API)."""
    try:
        datos = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ErrorIA(f"No se pudo leer el workflow {ruta}: {exc}") from exc
    if not isinstance(datos, dict):
        raise ErrorIA("El workflow no tiene el formato API de ComfyUI.")
    # Un workflow guardado desde la interfaz trae {"nodes": [...], "links": [...]}
    if "nodes" in datos and "links" in datos:
        raise ErrorIA(
            "Ese archivo es un workflow de la interfaz. En ComfyUI usa "
            "«Workflow → Export (API)» y selecciona el JSON exportado."
        )
    return datos


def generar_comfyui(
    prompt: str,
    *,
    ancho: int,
    alto: int,
    url: str = "",
    modelo: str = "",
    pasos: int = 25,
    semilla: int = 0,
    negativo: str = "",
    workflow_ruta: str = "",
    timeout: float = 600.0,
) -> bytes:
    """Genera una imagen con ComfyUI y devuelve los bytes del archivo."""
    base = _normalizar_url(url, "comfyui")
    if not _es_local(base):
        raise ErrorIA(
            "Por seguridad, la IA local solo se conecta a este equipo "
            "(127.0.0.1 o localhost)."
        )
    workflow_usuario = cargar_workflow(workflow_ruta) if workflow_ruta else None
    if not modelo and not workflow_usuario:
        modelos = listar_modelos("comfyui", base)
        if not modelos:
            raise ErrorIA(
                "ComfyUI no tiene checkpoints instalados.\n"
                "Descarga uno (por ejemplo SDXL-Turbo o flux1-schnell) en "
                "ComfyUI/models/checkpoints y vuelve a probar."
            )
        modelo = modelos[0]

    wf = _workflow_comfy(
        prompt,
        ancho=ancho,
        alto=alto,
        modelo=modelo,
        pasos=pasos,
        semilla=semilla,
        negativo=negativo,
        workflow=workflow_usuario,
    )
    cliente = str(uuid.uuid4())
    try:
        respuesta = peticion_json(
            base + "/prompt",
            metodo="POST",
            payload={"prompt": wf, "client_id": cliente},
            timeout=30.0,
        )
    except ErrorIA as exc:
        if "No se pudo conectar" in str(exc):
            raise _error_conexion(base, "comfyui", exc) from exc
        raise
    prompt_id = str(respuesta.get("prompt_id") or "")
    if not prompt_id:
        raise ErrorIA("ComfyUI no devolvió un identificador de trabajo.")

    limite = time.monotonic() + max(30.0, timeout)
    historial: dict = {}
    while time.monotonic() < limite:
        time.sleep(0.6)
        try:
            datos = _get_json(base + f"/history/{prompt_id}", timeout=20.0)
        except ErrorIA:
            continue
        if prompt_id in datos:
            historial = datos[prompt_id] or {}
            break
    if not historial:
        raise ErrorIA(
            f"ComfyUI no terminó la imagen en {int(timeout)} s. "
            "Revisa la ventana de ComfyUI: puede estar sin VRAM o esperando "
            "un modelo que falta."
        )

    estado = str((historial.get("status") or {}).get("status_str") or "")
    if estado == "error":
        raise ErrorIA(
            "ComfyUI devolvió un error al ejecutar el workflow. "
            "Revisa la consola de ComfyUI para ver el nodo que falló."
        )

    salidas = historial.get("outputs") or {}
    for nodo in salidas.values():
        for imagen in (nodo or {}).get("images") or []:
            if not isinstance(imagen, dict):
                continue
            consulta = urllib.parse.urlencode({
                "filename": imagen.get("filename", ""),
                "subfolder": imagen.get("subfolder", ""),
                "type": imagen.get("type", "output"),
            })
            datos_img = _descargar(base + "/view?" + consulta, timeout=120.0)
            if len(datos_img) > 1024:
                return datos_img
    raise ErrorIA("ComfyUI terminó pero no devolvió ninguna imagen.")


# --------------------------------------------------------------- Forge
def generar_forge(
    prompt: str,
    *,
    ancho: int,
    alto: int,
    url: str = "",
    modelo: str = "",
    pasos: int = 30,
    semilla: int = 0,
    negativo: str = "",
    timeout: float = 600.0,
) -> bytes:
    """Genera una imagen con Forge/AUTOMATIC1111 (``/sdapi/v1/txt2img``)."""
    base = _normalizar_url(url, "forge")
    if not _es_local(base):
        raise ErrorIA(
            "Por seguridad, la IA local solo se conecta a este equipo "
            "(127.0.0.1 o localhost)."
        )
    payload: dict = {
        "prompt": prompt,
        "negative_prompt": negativo or _NEGATIVO_DEFECTO,
        "width": int(ancho),
        "height": int(alto),
        "steps": int(pasos),
        "cfg_scale": 6.0,
        "sampler_name": "DPM++ 2M Karras",
        "seed": int(semilla),
        "batch_size": 1,
        "n_iter": 1,
        "send_images": True,
        "save_images": False,
    }
    if modelo:
        payload["override_settings"] = {"sd_model_checkpoint": modelo}
    ajustes = ajustes_para_modelo(modelo, int(pasos))
    if "cfg" in ajustes:
        payload["cfg_scale"] = float(ajustes["cfg"])
        payload["steps"] = int(ajustes["steps"])
        # Forge nombra los muestreadores a su manera.
        payload["sampler_name"] = (
            "Euler a" if ajustes.get("sampler_name") == "euler_ancestral"
            else "Euler" if ajustes.get("sampler_name") == "euler"
            else str(ajustes.get("sampler_name") or "Euler")
        )
    else:
        payload["steps"] = int(ajustes["steps"])
    try:
        datos = peticion_json(
            base + "/sdapi/v1/txt2img",
            metodo="POST",
            payload=payload,
            timeout=max(60.0, timeout),
        )
    except ErrorIA as exc:
        if "No se pudo conectar" in str(exc):
            raise _error_conexion(base, "forge", exc) from exc
        raise
    imagenes = datos.get("images") or []
    if not imagenes:
        raise ErrorIA("Forge no devolvió ninguna imagen.")
    crudo = str(imagenes[0])
    if crudo.startswith("data:"):
        crudo = crudo.partition(",")[2]
    if crudo.startswith("http"):
        return _descargar(crudo, timeout=120.0)
    try:
        return base64.b64decode(crudo)
    except (ValueError, TypeError) as exc:
        raise ErrorIA("Forge devolvió una imagen inválida.") from exc


# ------------------------------------------------------------------ API
def generar_local(
    prompt: str,
    *,
    backend: str = "comfyui",
    url: str = "",
    modelo: str = "",
    ancho: int = 1280,
    alto: int = 720,
    pasos: int = 25,
    semilla: int = 0,
    negativo: str = "",
    workflow_ruta: str = "",
    timeout: float = 600.0,
) -> bytes:
    """Genera una imagen con el backend local indicado."""
    backend = (backend or "comfyui").strip().lower()
    if backend in {"forge", "a1111", "automatic1111", "sd-webui"}:
        return generar_forge(
            prompt, ancho=ancho, alto=alto, url=url, modelo=modelo,
            pasos=pasos, semilla=semilla, negativo=negativo, timeout=timeout,
        )
    if backend == "comfyui":
        return generar_comfyui(
            prompt, ancho=ancho, alto=alto, url=url, modelo=modelo,
            pasos=pasos, semilla=semilla, negativo=negativo,
            workflow_ruta=workflow_ruta, timeout=timeout,
        )
    raise ErrorIA(f"Backend local desconocido: {backend}")


def probar_local(backend: str = "comfyui", url: str = "") -> str:
    """Genera una imagen diminuta de prueba y devuelve un mensaje de éxito."""
    estado = estado_backend(backend, url, timeout=8.0)
    if not estado["disponible"]:
        raise ErrorIA(estado["mensaje"])
    datos = generar_local(
        "a plain dark grey background, minimal, no text",
        backend=backend,
        url=estado["url"],
        modelo=(estado["modelos"][0] if estado["modelos"] else ""),
        ancho=512,
        alto=512,
        pasos=6,
        semilla=7,
        timeout=240.0,
    )
    if not datos:
        raise ErrorIA("El backend no devolvió imagen en la prueba.")
    return (
        f"Prueba correcta: {len(datos) // 1024} KB generados con "
        f"{BACKENDS.get(backend, backend)}."
    )
