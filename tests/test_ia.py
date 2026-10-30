"""Pruebas del paquete de IA, credenciales y complemento MCP.

Todo funciona sin red: los proveedores se sustituyen por dobles de prueba.
Las claves reales del usuario **nunca** se tocan: las credenciales se escriben
en una carpeta temporal.

Uso:
    python tests/test_ia.py
    QT_QPA_PLATFORM=offscreen python -m pytest tests/test_ia.py
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import ordering, plugins
from core.ia import credenciales, mcp_server
from core.ia.guion import (
    Bloque,
    Guion,
    construir_texto,
    marcas_estimadas,
    normalizar_guion,
    parsear_guion,
    validar_guion,
)
from core.ia.imagenes import _espera_limite, construir_prompt_imagen
from core.ia.pipeline import ConfigIA, ejecutar_pipeline
from core.ia.proveedores import _es_local, generar_texto

GUION_EJEMPLO = """Introducción.
Hoy vamos a aprender a ahorrar dinero de verdad, sin pasar hambre y sin dejar de vivir.

Capítulo uno: Conoce tu gasto
Durante una semana apunta todo lo que gastas. Esa cafetería diaria suma mucho al mes. Revisa tus suscripciones y cancela lo que no uses.

Capítulo dos: Ahorra sin sufrir
Define una meta pequeña y automática. Separa el ahorro el mismo día que cobras. Cocina en casa dos días más por semana.

