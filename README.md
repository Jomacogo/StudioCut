# Creador de Videos por Capítulos (para YouTube)

App de escritorio (Python + PyQt6) para videos de YouTube con capítulos:

- **Modo «Varios audios»**: `intro.mp3`, `capitulo1.mp3`, `capitulo2.mp3`… la
  app los ordena sola y une todo en un video.
- **Modo «Un solo audio»**: subes tu audio completo y la app lo **transcribe
  con reconocimiento de voz offline (Vosk, en español)** para encontrar donde
  dices «introducción», «capítulo uno», «capítulo dos»… y cambia la imagen
  justo en ese momento. El audio no se corta ni se funde.
- **Previsualización en vivo (Motor de edición en tiempo real)**: Reproduce
  el audio sincronizado y compone el video en vivo en el monitor con zoom
  Ken Burns dinámico y previsualización de transiciones desvanecidas (crossfade)
  sin tener que esperar a que FFmpeg renderice.
- **Formato de proyectos (.stcut)**: Guarda todo el trabajo de edición
  (como `.prproj` de Premiere o `.drp` de DaVinci) con `Ctrl+S` / `Ctrl+Shift+S`
  y ábrelo con `Ctrl+O` para continuar después; solo cuando estés listo pulsa
  «Exportar MP4».
- **Imagen por capítulo**: cada imagen cubre exactamente el tiempo de su
  audio; el video cambia de imagen justo en el cambio de capítulo (con
  transición desvanecida o con varios efectos).
- **Ken Burns**: zoom in / zoom out alternado sobre cada imagen durante su tramo.
- **Modo «Texto → voz»**: pegas tu guion y la app lo lee con el TTS de
  Windows (el mismo motor de Clipchamp) y genera el audio completo;
  de la lista de voces instaladas elige primero **«Jorge» en Español (México)**
  si está, luego cualquier voz es-MX y por último cualquier voz en español.
  Puedes ajustar la velocidad; después detecta los capítulos igual que en
  el modo de un solo audio, con el cierre final incluido.

## Requisitos

1. **Python 3.10+** (probado con 3.12).
2. **FFmpeg y ffprobe** en el PATH.
   Windows: `winget install ffmpeg` o descarga de https://www.gyan.dev/ffmpeg/builds/
3. Dependencias:
   ```
   pip install -r requirements.txt
   ```

## Uso

```
python main.py
```

## Modo «Un solo audio» (el audio completo, como pides)

1. En **Modo** elige `Un solo audio completo (detectar capítulos)`.
2. **Elige el audio completo** y añade tus imágenes en orden
   (imagen 1 = intro/principio, imagen 2 = capítulo 1…).
3. Pulsa **«🎙 Detectar capítulos»**: la app transcribe el audio y coloca
   una marca en el segundo exacto donde escucha «capítulo N»
   (entiende «cinco», «veinticinco», «treinta y dos», dígitos),
   la «introducción» y el **«cierre»** final (también «outro»,
   «despedida», «conclusión»); el cierre queda como última marca y
   las menciones sueltas en medio del audio se descartan.
   - La primera vez descarga un modelo de voz en español (~49 MB).
   - Un audio de 1 hora suele detectarse en unos minutos.
4. **Revisa la tabla**: puedes corregir el segundo de cada marca, añadir
   marcas con «+ Marca» o quitarlas. Cambia la imagen de cada fila si
   alguna no coincide.
5. «Probar estilo (rápido)» para ver el efecto antes del render final.

El audio final queda **intacto** (sin fundidos ni cortes); solo las imágenes
cambian con su efecto en cada marca.

## Modo «Texto → voz» (guion pegado, como Clipchamp)

1. En **Modo** elige `Texto → voz (TTS Windows)`, pega tu guion completo
   (introducción, capítulos y cierre) en el cuadro de texto.
2. Elige la voz (por defecto toma **Jorge, Español (México)** si está
   instalada; si no, la mejor voz en español disponible) y la velocidad.
