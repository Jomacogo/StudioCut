"""Pruebas de la IA local (ComfyUI / Forge) con servidores falsos.

No hace falta GPU ni descargar modelos: se levanta un servidor HTTP local que
imita las rutas reales de ComfyUI y de AUTOMATIC1111/Forge para comprobar que
el cliente arma bien las peticiones y devuelve la imagen.
"""
from __future__ import annotations

import base64
import json
import random
import struct
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import hardware
from core.ia import local
from core.ia.pipeline import ConfigIA, prompt_de_miniatura


def _png(ancho: int = 64, alto: int = 64) -> bytes:
    """PNG válido con ruido, para que pese más de 1 KB."""
    filas = b""
    for _ in range(alto):
        filas += b"\x00" + bytes(random.getrandbits(8) for _ in range(ancho * 3))

    def trozo(tipo: bytes, datos: bytes) -> bytes:
        return (
            struct.pack(">I", len(datos))
            + tipo
            + datos
            + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
        )

    cabecera = struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + trozo(b"IHDR", cabecera)
        + trozo(b"IDAT", zlib.compress(filas))
        + trozo(b"IEND", b"")
    )


PNG = _png()


class _Servidor:
    """Servidor HTTP en un hilo, con la última petición guardada."""

    def __init__(self, backend: str) -> None:
        self.backend = backend
        self.ultimo_post: dict = {}
        servidor = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args) -> None:  # silencio en las pruebas
                pass

            def _responder(self, datos, tipo="application/json") -> None:
                if isinstance(datos, (dict, list)):
                    cuerpo = json.dumps(datos).encode()
                else:
                    cuerpo = datos
                self.send_response(200)
                self.send_header("Content-Type", tipo)
                self.send_header("Content-Length", str(len(cuerpo)))
                self.end_headers()
                self.wfile.write(cuerpo)

            def do_GET(self) -> None:  # noqa: N802
                ruta = self.path.split("?")[0]
                if ruta == "/system_stats":
                    return self._responder(
                        {"devices": [{"name": "GPU de prueba", "vram_total": 8589934592}]}
                    )
                if ruta == "/object_info/CheckpointLoaderSimple":
                    return self._responder({
                        "CheckpointLoaderSimple": {
                            "input": {"required": {"ckpt_name": [["sdxl.safetensors"], {}]}}
                        }
                    })
                if ruta.startswith("/history/"):
                    return self._responder({
                        "job-1": {
                            "status": {"status_str": "success"},
                            "outputs": {
                                "7": {"images": [{
                                    "filename": "StudioCut_0001.png",
                                    "subfolder": "",
                                    "type": "output",
                                }]}
                            },
                        }
                    })
                if ruta == "/view":
                    return self._responder(PNG, "image/png")
                if ruta == "/sdapi/v1/options":
                    return self._responder({"sd_model_checkpoint": "sdxl.safetensors"})
                if ruta == "/sdapi/v1/sd-models":
                    return self._responder([{"title": "sdxl.safetensors"}])
                self.send_error(404)

            def do_POST(self) -> None:  # noqa: N802
                largo = int(self.headers.get("Content-Length") or 0)
                cuerpo = self.rfile.read(largo) if largo else b"{}"
                datos = json.loads(cuerpo or b"{}")
                servidor.ultimo_post = datos
                if self.path.startswith("/prompt"):
                    return self._responder({"prompt_id": "job-1"})
                if self.path.startswith("/sdapi/v1/txt2img"):
                    return self._responder({
                        "images": [base64.b64encode(PNG).decode("ascii")]
                    })
                self.send_error(404)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.puerto = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.puerto}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def cerrar(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def comfyui():
    servidor = _Servidor("comfyui")
    yield servidor
    servidor.cerrar()


@pytest.fixture
def forge():
    servidor = _Servidor("forge")
    yield servidor
    servidor.cerrar()


# --------------------------------------------------------------- ComfyUI
def test_estado_comfyui_detecta_modelos(comfyui):
    estado = local.estado_backend("comfyui", comfyui.url)
    assert estado["disponible"] is True
    assert estado["modelos"] == ["sdxl.safetensors"]
    assert "GPU de prueba" in estado["mensaje"]


def test_generar_comfyui_inyecta_prompt_y_tamano(comfyui):
    datos = local.generar_local(
        "una escultura de mármol",
        backend="comfyui",
        url=comfyui.url,
        ancho=1280,
        alto=720,
        pasos=12,
        semilla=42,
    )
    assert datos.startswith(b"\x89PNG")
    assert len(datos) > 1024
    enviado = comfyui.ultimo_post["prompt"]
    assert enviado["2"]["inputs"]["text"] == "una escultura de mármol"
    assert enviado["4"]["inputs"]["width"] == 1280
    assert enviado["4"]["inputs"]["height"] == 720
    assert enviado["5"]["inputs"]["seed"] == 42
    assert enviado["1"]["inputs"]["ckpt_name"] == "sdxl.safetensors"


def test_sdxl_turbo_recorta_cfg_y_pasos(comfyui):
    """SDXL-Turbo con CFG 7 y 25 pasos sale quemado: la app lo corrige sola."""
    local.generar_local(
        "un faro al atardecer",
        backend="comfyui",
        url=comfyui.url,
        modelo="sd_xl_turbo_1.0_fp16.safetensors",
        pasos=25,
        semilla=3,
    )
    muestreo = comfyui.ultimo_post["prompt"]["5"]["inputs"]
    assert muestreo["cfg"] == 1.0
    assert muestreo["steps"] <= 8
    assert muestreo["sampler_name"] == "euler_ancestral"
    # Un SDXL normal mantiene los pasos que pidió el usuario.
    assert local.ajustes_para_modelo("sdxl_base.safetensors", 25)["steps"] == 25


def test_workflow_propio_recibe_prompt(comfyui):
    """Un workflow exportado (Flux GGUF, LoRAs…) también se rellena solo."""
    workflow = {
        "10": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "flux-q4.gguf"}},
        "20": {"class_type": "CLIPTextEncode", "inputs": {"text": "viejo", "clip": ["11", 0]}},
        "21": {"class_type": "CLIPTextEncode", "inputs": {"text": "neg", "clip": ["11", 0]}},
        "30": {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": 512, "height": 512, "batch_size": 1},
        },
        "40": {
            "class_type": "KSampler",
            "inputs": {"seed": 1, "steps": 20, "cfg": 1.0},
        },
    }
    local._parchear_workflow(workflow, "texto nuevo", 1280, 720, 8, 99, "nada de texto")
    assert workflow["20"]["inputs"]["text"] == "texto nuevo"
    assert workflow["21"]["inputs"]["text"] == "nada de texto"
    assert workflow["30"]["inputs"]["width"] == 1280
    assert workflow["40"]["inputs"]["seed"] == 99
    assert workflow["40"]["inputs"]["steps"] == 8


