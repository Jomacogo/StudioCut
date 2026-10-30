"""Detección del hardware del equipo para decidir si conviene la IA local.

Studio Cut puede generar imágenes con una IA instalada en el propio PC
(ComfyUI o Forge/AUTOMATIC1111) en lugar de pagar una API. Eso solo tiene
sentido si el equipo tiene una GPU con VRAM suficiente, así que aquí se
inspecciona el hardware y se devuelve un veredicto entendible.

Todo se hace con la biblioteca estándar: ``nvidia-smi`` si existe y
``GlobalMemoryStatusEx`` de Windows para la RAM. Nunca lanza excepciones.
"""
from __future__ import annotations

import ctypes
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

# Umbrales. SDXL a 1024² pide ~8 GB de VRAM en fp16; Flux.1-schnell en GGUF Q4
# baja a ~6,8 GB. Por debajo de 6 GB la generación local deja de ser práctica.
VRAM_MINIMA_GB = 6.0
VRAM_COMODA_GB = 10.0
RAM_MINIMA_GB = 12.0

# Consejo por tramo de VRAM (se muestra tal cual al usuario).
_MODELOS_POR_TRAMO = (
    (VRAM_COMODA_GB, "Flux.1-schnell fp8 o SDXL: máxima calidad y velocidad."),
    (7.5, "SDXL fp16 a 1024², o Flux.1-schnell en GGUF Q4/Q5 (algo más lento)."),
    (VRAM_MINIMA_GB, "SDXL-Turbo/Lightning o Flux.1-schnell GGUF Q4 a 768-1024 px."),
)


@dataclass
class Equipo:
    """Resumen del hardware relevante para la IA local."""

    gpu: str = ""
    vram_gb: float = 0.0
    ram_gb: float = 0.0
    driver: str = ""
    so: str = ""
    motivo: str = ""
    consejo: str = ""
    detalles: list[str] = field(default_factory=list)

    @property
    def tiene_gpu(self) -> bool:
        return bool(self.gpu)

    @property
    def cumple_requisitos(self) -> bool:
        """True si la IA local es una opción razonable en este equipo."""
        return self.tiene_gpu and self.vram_gb >= VRAM_MINIMA_GB

    @property
    def nivel(self) -> str:
        """``excelente`` | ``justo`` | ``insuficiente`` | ``sin_gpu``."""
        if not self.tiene_gpu:
            return "sin_gpu"
        if self.vram_gb >= VRAM_COMODA_GB:
            return "excelente"
        if self.vram_gb >= VRAM_MINIMA_GB:
            return "justo"
        return "insuficiente"

    def resumen(self) -> str:
        """Una línea para mostrar en la interfaz."""
        if not self.tiene_gpu:
            return "Sin GPU NVIDIA detectada: la IA local no está disponible."
        texto = f"{self.gpu} · {self.vram_gb:.1f} GB VRAM · {self.ram_gb:.0f} GB RAM"
        if self.driver:
            texto += f" · driver {self.driver}"
        return texto

    def a_dict(self) -> dict:
        return {
            "gpu": self.gpu,
            "vram_gb": round(self.vram_gb, 1),
            "ram_gb": round(self.ram_gb, 1),
            "driver": self.driver,
            "so": self.so,
            "nivel": self.nivel,
            "cumple_requisitos": self.cumple_requisitos,
            "motivo": self.motivo,
            "consejo": self.consejo,
            "resumen": self.resumen(),
        }


def _ram_total_gb() -> float:
    """RAM física total en GB (Windows nativo; /proc en Linux)."""
    if os.name == "nt":
        class _Memoria(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        memoria = _Memoria()
        memoria.dwLength = ctypes.sizeof(_Memoria)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memoria)):
                return memoria.ullTotalPhys / (1024 ** 3)
        except Exception:  # noqa: BLE001 - solo es informativo
            return 0.0
        return 0.0
    try:
        with open("/proc/meminfo", encoding="utf-8") as archivo:
            for linea in archivo:
                if linea.startswith("MemTotal:"):
                    return float(linea.split()[1]) / (1024 ** 2)
    except OSError:
        pass
    return 0.0


