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

## Subida automática a YouTube (solo con título) 

Al momento de generar o subir se completan Título, Descripción, Etiquetas y
(opcionalmente) una **miniatura**; el video sube **exactamente con lo que
escribiste** en esos campos.

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
5. **En la misma pantalla del cliente OAuth**, en **URIs de redirección
   autorizados**, añade EXACTAMENTE:
   ```
   http://localhost:8080/
   ```
   (La app usa el puerto fijo 8080; si no lo añades, Google rechaza la
   solicitud con `redirect_uri_mismatch`.)
6. En la app: marca *Subir a YouTube al terminar* → **Buscar…** → elige el
   `client_secret.json` que descargaste.
7. La primera subida abrirá el navegador para iniciar sesión con la cuenta
   del canal (ojo: usa la cuenta del canal, no otra). El token queda
   guardado y las siguientes subidas no preguntan nada.

> Nota: videos subidos con la API quedan **ocultos (unlisted)** por defecto.
> Cámbialos a público desde YouTube Studio (es una protección típica para
> cuentas de prueba) o haciendo la app de Google *en producción*.

## Estructura

```
main.py             punto de entrada
core/ordering.py    orden intro/capítulos + emparejado audios↔imágenes
core/chapter_detection.py  transcripción Vosk + parseo de «capítulo N»
core/media.py       ffprobe/ffmpeg helpers
core/project.py     gestor de proyectos (.stcut) estilo DaVinci / Premiere
core/voz_tts.py     TTS de Windows: voces en español («Jorge» es-MX)
core/renderer.py    filtros ffmpeg: zoompan + xfade/acrossfade; modo con marcas
core/youtube_uploader.py  OAuth + subida resumable a la Data API v3
ui/main_window.py   workspace profesional NLE
ui/widgets/timeline.py     timeline interactivo con pistas y playhead
ui/widgets/monitor.py      motor de previsualización en vivo (audio + Ken Burns + crossfade)
ui/trabajador.py    QThreads: render, subida y detección de capítulos
styles.qss          tema oscuro NLE Studio
```
