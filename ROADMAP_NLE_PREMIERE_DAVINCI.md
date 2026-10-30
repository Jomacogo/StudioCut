# 🎬 ROADMAP NLE: Studio Cut → Nivel Premiere Pro / DaVinci Resolve

> Ingeniería inversa de las aplicaciones de referencia y plan de mejoras para
> llevar **Studio Cut** al nivel de un editor profesional.

---

## 📐 Arquitectura de Referencia — Cómo están construidos

### Adobe Premiere Pro

| Componente | Cómo funciona internamente |
|---|---|
| **Motor de Timeline** | Secuencia basada en `tracks` (pistas V1-V99, A1-A99). Cada track es un contenedor ordenado de `clips`. Un clip referencia un `media item` en el Media Pool con `in/out points`, **no** copia el archivo. |
| **Trim Engine** | Distingue 5 modos: **Ripple** (elimina gap), **Roll** (mueve corte, afecta ambos clips), **Slip** (mueve in/out sin mover clip en timeline), **Slide** (mueve clip, ajusta vecinos), **Selection** (mover libremente). |
| **Razor Tool** | Click en pista → divide clip en dos con el mismo `media reference`. Aplica a una pista o a todas (`Shift+Click`). |
| **Snap / Imán** | Puntos de anclaje: playhead, inicio/fin de clips, marcadores, keyframes. Tolerancia configurable. |
| **Playback Engine** | Mercury Playback Engine: decodifica GPU/CPU en paralelo. Composita frames en tiempo real con pipeline de efectos. Usa proxy cuando la resolución es muy alta. |
| **Project File (.prproj)** | XML comprimido (gzip). Contiene: secuencias, bins (carpetas de medios), assets como rutas relativas, presets de render, metadatos. NO incluye los archivos multimedia, solo las rutas. |
| **Audio Waveform** | Pre-calcula waveforms `.pek` al importar. Se dibuja como polígono de amplitud por muestra en la pista de audio. Cache persistente. |
| **Multicam** | Sincroniza múltiples cámaras por timecode o audio waveform matching, luego permite switching en tiempo real. |
| **Keyframes** | Cada propiedad (opacidad, posición, volumen) tiene una curva de keyframes con interpolación bézier, lineal o hold. |

### DaVinci Resolve

| Componente | Cómo funciona internamente |
|---|---|
| **Base de datos de proyecto** | Usa PostgreSQL o SQLite para almacenar proyectos. El `.drp` exportable es JSON + metadata. Más robusto que archivos planos. |
| **Fairlight Audio** | Motor de audio dedicado con bus mixing, EQ paramétrico, compresor, limiter, de-esser por pista. Waveform renderizado con FFT. |
| **Fusion (Compositing)** | Editor de nodos (node graph). Cada nodo = operación (blur, merge, tracker, 3D transform). Pipeline de renderizado forward. |
| **Color Grading** | Node-based color pipeline. Cada nodo tiene LUT, curves, wheels, qualifiers, windows. Render order de nodos es serial + parallel. |
| **Cut Page** | Timeline simplificado con Smart Insert, Source Overwrite, y trim automático. Dos timelines simultáneos: uno global y uno de detalle. |
| **Speed Ramp / Retiming** | Curva de velocidad por clip con keyframes de speed%. El motor interpola frames (optical flow o frame blending). |
| **Render Queue** | Cola de exports con presets (H.264, ProRes, DNxHR). Cada job referencia una secuencia y un rango in/out. Render multinúcleo + GPU. |

---

## ✅ Funciones ya implementadas en Studio Cut

