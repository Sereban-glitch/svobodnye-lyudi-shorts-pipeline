#!/usr/bin/env python3
"""
check_shorts.py — попал ли ролик в Shorts (без API-ключей и логина).

Почему это нужно отдельным скриптом: YouTube нигде в Data API не отдаёт
флаг «это Short». Единственный надёжный публичный признак — поведение
адреса /shorts/<id>:

  * 303 → редирект на /watch?v=<id>  → обычное длинное видео;
  * 200 и в странице есть плеер шортсов (reelWatchEndpoint) → ролик в Shorts.

Ограничение: для ПРИВАТНОГО видео проверить ничего нельзя. Поэтому
проверять надо на unlisted (или public).

Обязательно отправляем cookie SOCS/CONSENT: без них YouTube подсовывает
страницу согласия и отвечает 303 даже на нормальные шортсы.

    python3 check_shorts.py X7a5yDwEkgI
    python3 check_shorts.py --title "ТЦК не має права" X7a5yDwEkgI
"""

import argparse
import re
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Не идём по редиректу: сам факт редиректа — это и есть ответ."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def get(url: str) -> tuple:
    """Возвращает (код, заголовок Location, тело)."""
    opener = urllib.request.build_opener(NoRedirect)
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept-Language": "ru,en;q=0.8",
        # без этих cookie YouTube подсовывает страницу согласия и
        # отвечает 303/LOGIN_REQUIRED даже на нормальные шортсы
        "Cookie": "SOCS=CAI; CONSENT=YES+1",
    })
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, r.headers.get("Location", ""), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")
        except Exception:
            pass
        return e.code, e.headers.get("Location", ""), body


def main() -> None:
    ap = argparse.ArgumentParser(description="Проверка Shorts по video_id")
    ap.add_argument("video_id")
    ap.add_argument("--title", default="",
                    help="фрагмент названия: подтверждает, что страница про этот ролик")
    args = ap.parse_args()

    vid = args.video_id.strip()

    # Ответ бывает нестабильным, пока YouTube индексирует ролик: один
    # запрос может отдать редирект, следующий — нормальную страницу.
    for attempt in range(1, 4):
        code, location, body = get(f"https://www.youtube.com/shorts/{vid}")
        print(f"попытка {attempt}: /shorts/{vid} -> {code}"
              + (f", Location: {location}" if location else ""))
        if code == 200 and body:
            break
        time.sleep(4)

    print(f"video_id: {vid}")

    if code in (301, 302, 303, 307, 308) and "/watch" in location:
        print("РЕЗУЛЬТАТ: ❌ это НЕ Short — YouTube уводит на обычный плеер")
        sys.exit(1)

    if not body:
        print("РЕЗУЛЬТАТ: ⚠ страница не получена — повтори проверку позже")
        sys.exit(2)

    has_reel = "reelWatchEndpoint" in body
    title_ok = True
    if args.title:
        title_ok = args.title.lower() in body.lower()

    print(f"плеер шортсов: {'да' if has_reel else 'нет'}"
          + (f", название на странице: {'да' if title_ok else 'НЕТ'}" if args.title else ""))

    if code == 200 and has_reel and title_ok:
        print("РЕЗУЛЬТАТ: ✅ ролик в Shorts")
        sys.exit(0)

    if code == 200 and not title_ok:
        print("РЕЗУЛЬТАТ: ⚠ страница не про этот ролик (название не найдено)")
        sys.exit(2)

    print("РЕЗУЛЬТАТ: ❌ не похоже на Short")
    sys.exit(1)


if __name__ == "__main__":
    main()