3. Pulsa **«🎙 Generar voz»**: crea el WAV del audio completo y después
   **detecta los capítulos automáticamente** con el mismo motor del modo
   anterior (introducción, capítulo N, cierre). Puedes re-detectar o corregir
   las marcas en la tabla.
4. Añade tus imágenes en la tabla de marcas (una imagen por tramo) y
   genera el video igual que en los otros modos.

> Nota: Clipchamp usa las voces SAPI de Windows; las que aparezcan en el
> combo son las que tengas instaladas en Windows (Ajustes → Hora e idioma →
> Voz → Añadir voces). Si tu voz de Clipchamp no aparece, instala ese
> paquete de voz y la app la tomará sola.

## Inteligencia artificial (casi automática)

La pestaña **«🤖 IA»** crea un video completo desde una sola idea:

1. Escribe el tema y pulsa **«⚡ Generar todo automáticamente»**.
2. La app genera el **guion** con estructura `Introducción` → `Capítulo uno`,
   `Capítulo dos`… → `Cierre` (así Vosk los reconoce después).
3. Genera **una imagen por capítulo** con los nombres `intro_*`,
   `capitulo_NN_*` y `cierre_*`, de modo que se asignan solas a las marcas.
4. Genera la **voz** del guion y detecta capítulos y subtítulos.
5. Si quieres, pulsa **«✨ Generar solo el guion»** para revisarlo antes.

### Texto (guion)

- **OpenRouter** con `nvidia/nemotron-3-ultra-550b-a55b:free` por defecto.
  Si el modelo gratuito falla, prueba automáticamente otros modelos gratis.
- También admite cualquier API **OpenAI-compatible** (Groq, DeepSeek…) y
  **Ollama local** sin clave.
- El guion se repara una vez si el modelo olvida el cierre o devuelve
  encabezados con markdown.

### Imágenes

- **Pollinations · Flux** por defecto: gratis y sin clave (limita a 1 imagen
  cada 45 s; la app espera y reintenta sola).