- [x] Timeline visual interactivo con pistas V1, A1, Marcadores
- [x] Playhead arrastrable con actualización en tiempo real del monitor
- [x] Regla de tiempo con subdivisiones dinámicas
- [x] Detección automática de capítulos por palabras clave en audio
- [x] Panel de configuración de palabras clave (intro, capítulos, cierre)
- [x] Arrastrar imágenes del Media Bin al Timeline (drag & drop)
- [x] Monitor de previsualización en vivo con Ken Burns y transiciones
- [x] Sistema de proyectos propios (.stcut) con guardar/abrir/guardar como
- [x] **Trim de bordes de clip** (arrastrar borde → resize)
- [x] **Herramienta Cuchilla (Razor)** con guía visual y snap
- [x] **Dividir en Playhead** (Ctrl+K)
- [x] **Snap magnético** a clips vecinos y playhead
- [x] **Zoom horizontal** del timeline
- [x] Atajos de teclado profesionales (V, C, S, Ctrl+K, Space, Ctrl+S)
- [x] **Undo / Rehacer** (Ctrl+Z / Ctrl+Y, botones ↶ ↷, 40 instantáneas)
- [x] **Forma de onda real** en A1 con caché en disco (FFmpeg + hilo aparte)
- [x] Duraciones de audio y miniaturas cacheadas (apertura de proyecto inmediata)
- [x] Generación de imágenes de la IA en paralelo (3 a la vez, orden preservado)
- [x] **IA local de imágenes** (ComfyUI / Forge por HTTP) con detección de GPU,
      VRAM y RAM, y bloqueo si no cumple los requisitos
- [x] **Workflow propio de ComfyUI** (Flux GGUF, LoRAs) con inyección de
      prompt, tamaño, semilla y pasos
- [x] **Plantilla de miniatura** con marcadores `{titulo}`/`{tema}` y visor
      opcional de los prompts de cada imagen
- [x] **Barra de menús** (Archivo, Editar, Ajustes, Ventanas, Ayuda) que
      sustituye los botones fijos de IA y YouTube en la cabecera

---

## 🔮 Mejoras Pendientes — Roadmap por Prioridad

### 🔴 Prioridad Alta (Próximas iteraciones)

#### 1. Ripple & Roll Edit
> **Referencia:** Premiere (Shift+B), DaVinci (tecla T)
- **Ripple Edit**: Al recortar un clip, todos los posteriores se desplazan para cerrar el gap automáticamente
- **Roll Edit**: Mover el punto de corte entre dos clips sin afectar la duración total
- Implementar en `timeline.py` como modos adicionales del trim engine

#### 2. Mover clips libremente en la pista
> **Referencia:** Arrastrar clip en Premiere/DaVinci
- Click + arrastrar en el cuerpo del clip (no en el borde) → reposicionar en el timeline
- Detectar superposición y ofrecer: overwrite, insert, o swap
- Snap al inicio/fin de clips adyacentes

#### 3. Audio Waveform en pista A1 ✅ (base implementada)
> **Referencia:** Premiere, DaVinci Fairlight
- [x] Al importar audio, pre-calcular waveform (amplitud cada 20 ms)
- [x] Dibujar la onda real en la pista A1 del timeline
- [x] Cache en disco (`ui/.config/wavecache/`) para no recalcular
- [ ] Mezcla por pista cuando existan A1/A2 independientes

#### 4. Undo/Redo completo ✅ (base implementada)
> **Referencia:** Ctrl+Z / Ctrl+Shift+Z en toda aplicación profesional
- [x] Pila de instantáneas del proyecto (40 pasos) con Ctrl+Z / Ctrl+Y y botones
- [x] Cubre marcas, clips, imágenes asignadas, subtítulos, efectos y metadatos
- [ ] Migrar a `QUndoStack` con comandos nombrados
- [ ] Mostrar historial de acciones en panel lateral

#### 5. Multi-track (V1, V2, A1, A2)
> **Referencia:** Premiere V1-V3, DaVinci V1-V16
- Permitir múltiples pistas de video (overlay, PIP) y audio (narración + música + SFX)
- Cada pista con mute/solo/lock
- Compositing por capas: V2 sobre V1 con opacidad y modos de mezcla

### 🟡 Prioridad Media (Mejoras de UX)

