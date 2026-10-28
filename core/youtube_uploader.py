"""Subida automática a YouTube con la Data API v3 (OAuth + resumable).

Requiere `pip install google-api-python-client google-auth-oauthlib
google-auth-httplib2` y un archivo `client_secret.json` descargado de
Google Cloud Console (ver README).
"""
from __future__ import annotations

from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
# Puerto fijo y host fijos: el redirect_uri debe coincidir exactamente con
# el que registres en Google Cloud Console (ver README).
OAUTH_HOST = "localhost"
OAUTH_PORT = 8080
REDIRECT_URI = f"http://{OAUTH_HOST}:{OAUTH_PORT}/"


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


def _credenciales(client_secret: str, token_path: Path):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    creds = None
    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None
    if not creds or not creds.valid:
        InstalledAppFlow, _b, _m = _importar_deps()
        flow = InstalledAppFlow.from_client_secrets_file(
            client_secret, SCOPES,
            redirect_uri=REDIRECT_URI,
        )
        creds = flow.run_local_server(
            host=OAUTH_HOST,
            port=OAUTH_PORT,
            open_browser=True,
        )
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


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
) -> str:
    """Sube el video y devuelve su id de YouTube."""
    _d = _importar_deps()
    InstalledAppFlow, build, MediaFileUpload = _d
    creds = _credenciales(client_secret, token_path)
    youtube = build("youtube", "v3", credentials=creds)

    cuerpo = {
        "snippet": {
            "title": titulo or Path(video_path).stem,
            "description": descripcion or "",
            "tags": etiquetas or [],
            "categoryId": "24",  # 24 = Entretenimiento
        },
        "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False},
    }
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
