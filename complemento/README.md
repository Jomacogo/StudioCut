# StudioCut AI · complemento MCP

Complemento oficial de **Studio Cut** para generar guiones, imágenes y voz con
IA desde un agente externo (Claude Desktop, Cursor, VS Code…).

> Este repositorio es el complemento. La app principal está en
> [Jomacogo/StudioCut](https://github.com/Jomacogo/StudioCut).

## Instalación

No se instala a mano. Abre Studio Cut → pestaña **«🤖 IA»** →
**«⬇ Buscar e instalar»**. La app:

1. Lee `plugin.json` de la rama `main`.
2. Descarga el ZIP de la Release indicada.
3. Verifica el **SHA-256**.
4. Extrae en `%LOCALAPPDATA%\StudioCut\plugins\studiocut-ai\<versión>`.

También puedes probar una copia local con **«📁 Desde carpeta…»**.

## Seguridad

- El servidor solo arranca con `--stdio` **y** la variable de entorno
  `STUDIOCUT_LAUNCH_TOKEN`, que genera Studio Cut al lanzarlo.
- Abrirlo con doble clic muestra un mensaje y termina.
- La extracción del ZIP rechaza rutas `../`, enlaces simbólicos y archivos
  demasiado grandes.
- Las claves viajan solo por el entorno del proceso
  (`STUDIOCUT_OPENROUTER_KEY`, `STUDIOCUT_ELEVENLABS_KEY`); nunca se escriben
  en disco desde el complemento.

## Herramientas MCP

| Herramienta | Qué hace |
| --- | --- |
| `generar_guion` | Guion con Introducción / Capítulo uno… / Cierre |
| `generar_imagen` | Una imagen (Pollinations gratis, OpenRouter u OpenAI) |
| `generar_voz` | Audio con ElevenLabs o el TTS local de Windows |
| `listar_modelos_gratis` | Modelos gratuitos de OpenRouter |
| `estado` | Versión y claves disponibles (sin mostrarlas) |

## Uso manual (desarrollo)

```bash
python -m studiocut_ai --stdio
```

Para usarlo sin el token de Studio Cut:

```bash
python -m studiocut_ai --stdio --permitir-externo
```

Configuración para un cliente MCP:

```json
{
  "mcpServers": {
    "studiocut-ai": {
      "command": "python",
      "args": ["-m", "studiocut_ai", "--stdio"],
      "cwd": "C:\\ruta\\a\\studiocut-ai",
      "env": {
        "STUDIOCUT_LAUNCH_TOKEN": "lo-genera-studio-cut",
        "STUDIOCUT_OPENROUTER_KEY": "sk-or-v1-..."
      }
    }
  }
}
```

## Empaquetar una versión

Desde esta carpeta:

```bash
python empaquetar.py
```

Genera `dist/studiocut-ai-<versión>.zip`, actualiza el `sha256` y la `url` de
`plugin.json`.

### Publicar (automático)

Con GitHub CLI instalado y la sesión iniciada (`gh auth login`):

```powershell
powershell -ExecutionPolicy Bypass -File publicar.ps1
```

El script empaqueta, crea (si hace falta) el repositorio
`Jomacogo/StudioCut-AI`, sube `main`, publica la Release `v<versión>` con el
ZIP y verifica que el `plugin.json` público coincide con el SHA-256 local.

### Publicar (manual)

1. Crea el repositorio `Jomacogo/StudioCut-AI` (público).
2. Sube el contenido de esta carpeta a `main`.
3. Crea una **Release** con la etiqueta `v<versión>` y adjunta el ZIP.
4. En Studio Cut pulsa **«🔄 Buscar actualización»**.

## Licencia

MIT.