#### 6. Preview de thumbnails en clips del timeline
> **Referencia:** Premiere muestra thumbnails en cada clip
- Extraer frame representativo de cada segmento (primer frame o frame del 25%)
- Renderizar miniatura dentro del rectángulo del clip en V1

#### 7. Markers / Marcadores de edición
> **Referencia:** Premiere (M), DaVinci (M)
- Marcadores de color (rojo, verde, azul, amarillo) con notas de texto
- Diferentes a los capítulos: son puntos de referencia del editor
- Visibles en la regla de tiempo como banderitas

#### 8. Keyboard-driven editing (JKL)
> **Referencia:** Premiere, AVID, DaVinci
- J = Reproducir hacia atrás
- K = Pausa
- L = Reproducir hacia adelante
- JJ / LL = velocidad 2x, 4x, 8x
- Esencial para revisión rápida de material

#### 9. Slip & Slide Edit
> **Referencia:** Premiere (Y para slip, U para slide)
- **Slip**: Mover el contenido dentro del clip sin mover el clip en el timeline
- **Slide**: Mover el clip y ajustar los vecinos para mantener la duración total
- Requiere que cada clip tenga `in/out` points independientes del media

#### 10. Export Queue / Cola de renderizado
> **Referencia:** DaVinci Deliver page, Premiere Media Encoder
- Panel dedicado para configurar y encolar exports
- Presets: YouTube 1080p, Instagram Reels, TikTok, Custom
- Progreso visual por job, render en background

### 🟢 Prioridad Baja (Funciones avanzadas)

#### 11. Efectos y transiciones visuales
> **Referencia:** Premiere Effects panel, DaVinci Fusion
- Biblioteca de transiciones: dissolve, wipe, dip to black/white
- Arrastrar transición al punto de corte entre clips
- Panel de propiedades de la transición (duración, curva)

#### 12. Sistema de nodos para color/compositing
> **Referencia:** DaVinci Fusion / Color page
- Editor de nodos visual para procesamiento de imagen
- Nodos básicos: brillo/contraste, saturación, LUT, blur, sharpen
- Preview en tiempo real en el monitor

#### 13. Proxy Workflow
> **Referencia:** Premiere Proxies, DaVinci Optimized Media
- Generar versiones de baja resolución para edición fluida
- Al exportar, usar los originales en alta resolución
- Útil para equipos con hardware limitado

#### 14. Gestión avanzada de Media Pool
> **Referencia:** Premiere Bins, DaVinci Media Pool
- Carpetas / bins para organizar medios
- Metadata editable (nombre, notas, tags de color)
- Búsqueda y filtros
- Smart Bins basados en metadata

#### 15. Audio mixing con buses
> **Referencia:** DaVinci Fairlight, Premiere Audio Track Mixer
- Mixer visual con faders por pista
- Buses de submix (Diálogo, Música, SFX → Master)
- Medidor de nivel (VU meters) en tiempo real
- Plugins de audio: EQ, compresor, reverb

#### 16. Motion Graphics Templates
> **Referencia:** Premiere MOGRT, DaVinci Fusion Templates
- Títulos animados prediseñados
- Lower thirds, callouts, end cards
- Editor de propiedades (texto, color, duración)
- Librería de templates reutilizables

#### 17. Collaborative Editing
> **Referencia:** DaVinci Resolve Collaboration, Frame.io
- Múltiples editores en el mismo proyecto
- Bloqueo de timelines/bins
- Comentarios con timecodes

---

## 🏗️ Arquitectura técnica recomendada para escalar

