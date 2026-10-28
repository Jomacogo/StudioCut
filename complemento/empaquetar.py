"""Empaqueta el complemento y actualiza el SHA-256 de plugin.json.

Uso (desde la carpeta ``complemento``):

    python empaquetar.py

Genera ``dist/studiocut-ai-1.0.0.zip`` y deja ``plugin.json`` con el hash
correcto. Después sube el ZIP a una Release de GitHub y publica el
``plugin.json`` en la rama principal.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

BASE = Path(__file__).resolve().parent
DIST = BASE / "dist"
# El plugin.json se publica aparte: dentro del ZIP llevaría un hash obsoleto
# (el del propio ZIP), así que se excluye.
INCLUIR = ["README.md", "studiocut_ai"]


def main() -> None:
    manifiesto = json.loads((BASE / "plugin.json").read_text(encoding="utf-8"))
    version = str(manifiesto["version"])
    DIST.mkdir(exist_ok=True)
    destino = DIST / f"studiocut-ai-{version}.zip"

    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre in INCLUIR:
            ruta = BASE / nombre
            if not ruta.exists():
                continue
            if ruta.is_file():
                z.write(ruta, nombre)
            else:
                for archivo in sorted(ruta.rglob("*")):
                    if archivo.is_file() and "__pycache__" not in archivo.parts:
                        z.write(archivo, archivo.relative_to(BASE).as_posix())

    datos = destino.read_bytes()
    manifiesto["sha256"] = hashlib.sha256(datos).hexdigest()
    manifiesto["url"] = (
        f"https://github.com/Jomacogo/StudioCut-AI/releases/download/"
        f"v{version}/studiocut-ai-{version}.zip"
    )
    (BASE / "plugin.json").write_text(
        json.dumps(manifiesto, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"ZIP: {destino} ({len(datos) // 1024} KB)")
    print(f"SHA-256: {manifiesto['sha256']}")
    print(f"URL: {manifiesto['url']}")


if __name__ == "__main__":
    main()
