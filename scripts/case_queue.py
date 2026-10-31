#!/usr/bin/env python3
"""
case_queue.py — очередь дел с domdara.org.

Название с префиксом не случайно: файл queue.py в этой папке перекрывал
стандартный модуль Python `queue`, и faster-whisper в generate_subtitles.py
падал с «cannot import name Queue from queue».

Сайт работает на WordPress с открытым REST API, поэтому «что нового и что
уже обработано» можно вести автоматически, без ручного списка ссылок.

Состояние (что уже снято) хранится локально в
  ~/.config/opencode/skills/domdara-shorts/state/processed.json
и не зависит от сайта: даже если пост удалят, отметка останется.

Команды:
  python3 case_queue.py sync                 # подтянуть список постов категории «Право»
  python3 case_queue.py list --limit 15      # показать необработанные, с оценкой
  python3 case_queue.py next                 # следующее дело (JSON, для автопилота)
  python3 case_queue.py stats                # сколько всего / обработано / осталось
  python3 case_queue.py mark 5800 --project ~/projects/case_5800 --status private
  python3 case_queue.py mark 5787 --status skipped --note "только ссылка на YouTube"
  python3 case_queue.py unmark 5800          # снять отметку (переделать)
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SITE = "http://domdara.org"
API = f"{SITE}/wp-json/wp/v2"
CATEGORY_SLUG = "pravo"          # рубрика «Право»
STATE_DIR = Path(__file__).resolve().parent.parent / "state"
CACHE_FILE = STATE_DIR / "posts_cache.json"
PROCESSED_FILE = STATE_DIR / "processed.json"
UA = "domdara-shorts/1.0 (+local pipeline)"

# Что интересно аудитории канала (вес в оценке «видеопригодности»)
TOPIC_WEIGHTS = {
    "тцк": 3, "тцк и сп": 3, "військовозобов": 3, "военнообяз": 3,
    "мобілізац": 3, "мобилизац": 3, "влк": 3, "військово-лікарськ": 3,
    "оберіг": 3, "оберег": 3, "резерв+": 3, "резерв плюс": 3,
    "відстрочк": 2, "отсрочк": 2, "бронюван": 2, "бронирован": 2,
    "повістк": 2, "повестк": 2, "призов": 2, "контракт": 2,
    "пенсі": 2, "субсиді": 2, "виплат": 2, "выплат": 2, "допомог": 2,
    "соціальн": 2, "социальн": 2, "інвалід": 2, "инвалид": 2,
    "звільнен": 2, "увольнен": 2, "пільг": 2, "льгот": 2,
    "права людини": 2, "права люди": 2, "суд": 1, "позов": 1, "иск": 1,
    "касац": 1, "верховн": 1, "апеляц": 1,
}

LEGAL_MARKERS = (
    "решение именем", "рішення іменем", "постановление", "постанова",
    "ухвала", "приговор", "вирок", "административное дело",
    "адміністративна справа", "дело №", "справа №", "справі №",
)
REPOST_MARKERS = (
    "репост", "закликаємо", "закликаем", "підписуйтесь", "подписывайтесь",
    "youtube.com", "дивіться відео", "смотрите видео", "телеграм", "telegram",
)

CASE_RE = re.compile(
    r"(?:дело|справа|справі|справ[аи])\s*[№N]?\s*"
    r"(\d{1,4}\s*[/\-]\s*\d{1,6}\s*[/\-]\s*\d{1,6})",
    re.IGNORECASE,
)
COURT_RE = re.compile(
    r"([А-ЯІЇЄҐа-яіїєґ][А-ЯІЇЄҐа-яіїєґ\s\.\-]{3,90}?"
    r"(?:адміністративн|административн|районн|окружн|апеляційн|апелляционн|"
    r"верховн|міськ|городськ|горрайонн|господарськ)\w*\s+суд\w*)",
    re.IGNORECASE,
)


COURT_STEMS = (
    "адміністративн", "административн", "районн", "окружн", "апеляційн",
    "апелляционн", "верховн", "міськ", "городськ", "горрайонн", "господарськ",
)


def find_court(text: str) -> str:
    """Название суда из шапки решения.

    Берём фразу, оканчивающуюся на «суд/суда/судом», и отступаем назад по
    определениям («окружной административный»), плюс одно слово-топоним
    («Ахтырского», «Днепропетровский»). Так отсекается мусор вроде
    «г. Киев дело №… судья …».
    """
    m = COURT_RE.search(text[:5000])
    if not m:
        return ""
    words = m.group(1).split()
    # именно «суд/суда/судом», но не «судья»
    idx = next((i for i, w in enumerate(words)
                if re.fullmatch(r"суд(а|у|ом|і|ові)?", w.lower().rstrip(".,:;"))), None)
    if idx is None:
        return " ".join(words)[:120]

    start = idx
    while start - 1 >= 0 and words[start - 1].lower().startswith(COURT_STEMS):
        start -= 1
    if start - 1 >= 0:
        prev = words[start - 1].lower().rstrip(".,")
        if re.search(r"(ськ|цьк|зьк|ск)\w{0,3}$", prev):
            start -= 1
    return " ".join(words[start:idx + 1])[:120]
DATE_RE = re.compile(
    r"(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|серпня|"
    r"вересня|жовтня|листопада|грудня|января|февраля|марта|апреля|мая|июня|"
    r"июля|августа|сентября|октября|ноября|декабря)\s+(\d{4})",
    re.IGNORECASE,
)

MONTHS = {
    "січня": 1, "лютого": 2, "березня": 3, "квітня": 4, "травня": 5, "червня": 6,
    "липня": 7, "серпня": 8, "вересня": 9, "жовтня": 10, "листопада": 11, "грудня": 12,
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}


# --------------------------------------------------------------------------- #
#  HTTP и состояние
# --------------------------------------------------------------------------- #

def fetch(url: str) -> tuple:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8")), dict(r.headers)


def ensure_state() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for f, default in ((CACHE_FILE, {"posts": {}}), (PROCESSED_FILE, {"processed": {}})):
        if not f.exists():
            f.write_text(json.dumps(default, ensure_ascii=False, indent=2), encoding="utf-8")


def load(path: Path) -> dict:
    ensure_state()
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, data: dict) -> None:
    ensure_state()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def strip_html(raw: str) -> str:
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def category_id() -> int:
    cats, _ = fetch(f"{API}/categories?slug={CATEGORY_SLUG}&_fields=id,count,name")
    if not cats:
        raise SystemExit(f"рубрика {CATEGORY_SLUG} не найдена")
    return cats[0]["id"]


# --------------------------------------------------------------------------- #
#  Синхронизация каталога
# --------------------------------------------------------------------------- #

def cmd_sync(args) -> None:
    cat = category_id()
    cache = load(CACHE_FILE)
    known = cache["posts"]
    page = 1
    added = 0
    total = None

    while True:
        url = (f"{API}/posts?categories={cat}&per_page=100&page={page}"
               f"&orderby=date&order=desc"
               f"&_fields=id,date,modified,link,slug,title,excerpt,categories")
        try:
            batch, headers = fetch(url)
        except urllib.error.HTTPError as e:
            if e.code == 400:      # страницы закончились
                break
            raise
        if total is None:
            total = int(headers.get("X-WP-Total", 0))
        if not batch:
            break

        for p in batch:
            pid = str(p["id"])
            title = strip_html(p["title"]["rendered"])
            excerpt = strip_html(p["excerpt"]["rendered"])
            if pid in known and known[pid]["modified"] == p["modified"]:
                continue
            known[pid] = {
                "id": p["id"],
                "date": p["date"],
                "modified": p["modified"],
                "slug": p["slug"],
                "link": p["link"],
                "title": title,
                "excerpt": excerpt,
            }
            added += 1

        if len(batch) < 100:
            break
        page += 1
        time.sleep(0.4)

    cache["category_id"] = cat
    cache["total_on_site"] = total
    cache["synced_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save(CACHE_FILE, cache)
    print(f"[Queue] Синхронизация: на сайте {total}, в кэше {len(known)}, обновлено {added}")


# --------------------------------------------------------------------------- #
#  Оценка «стоит ли снимать»
# --------------------------------------------------------------------------- #

def score_post(post: dict) -> tuple:
    text = f"{post.get('title','')} {post.get('excerpt','')}".lower()
    score = 0
    hits = []
    for kw, w in TOPIC_WEIGHTS.items():
        if kw in text:
            score += w
            hits.append(kw)

    # совсем короткие посты — обычно просто ссылка на видео
    excerpt_len = len(post.get("excerpt", ""))
    if excerpt_len < 120:
        score -= 6
    elif excerpt_len > 600:
        score += 1

    # решение суда или агитка?
    if any(m in text for m in LEGAL_MARKERS):
        score += 4
        hits.append("решение")
    elif any(m in text for m in REPOST_MARKERS):
        score -= 8

    return score, sorted(set(hits))


def unprocessed() -> list:
    cache = load(CACHE_FILE)
    done = load(PROCESSED_FILE)["processed"]
    items = []
    for pid, post in cache["posts"].items():
        if pid in done:
            continue
        score, hits = score_post(post)
        items.append({**post, "score": score, "topics": hits})
    items.sort(key=lambda x: (-x["score"], x["date"]))
    return items


def cmd_list(args) -> None:
    items = unprocessed()
    if args.min_score is not None:
        items = [i for i in items if i["score"] >= args.min_score]
    items = items[:args.limit]
    if not items:
        print("[Queue] Необработанных нет — запусти sync или всё уже снято.")
        return
    print(f"{'id':>6}  {'дата':10}  {'оц':>3}  заголовок")
    for i in items:
        print(f"{i['id']:>6}  {i['date'][:10]:10}  {i['score']:>3}  {i['title'][:74]}")
        if args.verbose and i["topics"]:
            print(f"{'':6}  {'':10}  {'':>3}  темы: {', '.join(i['topics'][:8])}")


def cmd_next(args) -> None:
    """Следующее дело: оценка по отрывку, затем проверка по полному тексту.

    Отрывок бывает обманчив (агитка, репост, подборка ссылок), поэтому
    кандидатов из топа проверяем на наличие номера дела в самом решении.
    """
    items = [i for i in unprocessed() if i["score"] >= args.min_score]
    if not items:
        print(json.dumps({"status": "empty"}, ensure_ascii=False))
        return

    checked = 0
    for post in items:
        if checked >= args.max_checks:
            break
        checked += 1
        info = extract_case(post["id"])
        if not info["case_number"]:
            continue
        print(json.dumps({
            "status": "ok",
            "post_id": post["id"],
            "url": post["link"],
            "title": post["title"],
            "published": post["date"],
            "score": post["score"],
            "topics": post["topics"],
            "excerpt": post["excerpt"][:400],
            "case_number": info["case_number"],
            "court_name": info["court_name"],
            "decision_date": info["decision_date"],
            "text_length": info["text_length"],
        }, ensure_ascii=False, indent=2))
        return

    print(json.dumps({"status": "no_decisions",
                      "checked": checked}, ensure_ascii=False))


# --------------------------------------------------------------------------- #
#  Разбор текста решения
# --------------------------------------------------------------------------- #

def fetch_post_text(post_id: int) -> str:
    data, _ = fetch(f"{API}/posts/{post_id}?_fields=id,link,title,content,date")
    if isinstance(data, list):
        data = data[0]
    return strip_html(data["content"]["rendered"])


def extract_case(post_id: int) -> dict:
    text = fetch_post_text(post_id)
    case = CASE_RE.search(text)
    date = DATE_RE.search(text[:3000])
    return {
        "post_id": post_id,
        "case_number": re.sub(r"\s+", "", case.group(1)) if case else "",
        "court_name": find_court(text).title(),
        "decision_date": (
            f"{int(date.group(1)):02d}.{MONTHS[date.group(2).lower()]:02d}.{date.group(3)}"
            if date else ""
        ),
        "text_length": len(text),
        "head": text[:600],
    }


def cmd_inspect(args) -> None:
    print(json.dumps(extract_case(args.post_id), ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- #
#  Отметки
# --------------------------------------------------------------------------- #

def cmd_mark(args) -> None:
    data = load(PROCESSED_FILE)
    data["processed"][str(args.post_id)] = {
        "status": args.status,
        "project": str(Path(args.project).expanduser().resolve()) if args.project else "",
        "video_id": args.video_id or "",
        "note": args.note or "",
        "marked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save(PROCESSED_FILE, data)
    print(f"[Queue] Пост {args.post_id} помечен как «{args.status}»")


def cmd_unmark(args) -> None:
    data = load(PROCESSED_FILE)
    if data["processed"].pop(str(args.post_id), None):
        save(PROCESSED_FILE, data)
        print(f"[Queue] Отметка с поста {args.post_id} снята")
    else:
        print(f"[Queue] Пост {args.post_id} и не был помечен")


def cmd_scaffold(args) -> None:
    """Создаёт папку ролика по делу из очереди и заполняет manifest."""
    import shutil

    cache = load(CACHE_FILE)
    if args.post_id:
        post = cache["posts"].get(str(args.post_id))
        if not post:
            raise SystemExit(f"пост {args.post_id} не в кэше — запусти sync")
    else:
        items = [i for i in unprocessed() if i["score"] >= args.min_score]
        if not items:
            raise SystemExit("очередь пуста — запусти sync")
        post = items[0]

    info = extract_case(post["id"])
    project = Path(args.project).expanduser().resolve()
    (project / "source").mkdir(parents=True, exist_ok=True)
    (project / "cards").mkdir(parents=True, exist_ok=True)

    skill = Path(__file__).resolve().parent.parent
    manifest_path = project / "manifest.json"
    if not manifest_path.exists():
        shutil.copy(skill / "templates" / "manifest.example.json", manifest_path)

    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["project_id"] = project.name
    data["source_url"] = post["link"]
    data["post_id"] = post["id"]
    data["case_number"] = info["case_number"] or data.get("case_number", "")
    data["court_name"] = info["court_name"] or data.get("court_name", "")
    data["decision_date"] = info["decision_date"] or data.get("decision_date", "")
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    bgm = project / "source" / "bgm.mp3"
    if not bgm.exists() and (skill / "assets" / "bgm_legal.mp3").exists():
        shutil.copy(skill / "assets" / "bgm_legal.mp3", bgm)

    cmd_mark(argparse.Namespace(post_id=post["id"], status="in_progress",
                                project=str(project), video_id="", note="scaffold"))

    print(f"[Queue] Проект: {project}")
    print(f"[Queue] Дело: {data['case_number']} | {data['court_name']} | {data['decision_date']}")
    print(f"[Queue] Источник: {post['link']}")
    print("\n--- начало решения (для сценария) ---")
    print(info["head"])
    print("\nДальше: заполнить 4 сцены в manifest.json и запустить make_short.sh")


def cmd_stats(args) -> None:
    cache = load(CACHE_FILE)
    done = load(PROCESSED_FILE)["processed"]
    total_cache = len(cache["posts"])
    on_site = cache.get("total_on_site", "?")
    by_status = {}
    for v in done.values():
        by_status[v["status"]] = by_status.get(v["status"], 0) + 1
    left = unprocessed()
    good = [i for i in left if i["score"] >= 3]
    print(f"На сайте:        {on_site}")
    print(f"В кэше:          {total_cache}")
    print(f"Обработано:      {len(done)}  " + " ".join(f"{k}={v}" for k, v in by_status.items()))
    print(f"Осталось:        {len(left)}  (перспективных: {len(good)})")
    print(f"Синхронизация:   {cache.get('synced_at', 'ещё не было')}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Очередь дел domdara.org")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sync", help="подтянуть каталог с сайта")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("list", help="необработанные посты")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--min-score", type=int, default=None)
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("next", help="следующее дело (JSON)")
    s.add_argument("--min-score", type=int, default=3)
    s.add_argument("--max-checks", type=int, default=8)
    s.set_defaults(func=cmd_next)

    s = sub.add_parser("inspect", help="вытащить номер дела/суд/дату")
    s.add_argument("post_id", type=int)
    s.set_defaults(func=cmd_inspect)

    s = sub.add_parser("mark", help="отметить пост обработанным")
    s.add_argument("post_id", type=int)
    s.add_argument("--status", default="private",
                   choices=["in_progress", "unlisted", "private", "published", "skipped", "failed"])
    s.add_argument("--project", default="")
    s.add_argument("--video-id", default="")
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_mark)

    s = sub.add_parser("unmark", help="снять отметку")
    s.add_argument("post_id", type=int)
    s.set_defaults(func=cmd_unmark)

    s = sub.add_parser("scaffold", help="создать папку ролика по делу")
    s.add_argument("--project", required=True)
    s.add_argument("--post-id", type=int, default=None)
    s.add_argument("--min-score", type=int, default=3)
    s.set_defaults(func=cmd_scaffold)

    s = sub.add_parser("stats", help="сводка")
    s.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