```
Studio Cut/
├── core/
│   ├── timeline_engine.py      # Motor de secuencia: tracks, clips, ripple, roll
│   ├── clip.py                 # Modelo de clip: media_ref, in/out, speed, effects
│   ├── track.py                # Modelo de pista: tipo (V/A), clips[], mute/solo/lock
│   ├── undo_manager.py         # QUndoStack + comandos para cada operación
│   ├── waveform.py             # Generador y cache de waveforms de audio
│   ├── compositor.py           # Compositing de capas de video (V1+V2+...)
│   ├── effect_pipeline.py      # Pipeline de efectos por clip
│   ├── export_queue.py         # Cola de render con jobs y progreso
│   ├── proxy_manager.py        # Generación y gestión de proxies
│   ├── renderer.py             # (existente) Render final de video
│   ├── chapter_detection.py    # (existente) Detección de capítulos por audio
│   ├── media.py                # (existente) Utilidades de medios
│   ├── project.py              # (existente) Serialización de proyecto .stcut
│   └── ordering.py             # (existente) Ordenamiento de archivos
├── ui/
│   ├── main_window.py          # (existente) Ventana principal NLE
│   ├── trabajador.py           # (existente) Workers en hilos
│   └── widgets/
│       ├── timeline.py         # (existente) Widget de timeline visual
│       ├── monitor.py          # (existente) Monitor de previsualización
│       ├── waveform_widget.py  # Visualización de waveform de audio
│       ├── node_editor.py      # Editor de nodos para efectos/color
│       ├── mixer.py            # Audio mixer con faders y VU meters
│       └── effect_browser.py   # Navegador de efectos y transiciones
└── assets/
    ├── transitions/            # Presets de transiciones
    ├── templates/              # Templates de motion graphics
    └── luts/                   # Look-Up Tables para color grading
```

---

## 🔑 Decisiones técnicas clave

| Decisión | Recomendación | Razón |
|---|---|---|
| Modelo de clips | `ClipReference` → `MediaItem` | Como Premiere: el clip apunta al medio, no lo duplica. Permite relinkar. |
| Undo/Redo | `QUndoStack` de Qt | Nativo, probado, soporta grouping y macros. |
| Waveform cache | `.wfcache` en carpeta de proyecto | Evita recalcular en cada apertura. |
| Export | Subprocess + FFmpeg | Ya funciona. Escalar a cola de jobs con progreso. |
| Color/Efectos | Pipeline serial por clip | Simple y extensible. Los nodos son la siguiente iteración. |
| Multi-track | `list[Track]` en `Sequence` | Cada track tiene su tipo y lista de clips. |

---

## 📊 Comparativa actual vs objetivo

| Feature | Premiere | DaVinci | Studio Cut (Actual) | Studio Cut (Meta) |
|---|:---:|:---:|:---:|:---:|
| Timeline multitrack | ✅ | ✅ | ⚠️ 1 pista | ✅ Multi V+A |
| Trim (ripple/roll) | ✅ | ✅ | ⚠️ Solo resize | ✅ Completo |
| Razor/Split | ✅ | ✅ | ✅ | ✅ |
| Snap magnético | ✅ | ✅ | ✅ | ✅ |
| Audio waveform | ✅ | ✅ | ❌ | ✅ |
| Undo/Redo | ✅ | ✅ | ❌ | ✅ |
| Proxy workflow | ✅ | ✅ | ❌ | ✅ |
| Color grading | ✅ | ✅ | ❌ | 🟡 Básico |
| Node editor | ❌ | ✅ | ❌ | 🟡 |
| Motion graphics | ✅ | ✅ | ❌ | 🟡 |
| Proyecto propio | ✅ (.prproj) | ✅ (.drp) | ✅ (.stcut) | ✅ |
| Detección por audio | ❌ | ❌ | ✅ ¡Único! | ✅ |
| TTS integrado | ❌ | ❌ | ✅ ¡Único! | ✅ |
| Ken Burns auto | ❌ | ❌ | ✅ ¡Único! | ✅ |

> **Nota**: Las features marcadas como "¡Único!" son ventajas competitivas
> que ni Premiere ni DaVinci ofrecen de forma nativa. Son el diferenciador
> principal de Studio Cut.

---

*Última actualización: Octubre 2026*
*Versión del roadmap: 1.0*