Cierre.
Empieza hoy con un solo cambio. Comparte este video y nos vemos en el siguiente."""


# ------------------------------------------------------------------ Guion
def test_guion_parseo() -> None:
    guion = parsear_guion(GUION_EJEMPLO)
    assert [b.tipo for b in guion.bloques] == [
        "intro", "capitulo", "capitulo", "cierre",
    ], [b.tipo for b in guion.bloques]
    assert [b.numero for b in guion.capitulos] == [1, 2]
    assert guion.capitulos[0].titulo == "Conoce tu gasto"
    assert validar_guion(guion) == [], validar_guion(guion)
    assert "Introducción." in guion.texto
    assert "Capítulo uno:" in guion.texto
    assert "Cierre." in guion.texto


def test_guion_inline_y_markdown() -> None:
    texto = (
        "**Introducción.** Bienvenidos al video de hoy sobre hábitos saludables "
        "que puedes aplicar desde mañana mismo sin gastar dinero. "
        "**Capítulo uno:** Empieza por dormir mejor y mantener un horario fijo "
        "incluso los fines de semana para descansar de verdad. "
        "**Capítulo dos:** Muévete cinco minutos al despertar, estira el cuerpo "
        "y camina un poco antes de sentarte a trabajar. "
        "**Cierre.** Gracias por acompañarme, comparte el video y nos vemos pronto."
    )
    guion = parsear_guion(texto)
    tipos = [b.tipo for b in guion.bloques]
    assert tipos == ["intro", "capitulo", "capitulo", "cierre"], tipos
    assert "**" not in guion.texto
    assert validar_guion(guion) == [], validar_guion(guion)


def test_guion_normaliza_orden_y_numeros() -> None:
    guion = Guion(bloques=[
        Bloque("cierre", 0, "", "Nos vemos pronto y gracias por acompañarme hoy."),
        Bloque("capitulo", 7, "Segundo tema", "Texto suficientemente largo del segundo tema del video de prueba."),
        Bloque("intro", 0, "", "Bienvenidos a este video donde veremos ideas útiles para todos."),
        Bloque("capitulo", 3, "Primer tema", "Texto suficientemente largo del primer tema del video de prueba."),
    ])
    normalizar_guion(guion)
    assert [b.tipo for b in guion.bloques] == ["intro", "capitulo", "capitulo", "cierre"]
    assert [b.numero for b in guion.capitulos] == [1, 2]
    assert construir_texto(guion.bloques).startswith("Introducción.")


def test_guion_incompleto_se_detecta() -> None:
    guion = parsear_guion("Capítulo uno: Solo esto.\nMuy corto.")
    problemas = validar_guion(guion)
    assert any("introducción" in p for p in problemas), problemas
    assert any("cierre" in p for p in problemas), problemas


def test_marcas_estimadas() -> None:
    guion = parsear_guion(GUION_EJEMPLO)
    marcas = marcas_estimadas(guion, 120.0)
    assert [m["titulo"] for m in marcas] == [
        "Introducción", "Capítulo 1", "Capítulo 2", "Cierre",
    ]
    assert marcas[0]["tiempo"] == 0.0
    tiempos = [m["tiempo"] for m in marcas]
    assert tiempos == sorted(tiempos)
    assert all(0.0 <= t <= 120.0 for t in tiempos)


# --------------------------------------------------------------- Imágenes
def test_nombres_ia_se_clasifican_bien() -> None:
    """Los nombres reales que genera la IA llevan guion bajo y fecha."""
    casos = {
        "intro_20261028_175302.jpg": ("intro", 0),
        "cierre_20261028_175302.jpg": ("cierre", 0),
        "capitulo_01_20261028_175302.jpg": ("capitulo", 1),
        "capitulo_12_20261028_175302.png": ("capitulo", 12),
    }
    for nombre, esperado in casos.items():
        assert ordering._clasificar_imagen(nombre) == esperado, nombre

    orden = ordering.ordenar_imagenes(list(casos))
    assert orden[0].startswith("intro_"), orden
    assert orden[-1].startswith("cierre_"), orden


def test_emparejar_imagenes_ia_no_se_invierte() -> None:
    imagenes = [
        "capitulo_01_20261028_175302.jpg",
        "cierre_20261028_175302.jpg",
        "intro_20261028_175302.jpg",
    ]
    marcas = [
        {"titulo": "Introducción", "tiempo": 0.0},
        {"titulo": "Capítulo 1", "tiempo": 12.39},
        {"titulo": "Cierre", "tiempo": 54.63},
    ]
    asignadas = ordering.emparejar_imagenes_por_capitulo(marcas, imagenes)
    assert asignadas[0].startswith("intro_"), asignadas
    assert asignadas[1].startswith("capitulo_01_"), asignadas
    assert asignadas[2].startswith("cierre_"), asignadas


def test_prompt_imagen_sin_texto() -> None:
    prompt = construir_prompt_imagen(
        Bloque("capitulo", 1, "Ahorro", "Una familia planifica sus compras."),
        tema="Finanzas personales",
    )
    assert "16:9" in prompt
    assert "Sin texto" in prompt
    assert "Finanzas personales" in prompt


def test_espera_limite_pollinations() -> None:
    mensaje = (
        '{"status":402,"error":{"code":"FREE_LIMIT","message":'
        '"Free limit reached: 1 image every 45 seconds per IP. Retry after 40s"}}'
    )
    assert _espera_limite(mensaje) == 40.0
    assert _espera_limite("HTTP 402 sin más detalle") is None
    assert _espera_limite("Retry after 999s") == 120.0


# ----------------------------------------------------------- Credenciales
def test_credenciales_temporal() -> None:
    with tempfile.TemporaryDirectory(prefix="credenciales_") as tmp:
        carpeta = Path(tmp)
        archivo = carpeta / "ia_credenciales.json"
        with mock.patch.object(credenciales, "_CARPETA", carpeta), \
                mock.patch.object(credenciales, "_ARCHIVO", archivo):
            assert credenciales.obtener_clave("openrouter") == ""
            credenciales.guardar_clave("openrouter", "sk-or-v1-prueba-1234567890")
            assert credenciales.obtener_clave("openrouter") == "sk-or-v1-prueba-1234567890"
            assert credenciales.pista_clave("openrouter") == "sk-or-…7890"
            datos = archivo.read_text(encoding="utf-8")
            assert "sk-or-v1-prueba-1234567890" not in datos, "la clave quedó en claro"
            assert "openrouter" in credenciales.listar_proveedores()
            credenciales.borrar_clave("openrouter")
            assert not credenciales.hay_clave("openrouter")
            assert not archivo.exists() or "openrouter" not in archivo.read_text(encoding="utf-8")


# -------------------------------------------------------------- Pipeline
def test_pipeline_sin_red() -> None:
    guion = parsear_guion(GUION_EJEMPLO)
    eventos: list[str] = []
    with tempfile.TemporaryDirectory(prefix="pipeline_") as tmp, \
            mock.patch("core.ia.pipeline.generar_guion", return_value=guion), \
            mock.patch("core.ia.pipeline.generar_paquete_editorial", return_value={}), \
            mock.patch("core.ia.pipeline.generar_imagenes", return_value=["a.png", "b.png"]), \
            mock.patch("core.ia.pipeline.generar_voz_ia", return_value=str(Path(tmp) / "voz.wav")):
        cfg = ConfigIA(duracion_min=2, n_capitulos=2)
        resultado = ejecutar_pipeline(
            "Ahorro",
            cfg,
            {"openrouter": "clave-falsa"},
            tmp,
            al_progreso=lambda p, e: eventos.append(f"{p}:{e}"),
            al_log=lambda m: eventos.append(m),
            al_guion=lambda texto, g: eventos.append("guion"),
            al_imagenes=lambda rutas: eventos.append(f"imagenes:{len(rutas)}"),
            al_voz=lambda ruta: eventos.append("voz"),
        )
    assert resultado.guion is guion
    assert resultado.imagenes == ["a.png", "b.png"]
    assert resultado.voz.endswith("voz.wav")
    assert "guion" in eventos and "imagenes:2" in eventos and "voz" in eventos
    assert any(e.startswith("100:") for e in eventos), eventos


def test_paquete_editorial_sin_red_y_prompt_de_miniatura() -> None:
    from core.ia.guion import generar_paquete_editorial

    guion = parsear_guion(GUION_EJEMPLO)
    respuesta = {
        "texto": json.dumps({
            "titulos": ["Ahorra con intención"],
            "descripcion": "Ideas prácticas para ordenar tus finanzas.",
            "etiquetas": ["finanzas", "ahorro"],
            "prompts_imagenes": [
                {"seccion": "Introducción", "prompt": "Una mesa con un cuaderno de ahorro"},
                {"seccion": "Capítulo uno", "prompt": "Una persona revisa sus gastos"},
                {"seccion": "Capítulo dos", "prompt": "Una familia prepara un presupuesto"},
                {"seccion": "Cierre", "prompt": "Una persona mira hacia el amanecer"},
            ],
        }, ensure_ascii=False)
    }
    with mock.patch("core.ia.proveedores.generar_texto", return_value=respuesta):
        paquete = generar_paquete_editorial(
            "Ahorro", guion, clave="test", instrucciones_canal="brief de prueba"
        )
    assert paquete["titulos"] == ["Ahorra con intención"]
    assert paquete["etiquetas"] == ["finanzas", "ahorro"]
    assert len(paquete["prompts_imagenes"]) == len(guion.bloques)
    assert "pending" not in paquete["descripcion"].lower()
    assert "\n\n" in paquete["descripcion"]
    assert "16:9" in paquete["prompt_miniatura"]
    assert "dark academia" in paquete["prompt_miniatura"]


def test_config_ia_persiste_brief_y_paquete() -> None:
    paquete = {"titulos": ["Título de prueba"], "prompt_miniatura": "Estilo clásico"}
    cfg = ConfigIA(prompt_canal="Mi brief editable", paquete_editorial=paquete)
    restaurada = ConfigIA.desde_dict(cfg.a_dict())
    assert restaurada.prompt_canal == "Mi brief editable"
    assert restaurada.paquete_editorial == paquete
    assert restaurada.duracion_min == 30


def test_pipeline_prepara_paquete_con_guion_existente() -> None:
    guion = parsear_guion(GUION_EJEMPLO)
    paquete = {
        "titulos": ["Título"], "descripcion": "Descripción", "etiquetas": ["ahorro"],
        "prompt_miniatura": "Prompt 16:9", "prompts_imagenes": [], "miniatura_ruta": "",
    }
    eventos = []
    cfg = ConfigIA(generar_imagenes=False, generar_voz=False)
    with mock.patch("core.ia.pipeline.generar_guion") as generar_guion_mock, \
            mock.patch("core.ia.pipeline.generar_paquete_editorial", return_value=paquete), \
            mock.patch("core.ia.pipeline.generar_imagenes") as imagenes_mock, \
            mock.patch("core.ia.pipeline.generar_voz_ia") as voz_mock:
        resultado = ejecutar_pipeline(
            "Ahorro", cfg, {"openrouter": "clave-falsa"}, tempfile.gettempdir(),
            guion_previo=guion, al_paquete=lambda p: eventos.append(p),
        )
    generar_guion_mock.assert_not_called()
    voz_mock.assert_not_called()
    imagenes_mock.assert_not_called()
    assert resultado.guion is guion
    assert resultado.paquete == paquete
    assert eventos == [paquete]


def test_pipeline_exige_clave() -> None:
    cfg = ConfigIA()
    try:
        ejecutar_pipeline("Idea", cfg, {}, tempfile.gettempdir())
    except Exception as exc:  # noqa: BLE001
        assert "OpenRouter" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Debió exigir la clave de OpenRouter")


def test_texto_local_sin_clave() -> None:
    """Ollama y otros servidores locales no deben exigir clave."""
    assert _es_local("http://localhost:11434/v1")
    assert _es_local("http://127.0.0.1:1234/v1")
    assert not _es_local("https://openrouter.ai/api/v1")

    respuesta = {"texto": "OK", "razonamiento": "", "uso": {}, "modelo": "llama3", "fin": "stop"}
    with mock.patch("core.ia.proveedores.llamar_chat", return_value=respuesta) as llamada:
        resultado = generar_texto(
            [{"role": "user", "content": "Hola"}],
            clave="",
            modelo="llama3",
            base_url="http://localhost:11434/v1",
        )
    assert resultado["texto"] == "OK"
    assert llamada.call_args.kwargs["clave"] == ""


# --------------------------------------------------------------- Plugins
def test_manifest_valido_y_rechazos() -> None:
    bueno = {
        "version": "1.0.0",
        "url": "https://github.com/Jomacogo/StudioCut-AI/releases/download/v1.0.0/x.zip",
        "sha256": "a" * 64,
        "min_app": "1.0.0",
    }
    assert plugins.validar_manifest(dict(bueno))["version"] == "1.0.0"

    casos = [
        {**bueno, "sha256": "abc"},
        {**bueno, "url": "http://inseguro/x.zip"},
        {**bueno, "min_app": "99.0.0"},
        {**bueno, "version": ""},
    ]
    for datos in casos:
        try:
            plugins.validar_manifest(dict(datos))
        except plugins.ErrorComplemento:
            continue
        raise AssertionError(f"Debería rechazar: {datos}")


def _zip_bytes(archivos: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for nombre, contenido in archivos.items():
            z.writestr(nombre, contenido)
    return buffer.getvalue()


def test_instalacion_zip_y_estado() -> None:
    datos = _zip_bytes({
        "plugin.json": json.dumps({"version": "1.0.0"}),
        "studiocut_ai/__init__.py": '__version__ = "1.0.0"\n',
        "studiocut_ai/__main__.py": "print('hola')\n",
    })
    with tempfile.TemporaryDirectory(prefix="plugins_") as tmp:
        base = Path(tmp) / "plugins"
        with mock.patch.object(plugins, "ruta_base", return_value=base):
            ruta = plugins.instalar_desde_zip(datos, version="1.0.0")
            assert (ruta / "studiocut_ai" / "__main__.py").exists()
            estado = plugins.estado()
            assert estado["instalado"] is True
            assert estado["version"] == "1.0.0"
            assert len(estado["sha256"]) == 64
            comando, cwd = plugins.comando_lanzamiento()
            assert comando[1:] == ["-m", "studiocut_ai", "--stdio"], comando
            assert Path(cwd) == ruta
            config = plugins.configuracion_mcp()
            servidor = config["mcpServers"]["studiocut-ai"]
            assert servidor["env"]["STUDIOCUT_LAUNCH_TOKEN"]
            plugins.desinstalar()
            assert plugins.estado()["instalado"] is False


def test_zip_inseguro_se_rechaza() -> None:
    datos = _zip_bytes({
        "studiocut_ai/__main__.py": "print('hola')\n",
        "../fuera.txt": "malicioso",
    })
    with tempfile.TemporaryDirectory(prefix="plugins_") as tmp:
        base = Path(tmp) / "plugins"
        with mock.patch.object(plugins, "ruta_base", return_value=base):
            try:
                plugins.instalar_desde_zip(datos, version="1.0.0")
            except plugins.ErrorComplemento as exc:
                assert "insegura" in str(exc).lower() or "fuera" in str(exc).lower(), exc
            else:  # pragma: no cover
                raise AssertionError("El ZIP con «../» debió rechazarse")


def test_hash_incorrecto_se_rechaza() -> None:
    datos = _zip_bytes({"studiocut_ai/__main__.py": "print('hola')\n"})
    with tempfile.TemporaryDirectory(prefix="plugins_") as tmp:
        base = Path(tmp) / "plugins"
        with mock.patch.object(plugins, "ruta_base", return_value=base):
            try:
                plugins.instalar_desde_zip(
                    datos, version="1.0.0", sha256_esperado="0" * 64
                )
            except plugins.ErrorComplemento as exc:
                assert "SHA-256" in str(exc)
            else:  # pragma: no cover
                raise AssertionError("El hash incorrecto debió rechazarse")


# ------------------------------------------------------------------- MCP
def test_mcp_protocolo_basico() -> None:
    init = mcp_server.manejar_solicitud({
        "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {},
    })
    assert init["result"]["serverInfo"]["name"] == "studiocut-ia"

    listado = mcp_server.manejar_solicitud({
        "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
    })
    nombres = [t["name"] for t in listado["result"]["tools"]]
    assert set(nombres) >= {
        "generar_guion", "generar_imagenes", "generar_voz",
        "listar_modelos_gratis", "estado",
    }

    assert mcp_server.manejar_solicitud({
        "jsonrpc": "2.0", "id": 3, "method": "ping", "params": {},
    })["result"] == {}

    assert mcp_server.manejar_solicitud({
        "jsonrpc": "2.0", "id": 4, "method": "notifications/initialized",
    }) is None

    error = mcp_server.manejar_solicitud({
        "jsonrpc": "2.0", "id": 5, "method": "no/existe", "params": {},
    })
    assert error["error"]["code"] == -32601


def test_mcp_estado_sin_claves_reales() -> None:
    with mock.patch.dict(os.environ, {}, clear=False), \
            mock.patch.object(mcp_server, "_clave", return_value=""):
        respuesta = mcp_server.manejar_solicitud({
            "jsonrpc": "2.0", "id": 9, "method": "tools/call",
            "params": {"name": "estado", "arguments": {}},
        })
    texto = respuesta["result"]["content"][0]["text"]
    estado = json.loads(texto)
    assert estado["openrouter"] is False
    assert estado["elevenlabs"] is False
    assert respuesta["result"]["isError"] is False


# ------------------------------------------------------------------- GUI
def test_gui_ventanas_ia_youtube_separadas() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover - entorno sin Qt
        print(f"AVISO GUI omitida: {exc}")
        return
    app = QApplication.instance() or QApplication([])

    from ui.main_window import VentanaPrincipal

    ventana = VentanaPrincipal("VideoCapitulosTest", "TestIA")
    try:
        pestañas = [
            ventana.tabs_inspector.tabText(i)
            for i in range(ventana.tabs_inspector.count())
        ]
        assert all("IA" not in pestaña and "YouTube" not in pestaña for pestaña in pestañas), pestañas
        assert ventana.panel_ia is not None
        # IA y YouTube ya no ocupan la cabecera: se abren desde los menús.
        menu_ventanas = next(
            a.menu() for a in ventana.menuBar().actions()
            if a.text().replace("&", "") == "Ventanas"
        )
        etiquetas = [a.text() for a in menu_ventanas.actions()]
        assert any("IA" in e for e in etiquetas), etiquetas
        assert any("YouTube" in e for e in etiquetas), etiquetas
        assert not hasattr(ventana, "btn_abrir_ia")
        ventana._abrir_ventana_ia()
        ventana._abrir_ventana_youtube()
        app.processEvents()
        assert ventana._ventana_ia is not None and ventana._ventana_ia.isVisible()
        assert ventana._ventana_youtube is not None and ventana._ventana_youtube.isVisible()
        assert ventana.panel_ia.parentWidget() is ventana._ventana_ia
        assert ventana._youtube_panel.parentWidget() is not ventana.tabs_inspector
        cfg = ventana.panel_ia.config_actual()
        assert cfg.modelo_texto == "nvidia/nemotron-3-ultra-550b-a55b:free"
        assert cfg.proveedor_imagen == "pollinations"
        assert cfg.generar_imagenes is True

        # Las imágenes generadas por IA se asignan por nombre, sin invertirse.
        with tempfile.TemporaryDirectory(prefix="gui_ia_") as tmp:
            carpeta = Path(tmp)
            nombres = ["intro_20260101_000000.jpg", "capitulo_01_20260101_000000.jpg",
                       "cierre_20260101_000000.jpg"]
            rutas = []
            for nombre in nombres:
                ruta = carpeta / nombre
                ruta.write_bytes(b"\xff\xd8\xff" + b"0" * 2048)
                rutas.append(str(ruta))
            ventana._ia_aplicar_imagenes(rutas)
            assert len(ventana.imagenes) == 3, ventana.imagenes

            marcas = [
                {"titulo": "Introducción", "tiempo": 0.0},
                {"titulo": "Capítulo 1", "tiempo": 20.0},
                {"titulo": "Cierre", "tiempo": 60.0},
            ]
            ventana.chk_sub_auto.setChecked(False)
            ventana._deteccion_lista(marcas)
            assert ventana.img_manual[0].endswith("intro_20260101_000000.jpg"), ventana.img_manual
            assert ventana.img_manual[1].endswith("capitulo_01_20260101_000000.jpg"), ventana.img_manual
            assert ventana.img_manual[2].endswith("cierre_20260101_000000.jpg"), ventana.img_manual
    finally:
        if ventana._ventana_ia is not None:
            ventana._ventana_ia.close()
        if ventana._ventana_youtube is not None:
            ventana._ventana_youtube.close()
        ventana._proyecto_modificado = False
        ventana.close()
        app.processEvents()


# --------------------------------------------------------------- YouTube
def test_youtube_fecha_programada() -> None:
    from datetime import datetime, timedelta, timezone

    from core.youtube_uploader import ErrorYouTube, _fecha_publicacion

    futura = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(timespec="seconds")
    normalizada = _fecha_publicacion(futura)
    assert normalizada.startswith(futura[:13]), normalizada
    assert _fecha_publicacion("") == ""

    pasada = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")
    try:
        _fecha_publicacion(pasada)
    except ErrorYouTube as exc:
        assert "5 minutos" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Una fecha pasada debía rechazarse")


def test_gui_youtube_idioma_y_programacion() -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from PyQt6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover - entorno sin Qt
        print(f"AVISO GUI omitida: {exc}")
        return
    app = QApplication.instance() or QApplication([])

    from ui.main_window import VentanaPrincipal

    ventana = VentanaPrincipal("VideoCapitulosTest", "TestIA")
    try:
        assert ventana.cbo_idioma_yt.count() > 1
        assert ventana.cbo_idioma_yt.findData("es") >= 0

        # Programar obliga a privado y bloquea el selector mientras esté activo.
        idx_publico = ventana.cbo_privacidad_youtube.findData("public")
        ventana.cbo_privacidad_youtube.setCurrentIndex(idx_publico)
        ventana.chk_programar.setChecked(True)
        app.processEvents()
        assert ventana.cbo_privacidad_youtube.currentData() == "private"
        assert ventana.cbo_privacidad_youtube.isEnabled() is False
        assert ventana.dt_publicar.isEnabled() is True
        assert ventana._publicar_en_iso(), "Debe devolver una fecha ISO"
        assert ventana._validar_programacion_ui() is True

        ventana.chk_programar.setChecked(False)
        app.processEvents()
        assert ventana.cbo_privacidad_youtube.isEnabled() is True
        assert ventana._publicar_en_iso() == ""

        # Los metadatos nuevos viajan al proyecto.
        ventana.cbo_idioma_yt.setCurrentIndex(ventana.cbo_idioma_yt.findData("es"))
        datos = ventana._recopilar_datos_proyecto()
        assert datos["youtube"]["idioma"] == "es"
        assert "programar_fecha" in datos["youtube"]
    finally:
        ventana._proyecto_modificado = False
        ventana.close()
        app.processEvents()


if __name__ == "__main__":
    pruebas = [valor for nombre, valor in sorted(globals().items()) if nombre.startswith("test_")]
    for prueba in pruebas:
        prueba()
        print(f"OK {prueba.__name__}")
    print(f"TODOS LOS TESTS OK ({len(pruebas)})")