def _gpu_nvidia() -> tuple[str, float, str]:
    """(nombre, VRAM en GB, driver) de la primera GPU NVIDIA, si la hay."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        # Ruta habitual en Windows cuando no está en el PATH.
        candidato = os.path.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe",
        )
        if os.path.exists(candidato):
            exe = candidato
    if not exe:
        return "", 0.0, ""
    try:
        salida = subprocess.run(
            [
                exe,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", 0.0, ""
    if salida.returncode != 0 or not salida.stdout.strip():
        return "", 0.0, ""
    return parsear_gpu(salida.stdout)


def _consejo_para(vram_gb: float) -> str:
    for limite, texto in _MODELOS_POR_TRAMO:
        if vram_gb >= limite:
            return texto
    return _MODELOS_POR_TRAMO[-1][1]


def detectar_equipo(usar_cache: bool = True) -> Equipo:
    """Inspecciona el equipo y devuelve un :class:`Equipo` con el veredicto."""
    global _CACHE
    if usar_cache and _CACHE is not None:
        return _CACHE

    gpu, vram, driver = _gpu_nvidia()
    ram = _ram_total_gb()
    equipo = Equipo(
        gpu=gpu,
        vram_gb=vram,
        ram_gb=ram,
        driver=driver,
        so="Windows" if os.name == "nt" else os.uname().sysname,
    )
    equipo.detalles = [
        f"GPU: {gpu or 'no detectada'}",
        f"VRAM: {vram:.1f} GB (mínimo recomendado {VRAM_MINIMA_GB:.0f} GB)",
        f"RAM: {ram:.0f} GB (mínimo recomendado {RAM_MINIMA_GB:.0f} GB)",
    ]

    if not gpu:
        equipo.motivo = (
            "No se encontró una GPU NVIDIA con nvidia-smi. ComfyUI puede "
            "funcionar con otra GPU o en CPU, pero será demasiado lento para "
            "generar las imágenes de un video completo."
        )
        equipo.consejo = "Sigue usando Pollinations o una API de pago."
    elif vram < VRAM_MINIMA_GB:
        equipo.motivo = (
            f"Esta GPU tiene {vram:.1f} GB de VRAM y la generación local pide "
            f"al menos {VRAM_MINIMA_GB:.0f} GB. Se podría forzar, pero cada "
            "imagen tardaría minutos y podría quedarse sin memoria."
        )
        equipo.consejo = "Usa Pollinations (gratis) o una API de pago."
    else:
        equipo.motivo = "El equipo cumple los requisitos para la IA local."
        equipo.consejo = _consejo_para(vram)
        if ram and ram < RAM_MINIMA_GB:
            equipo.motivo += (
                f" Aviso: {ram:.0f} GB de RAM es justo; cierra programas "
                "pesados antes de generar."
            )

    if usar_cache:
        _CACHE = equipo
    return equipo


_CACHE: Equipo | None = None


def olvidar_cache() -> None:
    """Fuerza a volver a inspeccionar el equipo en la próxima llamada."""
    global _CACHE
    _CACHE = None


def parsear_gpu(texto: str) -> tuple[str, float, str]:
    """Interpreta una salida de ``nvidia-smi`` (útil para pruebas)."""
    primera = (texto or "").strip().splitlines()[0] if texto.strip() else ""
    partes = [p.strip() for p in primera.split(",")]
    if len(partes) < 2:
        return "", 0.0, ""
    try:
        vram = float(re.sub(r"[^\d.]", "", partes[1])) / 1024.0
    except ValueError:
        vram = 0.0
    return partes[0], vram, partes[2] if len(partes) > 2 else ""