def test_generar_con_workflow_guardado(comfyui, tmp_path):
    ruta = tmp_path / "wf.json"
    ruta.write_text(json.dumps({
        "20": {"class_type": "CLIPTextEncode", "inputs": {"text": "x", "clip": ["11", 0]}},
        "30": {"class_type": "EmptyLatentImage", "inputs": {"width": 8, "height": 8}},
        "40": {"class_type": "KSampler", "inputs": {"seed": 1, "steps": 20}},
    }), encoding="utf-8")
    datos = local.generar_local(
        "otra escena", backend="comfyui", url=comfyui.url,
        workflow_ruta=str(ruta), ancho=1024, alto=1024,
    )
    assert datos.startswith(b"\x89PNG")
    enviado = comfyui.ultimo_post["prompt"]
    assert enviado["20"]["inputs"]["text"] == "otra escena"
    assert enviado["30"]["inputs"]["width"] == 1024


def test_workflow_de_interfaz_se_rechaza(tmp_path):
    ruta = tmp_path / "interfaz.json"
    ruta.write_text(json.dumps({"nodes": [], "links": []}), encoding="utf-8")
    with pytest.raises(local.ErrorIA) as info:
        local.cargar_workflow(str(ruta))
    assert "Export (API)" in str(info.value)


def test_local_solo_acepta_este_equipo():
    with pytest.raises(local.ErrorIA) as info:
        local.generar_local("x", backend="comfyui", url="http://10.0.0.5:8188")
    assert "solo se conecta a este equipo" in str(info.value)


# ----------------------------------------------------------------- Forge
def test_generar_forge(forge):
    datos = local.generar_local(
        "un bosque de noche", backend="forge", url=forge.url, ancho=1280, alto=720,
        pasos=20, semilla=5,
    )
    assert datos.startswith(b"\x89PNG")
    enviado = forge.ultimo_post
    assert enviado["prompt"] == "un bosque de noche"
    assert enviado["width"] == 1280
    assert enviado["height"] == 720
    assert enviado["seed"] == 5


def test_estado_forge(forge):
    estado = local.estado_backend("forge", forge.url)
    assert estado["disponible"] is True
    assert estado["modelos"] == ["sdxl.safetensors"]


def test_servidor_apagado_da_mensaje_util():
    estado = local.estado_backend("comfyui", "http://127.0.0.1:1", timeout=1.0)
    assert estado["disponible"] is False
    assert estado["mensaje"]


# -------------------------------------------------------------- Hardware
def test_parseo_nvidia_smi():
    nombre, vram, driver = hardware.parsear_gpu("NVIDIA GeForce RTX 4060 Ti, 8188, 617.14")
    assert nombre == "NVIDIA GeForce RTX 4060 Ti"
    assert vram == pytest.approx(8.0, abs=0.01)
    assert driver == "617.14"


def test_veredicto_por_vram():
    equipo = hardware.Equipo(gpu="RTX 4060 Ti", vram_gb=8.0, ram_gb=16.0)
    assert equipo.cumple_requisitos is True
    assert equipo.nivel == "justo"
    flojo = hardware.Equipo(gpu="GTX 1050", vram_gb=2.0, ram_gb=8.0)
    assert flojo.cumple_requisitos is False
    assert flojo.nivel == "insuficiente"
    grande = hardware.Equipo(gpu="RTX 4090", vram_gb=24.0, ram_gb=64.0)
    assert grande.nivel == "excelente"


# --------------------------------------------------------------- Miniatura
def test_plantilla_de_miniatura_usa_marcadores():
    cfg = ConfigIA(prompt_miniatura='Cartel dorado que dice "{titulo}" sobre {tema}.')
    paquete = {"titulo": "MODO MONJE"}
    texto = prompt_de_miniatura(paquete, cfg, idea="disciplina")
    assert texto == "Cartel dorado que dice \"MODO MONJE\" sobre disciplina."


def test_sin_plantilla_se_usa_el_prompt_de_la_ia():
    cfg = ConfigIA()
    paquete = {"prompt_miniatura": "Prompt generado por la IA"}
    assert prompt_de_miniatura(paquete, cfg) == "Prompt generado por la IA"
