#!/usr/bin/env python3
"""
upload_youtube.py — загрузка готового Shorts из папки проекта.

Читает metadata.json проекта, грузит final_short.mp4 и (если есть)
thumbnail.jpg, пишет upload_result.json рядом.

    python3 upload_youtube.py --project ~/projects/case_x
    python3 upload_youtube.py --project ~/projects/case_x --privacy public
    python3 upload_youtube.py --project ~/projects/case_x --token ~/token.json

Токен по умолчанию — общий для канала: ~/projects/court-montage/token.json.
Канал сверяется с config.json проекта (youtube.expected_channel_id), чтобы
ролик не улетел не на тот аккаунт.
"""

import argparse
import json
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
DEFAULT_TOKEN = Path.home() / "projects" / "court-montage" / "token.json"


def load_creds(token: Path) -> Credentials:
    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            raise SystemExit(f"{token} недействителен и не обновляется")
    return creds


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--token", default=str(DEFAULT_TOKEN))
    ap.add_argument("--privacy", default=None,
                    help="переопределить privacyStatus из metadata.json")
    ap.add_argument("--video", default="final_short.mp4")
    args = ap.parse_args()

    proj = Path(args.project).expanduser().resolve()
    meta = json.loads((proj / "metadata.json").read_text(encoding="utf-8"))
    if args.privacy:
        meta["privacyStatus"] = args.privacy

    video = proj / args.video
    if not video.exists():
        raise SystemExit(f"нет файла {video}")

    config_file = proj / "config.json"
    expected = None
    if config_file.exists():
        expected = (json.loads(config_file.read_text(encoding="utf-8"))
                    .get("youtube", {}).get("expected_channel_id"))

    youtube = build("youtube", "v3", credentials=load_creds(Path(args.token)),
                    cache_discovery=False)
    item = youtube.channels().list(part="snippet", mine=True).execute()["items"][0]
    print(f"CHANNEL: {item['id']} {item['snippet']['title']}", flush=True)
    if expected and item["id"] != expected:
        raise SystemExit(f"чужой канал: {item['id']} != {expected}")

    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta["tags"],
            "categoryId": meta["categoryId"],
            "defaultLanguage": meta["defaultLanguage"],
            "defaultAudioLanguage": meta["defaultAudioLanguage"],
        },
        "status": {
            "privacyStatus": meta["privacyStatus"],
            "selfDeclaredMadeForKids": meta["selfDeclaredMadeForKids"],
            "embeddable": meta["embeddable"],
            "license": "youtube",
        },
    }

    media = MediaFileUpload(str(video), mimetype="video/mp4",
                            chunksize=8 * 1024 * 1024, resumable=True)
    req = youtube.videos().insert(part="snippet,status", body=body,
                                  media_body=media, notifySubscribers=False)
    response, retry = None, 0
    while response is None:
        try:
            status, response = req.next_chunk()
            if status:
                print(f"UPLOAD {int(status.progress() * 100)}%", flush=True)
            retry = 0
        except HttpError as e:
            if e.resp.status in (429, 500, 502, 503, 504) and retry < 6:
                retry += 1
                print(f"RETRY {retry}: {e}", flush=True)
            else:
                raise

    vid = response["id"]
    print(f"VIDEO_ID: {vid}", flush=True)

    thumb = proj / "thumbnail.jpg"
    if thumb.exists():
        try:
            youtube.thumbnails().set(
                videoId=vid,
                media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg"),
            ).execute()
            print("THUMBNAIL: ok", flush=True)
        except Exception as e:                      # noqa: BLE001
            print(f"THUMBNAIL ERROR: {e}", flush=True)

    result = {
        "video_id": vid,
        "url": f"https://youtu.be/{vid}",
        "studio": f"https://studio.youtube.com/video/{vid}/edit",
        "title": meta["title"],
        "privacy": meta["privacyStatus"],
        "channel": item["snippet"]["title"],
    }
    (proj / "upload_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"DONE {result['url']}", flush=True)


if __name__ == "__main__":
    main()
