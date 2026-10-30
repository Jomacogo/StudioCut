"""Subida automática a YouTube con la Data API v3 (OAuth + resumable).

Requiere `pip install google-api-python-client google-auth-oauthlib
google-auth-httplib2` y un archivo `client_secret.json` descargado de
Google Cloud Console (ver README).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from core.ia.credenciales import desproteger_secreto, proteger_secreto

SCOPES = ["https://www.googleapis.com/auth/youtube"]
TOKEN_PATH = Path(__file__).resolve().parents[1] / "ui" / ".config" / "youtube_token.json"


def _guardar_token(token_path: Path, creds) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    protegido = proteger_secreto(creds.to_json())
    temporal = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=token_path.parent, prefix="youtube_", suffix=".tmp",
            delete=False,
        ) as archivo:
            temporal = Path(archivo.name)
            archivo.write(protegido)
        os.replace(temporal, token_path)
        if os.name == "nt":
            os.chmod(token_path, 0o600)
    finally:
        if temporal and temporal.exists():
            temporal.unlink()


def _leer_token(token_path: Path):
    from google.oauth2.credentials import Credentials

    if not token_path.exists():
        return None
    try:
        texto = desproteger_secreto(token_path.read_bytes())
        datos = json.loads(texto)
        return Credentials.from_authorized_user_info(datos)
    except Exception:
        # Compatibilidad con tokens JSON en claro guardados por versiones previas.
        try:
            return Credentials.from_authorized_user_file(str(token_path))
        except Exception:
            return None


class ErrorYouTube(Exception):
    pass


def _importar_deps():
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        return InstalledAppFlow, build, MediaFileUpload
    except ImportError as exc:
        raise ErrorYouTube(
            "Faltan las librerías de YouTube. Instala:\n"
            "pip install google-api-python-client google-auth-oauthlib"
        ) from exc


def _credenciales(client_secret: str = "", token_path: Path = TOKEN_PATH):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = _leer_token(token_path)
    if creds and not creds.has_scopes(SCOPES):
        # Los tokens antiguos que solo permitían subir no sirven para editar.
        creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _guardar_token(token_path, creds)
        except Exception:
            creds = None
    if not creds or not creds.valid:
        if not client_secret or not Path(client_secret).is_file():
            raise ErrorYouTube(
                "YouTube no está conectado o el permiso necesita renovarse. "
                "Selecciona el cliente OAuth de escritorio y pulsa «Conectar cuenta»."
            )
        InstalledAppFlow, _b, _m = _importar_deps()
        flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
        creds = flow.run_local_server(host="127.0.0.1", port=0, open_browser=True)
        _guardar_token(token_path, creds)
    return creds


def conectar_cuenta(client_secret: str, token_path: Path = TOKEN_PATH) -> dict:
    """Autoriza YouTube una vez y devuelve el canal conectado."""
    _importar_deps()
    creds = _credenciales(client_secret, token_path)
    youtube = _crear_servicio(creds)
    respuesta = youtube.channels().list(part="snippet", mine=True).execute()
    canales = respuesta.get("items") or []
    if not canales:
        raise ErrorYouTube("La cuenta autorizada no tiene un canal de YouTube disponible.")
    canal = canales[0]
    return {
        "id": canal.get("id", ""),
        "nombre": canal.get("snippet", {}).get("title", "Canal conectado"),
    }


def _crear_servicio(creds):
    _InstalledAppFlow, build, _MediaFileUpload = _importar_deps()
    return build("youtube", "v3", credentials=creds)


def _fecha_publicacion(valor: str) -> str:
    """Valida una fecha ISO y la devuelve normalizada para la API."""
    from datetime import datetime, timedelta, timezone

    texto = (valor or "").strip()
    if not texto:
        return ""
    try:
        fecha = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ErrorYouTube("La fecha de publicación programada no es válida.") from exc
    if fecha.tzinfo is None:
        fecha = fecha.astimezone()
    if fecha <= datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ErrorYouTube(
            "La publicación programada debe ser al menos 5 minutos en el futuro."
        )
    return fecha.isoformat(timespec="seconds")


def listar_videos(
    token_path: Path = TOKEN_PATH, max_resultados: int = 50, client_secret: str = ""
) -> list[dict]:
    """Lista los videos recientes del canal autorizado."""
    creds = _credenciales(client_secret, token_path)
    youtube = _crear_servicio(creds)
    canal = (youtube.channels().list(part="contentDetails", mine=True).execute().get("items") or [])
    if not canal:
        return []
    playlist = canal[0].get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    if not playlist:
        return []
    pagina = youtube.playlistItems().list(
        part="snippet,contentDetails", playlistId=playlist,
        maxResults=max(1, min(int(max_resultados), 50)),
    ).execute()
    ids = [item.get("contentDetails", {}).get("videoId") for item in pagina.get("items", [])]
    ids = [vid for vid in ids if vid]
    if not ids:
        return []
    detalles = youtube.videos().list(part="snippet,status", id=",".join(ids)).execute()
    return [{
        "id": video.get("id", ""),
        "titulo": video.get("snippet", {}).get("title", ""),
        "descripcion": video.get("snippet", {}).get("description", ""),
        "etiquetas": video.get("snippet", {}).get("tags", []),
        "privacidad": video.get("status", {}).get("privacyStatus", "unlisted"),
        "idioma": video.get("snippet", {}).get("defaultLanguage", ""),
        "programado": video.get("status", {}).get("publishAt", ""),
    } for video in detalles.get("items", [])]


def actualizar_video(
    video_id: str, titulo: str, descripcion: str, etiquetas: list[str] | None,
    privacidad: str, token_path: Path = TOKEN_PATH, miniatura: str | None = None,
    categoria: str = "24", client_secret: str = "",
    idioma: str | None = None, publicar_en: str = "",
) -> None:
    """Actualiza metadatos y, si se proporciona, miniatura de un video propio.

    ``idioma`` en ``None`` conserva el que ya tenía el video; ``""`` lo quita.
    ``publicar_en`` vacío elimina una programación previa.
    """
    if privacidad not in {"private", "unlisted", "public"}:
        raise ErrorYouTube("Privacidad no válida.")
    fecha_publicacion = _fecha_publicacion(publicar_en)
    if fecha_publicacion:
        privacidad = "private"
    creds = _credenciales(client_secret, token_path)
    youtube = _crear_servicio(creds)
    actual = youtube.videos().list(part="snippet,status", id=video_id).execute()
    videos = actual.get("items") or []
    if not videos:
        raise ErrorYouTube("No se encontró el video en el canal autorizado.")
    snippet = videos[0].get("snippet", {})
    status = videos[0].get("status", {})
    titulo = (titulo or "").strip()
    if not titulo:
        raise ErrorYouTube("El título del video no puede estar vacío.")

    # YouTube reemplaza los campos mutables de cada `part` enviado; conservar
    # los que este formulario no ofrece evita borrar categoría, idioma o estado.
    snippet_actualizado = {
        "title": titulo,
        "description": descripcion,
        "tags": etiquetas or [],
        "categoryId": categoria or snippet.get("categoryId") or "24",
    }
    if idioma is None:
        if snippet.get("defaultLanguage"):
            snippet_actualizado["defaultLanguage"] = snippet["defaultLanguage"]
    elif idioma:
        snippet_actualizado["defaultLanguage"] = idioma

    status_actualizado = {"privacyStatus": privacidad}
    for campo in ("license", "embeddable", "publicStatsViewable"):
        if campo in status:
            status_actualizado[campo] = status[campo]
    # Omitir publishAt al enviar `status` borra la programación, que es justo
    # lo que se espera al desmarcar «Programar publicación».
    if fecha_publicacion:
        status_actualizado["publishAt"] = fecha_publicacion

    cuerpo = {
        "id": video_id,
        "snippet": snippet_actualizado,
        "status": status_actualizado,
    }
    youtube.videos().update(part="snippet,status", body=cuerpo).execute()
    if miniatura and Path(miniatura).is_file():
        youtube.thumbnails().set(videoId=video_id, media_body=miniatura).execute()


def subir_video(
    video_path: str,
    titulo: str,
    descripcion: str,
    etiquetas: list[str] | None,
    client_secret: str,
    token_path: Path,
    miniatura: str | None = None,
    al_progreso=None,
    al_log=None,
    privacidad: str = "unlisted",
    categoria: str = "24",
    idioma: str = "",
    publicar_en: str = "",
) -> str:
    """Sube el video y devuelve su id de YouTube."""
    _d = _importar_deps()
    InstalledAppFlow, build, MediaFileUpload = _d
    if privacidad not in {"private", "unlisted", "public"}:
        raise ErrorYouTube("Privacidad no válida.")
    fecha_publicacion = _fecha_publicacion(publicar_en)
    creds = _credenciales(client_secret, token_path)
    youtube = build("youtube", "v3", credentials=creds)

    snippet_cuerpo = {
        "title": titulo or Path(video_path).stem,
        "description": descripcion or "",
        "tags": etiquetas or [],
        "categoryId": categoria or "24",  # 24 = Entretenimiento
    }
    if idioma:
        snippet_cuerpo["defaultLanguage"] = idioma

    status_cuerpo = {
        "privacyStatus": "private" if fecha_publicacion else privacidad,
        "selfDeclaredMadeForKids": False,
    }
    if fecha_publicacion:
        status_cuerpo["publishAt"] = fecha_publicacion

    cuerpo = {"snippet": snippet_cuerpo, "status": status_cuerpo}
    media = MediaFileUpload(
        video_path, chunksize=8 * 1024 * 1024, resumable=True, mimetype="video/mp4"
    )
    peticion = youtube.videos().insert(
        part=",".join(cuerpo.keys()), body=cuerpo, media_body=media
    )
    respuesta = None
    while respuesta is None:
        estado, respuesta = peticion.next_chunk()
        if estado and al_progreso:
            al_progreso(int(estado.progress() * 100))
        if not respuesta and al_log:
            al_log(f"Subida... {int((estado.progress() if estado else 0) * 100)}%")
    vid = respuesta["id"]
    if al_log:
        al_log(f"Video subido: https://youtu.be/{vid}")

    if miniatura and Path(miniatura).exists():
        try:
            youtube.thumbnails().set(videoId=vid, media_body=miniatura).execute()
            if al_log:
                al_log("Miniatura establecida.")
        except Exception as exc:  # noqa: BLE001
            if al_log:
                al_log(f"Aviso: no se pudo poner la miniatura ({exc}). "
                       "Requiere canal verificado.")
    return vid