- **OpenRouter · Gemini** y **OpenAI · gpt-image** como opciones de pago.
- **IA local** (ComfyUI o Forge/AUTOMATIC1111) si tu GPU cumple los
  requisitos: sin coste por imagen y sin enviar nada a internet. Ver
  [IA local](#ia-local-generar-imágenes-en-tu-propio-pc).
- Si el proveedor de pago falla, se reintenta con Pollinations si está
  marcado «usar Pollinations gratis».
- Estilos: cinematográfico, ilustración, 3D, minimalista, acuarela y futurista.

### Miniatura y prompts de cada imagen

En el bloque **«Materiales y prompts visuales»** del asistente de IA:

- **Mi plantilla de miniatura** — si escribes ahí un prompt, se usa para
  **todas** las miniaturas en lugar de dejar que la IA lo improvise. Admite
  los marcadores `{titulo}` y `{tema}`, que se sustituyen al generar. El botón
  **«Usar el ejemplo de mi canal»** rellena la plantilla con el estilo de
  estatua de mármol, fondo negro y tipografía dorada.
- **Prompt de la miniatura actual** — lo que realmente se envía al generar o
  regenerar esa miniatura; se puede editar a mano.
- **«Mostrar los prompts de cada imagen (avanzado)»** — desmarcado, la IA
  crea el prompt de cada capítulo y lo coloca en el video sin mostrarlo.
  Marcado, aparece la lista de secciones para revisar o cambiar cada prompt y
  regenerar una escena concreta. La preferencia se guarda en el proyecto.

### Voz

- **Windows / Edge** con las voces neuronales de siempre (Jorge es-MX).
- **ElevenLabs** solo si tienes cuenta: guarda la clave, carga tus voces y
  elige una. Si falla o no hay clave, se usa el TTS de Windows sin cortar
  el flujo.

### Claves y privacidad

Las claves se guardan **cifradas con DPAPI de Windows** en
`ui/.config/ia_credenciales.json` (excluido por `.gitignore`). Nunca se
escriben en el proyecto `.stcut`, en `QSettings`, en los logs ni en el
repositorio. Solo las puede descifrar tu usuario de Windows en ese equipo.
Puedes borrarlas con el botón **«Borrar»**.

## Complemento MCP (opcional)

El complemento permite que un agente externo (Claude Desktop, Cursor…) use
las mismas capacidades de IA de Studio Cut. Se instala **solo desde la
asistente de IA (menú **Ajustes** o **Ventanas → Asistente de IA**)**:

1. **«⬇ Buscar e instalar»**: lee el `plugin.json` publicado, descarga el ZIP
   y **verifica el SHA-256** antes de instalar.
2. **«📁 Desde carpeta…»**: instala una copia local para probar antes de
   publicar.
3. **«▶ Probar»**: arranca el servidor y comprueba que responde.
4. **«📋 Copiar configuración MCP»**: copia el JSON para tu cliente.

Detalles de seguridad:

- Se instala en `%LOCALAPPDATA%\StudioCut\plugins\studiocut-ai\<versión>`.
- La extracción rechaza rutas `../`, enlaces simbólicos y ZIP gigantes.
- El servidor exige `--stdio` y la variable `STUDIOCUT_LAUNCH_TOKEN`; el token
  solo lo genera Studio Cut al lanzarlo. Abrirlo con doble clic no hace nada.
- El complemento vive en un repositorio aparte
  (`Jomacogo/StudioCut-AI`) para poder actualizarlo sin tocar la app.

## IA local: generar imágenes en tu propio PC

Studio Cut puede generar las imágenes con un programa instalado en tu
equipo. No hay coste por imagen ni se sube nada a internet; el precio es la
velocidad: cada imagen tarda entre 20 y 90 segundos según la tarjeta.

**Requisitos** (el panel los comprueba solo al abrir el asistente):

| | Mínimo | Cómodo |
|---|---|---|
| GPU | NVIDIA con **6 GB** de VRAM | 10 GB o más |
| RAM | 12 GB | 16 GB o más |

Con **8 GB de VRAM** (RTX 4060 Ti, 3060 Ti, 3070…) funciona:

- **SDXL** a 1024² en fp16, o **SDXL-Turbo/Lightning** (4-8 pasos, muy rápido).
- **Flux.1-schnell** en **fp8 o GGUF Q4** (~6,8 GB de VRAM). Mejor calidad de
  texto y composición; más lento que SDXL-Turbo.
- Con 10 GB o más: Flux fp8 con más margen y SDXL con refiner.

**Instalación** (una vez):

1. Instala **ComfyUI** (recomendado) o **Forge / AUTOMATIC1111** para Windows.
2. Descarga un checkpoint que quepa en tu VRAM y colócalo en
   `ComfyUI/models/checkpoints` (`sdxl-turbo.safetensors`,
   `flux1-schnell-fp8.safetensors`…).
3. Abre ComfyUI y déjalo abierto: escucha en `http://127.0.0.1:8188`.
   En Forge, arranca con `--api` (escucha en `:7860`).
4. En la app: **Imágenes → proveedor «IA local en tu PC»**, pulsa
   **«🔍 Buscar programa»**, elige el modelo y **«▶ Probar»**.

Detalles útiles:

- La app **no descarga modelos** ni arranca el servidor por su cuenta: solo
  se conecta a `127.0.0.1` y avisa con un mensaje claro si no hay nada
  escuchando. Por seguridad rechaza cualquier dirección que no sea local.
- Las imágenes locales se generan **de una en una**: comparten una sola GPU
  y lanzar dos a la vez solo provoca errores de memoria.
- **Flux en GGUF, LoRAs o workflows propios**: en ComfyUI usa
  *Workflow → Export (API)*, guarda el JSON y elígelo en el campo
  **«Workflow»**. La app inyecta el prompt, el tamaño, la semilla y los pasos
  en ese workflow (primer nodo de texto = positivo, segundo = negativo).
- Si el backend falla, se puede mantener marcado «usar Pollinations gratis»
  como red de seguridad.

## Ventanas y menús

El editor conserva en el inspector **Ajustes Video**, **Detección** y
**Subtítulos**. La IA y YouTube viven en ventanas aparte que se abren desde
la barra de menús, no desde botones fijos: la cabecera queda despejada.

- **Archivo** — nuevo, abrir, guardar, guardar como, añadir audios/imágenes,
  exportar MP4 y salir.
- **Editar** — deshacer, rehacer y detectar capítulos.
- **Ajustes** — asistente de IA y claves, publicación en YouTube y
  comprobación de requisitos de la IA local.
- **Ventanas** — abrir el asistente de IA (`Ctrl+I`), YouTube
  (`Ctrl+Shift+Y`) o cerrar las ventanas flotantes (`Ctrl+Shift+W`).
- **Ayuda** — guía rápida (`F1`), instalar la IA local y «Acerca de».

Las dos ventanas trabajan sobre el mismo proyecto, guion y metadatos; puedes
tenerlas abiertas junto al editor o cerrarlas sin perder nada.

## YouTube: conectar una vez, publicar y editar

Al generar o subir se completan Título, Descripción, Etiquetas, **idioma**,
privacidad y (opcionalmente) una **miniatura**; el video sube **exactamente
con lo que escribiste** en esos campos.

La ventana está dividida en cuatro secciones: **Cuenta**, **Metadatos**,
**Publicación** y **Editar un video ya publicado**.

- **Idioma del video** → se envía como `snippet.defaultLanguage` (por ejemplo
  `es`). Ayuda a que YouTube indexe bien el video y a las traducciones.
- **Programar publicación** → marca la casilla y elige fecha y hora. El video
  se sube **privado** y YouTube lo publica solo en ese momento (`publishAt`).
  Mientras la casilla esté marcada, la privacidad queda bloqueada en *Privado*
  porque YouTube lo exige. Desmárcala y guarda para **cancelar** una
  programación.
- **Desconectar** borra la autorización guardada en el equipo (no toca tus
  videos ni el proyecto).

- **Con título lleno** → al terminar el render se sube a YouTube con
  título/descripción/miniatura tal cual.
- **Sin título** → no se sube nada: queda **pendiente de subir** y aparece
  el botón **«⬆ Subir pendiente…»** para subirlo después con tus datos;
  cada video pendiente queda registrado en los ajustes de la app.
- **Revisar antes de subir**: marca **«Revisar el video antes de subir»**
  en el panel de YouTube: al terminar el render NO se sube nada, el video
  se abre solo en tu reproductor y queda pendiente; cuando te convenza,
  pulsa **«⬆ Subir pendiente…»** y ahí sí va a YouTube con "el título"
  descripción/miniatura actuales.
- El botón **«▶ Ver video»** abre el último video renderizado en tu
  reproductor en cualquier momento; **«Abrir carpeta»** abre su carpeta.
- **El video siempre se guarda en MP4** (si escribiste otra extensión, la
  app la cambia a `.mp4`).

Configuración (una sola vez):

1. Ve a https://console.cloud.google.com/
   - Crea un proyecto (puedes usar uno existente).
2. **APIs y servicios → Biblioteca** → activa **YouTube Data API v3**.
3. **Pantalla de consentimiento OAuth**: tipo *Externo*, completa lo
   mínimo; en **Usuarios de prueba** añade la cuenta de Gmail de tu canal.
4. **Credenciales → Crear credenciales → ID de cliente OAuth** → tipo
   *Aplicación de escritorio* → descarga el JSON.
5. No hace falta registrar una URI de redirección: los clientes de tipo
   *Aplicación de escritorio* usan un puerto local aleatorio
   (`http://127.0.0.1:…`).
6. En la app, abre **Ventanas → YouTube** → **Configurar…** → elige el
   `client_secret.json` y pulsa **🔗 Conectar cuenta**.
7. El navegador se abrirá una sola vez para autorizar la cuenta del canal
   (ojo: usa la cuenta del canal, no otra). El token queda guardado cifrado
   en este equipo y ya puedes **subir**, **cargar tus videos** y **guardar
   cambios** de título, descripción, etiquetas, idioma, privacidad, fecha
   programada y miniatura sin volver a autorizar.

> Notas:
> - El permiso solicitado permite publicar y **editar** videos; si conectaste
>   la cuenta con una versión anterior, vuelve a pulsar «Conectar cuenta» una
>   vez para ampliarlo.
> - La privacidad se elige en el panel (Privado / Oculto / Público). Los
>   videos nuevos se suben con la que tengas seleccionada.
> - Si la app de Google está en modo *Prueba*, solo autorizan las cuentas
>   añadidas como **Usuarios de prueba** y la autorización caduca a los 7 días.
> - Editar un video reenvía también categoría, idioma y ajustes de estado que
>   el formulario no muestra, para no borrarlos.

## Editor: deshacer, onda real y velocidad

- **Deshacer / Rehacer** — botones **↶ ↷** en la barra de la línea de tiempo y
  **Ctrl+Z / Ctrl+Y** (también Ctrl+Shift+Z). Guarda hasta 40 instantáneas del
  proyecto; se reinicia al abrir o crear uno nuevo. Usa un temporizador que
  compara una huella barata del proyecto, así que no penaliza la edición.
- **Forma de onda real** en la pista **A1** (antes era decorativa). Se calcula
  con FFmpeg en un hilo aparte y se guarda en `ui/.config/wavecache/`; al
  reabrir el proyecto se dibuja al instante. Mientras se analiza, la pista
  muestra una línea base con el aviso «analizando forma de onda…».
- **Duraciones de audio en caché** — `ffprobe` tarda ~100 ms por archivo y se
  consultaba decenas de veces (tabla, timeline, subtítulos). Ahora se memoriza
  por ruta + fecha + tamaño: la segunda consulta tarda **0,17 ms**.
- **Miniaturas ligeras** — las listas y combos de imágenes usan iconos de
  112×63 cacheados en lugar de cargar el archivo completo: menos RAM y listas
  que se abren al instante.
- **Monitor con imágenes reducidas** — la previsualización escala a 1600 px de
  ancho y limita la caché a 120 imágenes; el render final sigue usando los
  originales.
- **Imágenes de la IA en paralelo** — el pipeline genera 3 imágenes a la vez
  (2 con Pollinations por su límite gratuito) manteniendo el orden de los
  capítulos: 8 imágenes pasan de 9,6 s a 3,6 s en la prueba de humo.

## Estructura

```
main.py             punto de entrada
core/ordering.py    orden intro/capítulos + emparejado audios↔imágenes
core/chapter_detection.py  transcripción Vosk + parseo de «capítulo N»
core/media.py       ffprobe/ffmpeg helpers (duraciones cacheadas)
core/hardware.py    detección de GPU/VRAM/RAM para la IA local
core/ia/local.py    cliente de ComfyUI y Forge/A1111 por HTTP local
core/waveform.py    picos de audio para la forma de onda del timeline (con caché)
core/project.py     gestor de proyectos (.stcut) estilo DaVinci / Premiere
core/voz_tts.py     TTS de Windows: voces en español («Jorge» es-MX)
core/renderer.py    filtros ffmpeg: zoompan + xfade/acrossfade; modo con marcas
core/youtube_uploader.py  OAuth + subida resumable a la Data API v3
core/ia/            guion, imágenes, voz y pipeline de IA (sin dependencias nuevas)
core/plugins.py     instalación verificada del complemento MCP
ui/main_window.py   workspace profesional NLE
ui/widgets/panel_ia.py     ventana «🤖 IA» y gestor del complemento
ui/widgets/timeline.py     timeline interactivo con pistas, playhead y onda real
ui/widgets/miniaturas.py   iconos de lista escalados y cacheados
ui/widgets/monitor.py      motor de previsualización en vivo (audio + Ken Burns + crossfade)
ui/trabajador.py    QThreads: render, subida, detección de capítulos, IA y onda
complemento/        fuente del complemento MCP (repo StudioCut-AI)
styles.qss          tema oscuro NLE Studio
```
