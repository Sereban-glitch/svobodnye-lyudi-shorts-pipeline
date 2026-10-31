#!/usr/bin/env python3
"""
Generates high-resolution transparent PNG cards for YouTube Shorts (1080x1920).
Card dimensions: 920px width, rendered cleanly with yellow <mark> highlights.
"""

import json
import re
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

def _resolve_project() -> Path:
    import sys
    for i, a in enumerate(sys.argv):
        if a == "--project" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).expanduser().resolve()
        if a.startswith("--project="):
            return Path(a.split("=", 1)[1]).expanduser().resolve()
    return Path(__file__).resolve().parent


PROJECT_DIR = _resolve_project()
MANIFEST_FILE = PROJECT_DIR / "manifest.json"
CARDS_DIR = PROJECT_DIR / "cards"
CARDS_DIR.mkdir(parents=True, exist_ok=True)

def fetch_page_paragraphs(url: str):
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
    resp = requests.get(url, headers=headers, timeout=20)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    content = soup.find("div", class_="entry-content")
    if not content:
        raise RuntimeError("Could not find .entry-content on page")
    
    paras = []
    for tag in content.find_all(["p", "blockquote", "h1", "h2", "h3", "h4"]):
        t = tag.get_text().strip()
        # Clean non-breaking spaces
        t = t.replace("\xa0", " ")
        if t:
            paras.append(t)
    return paras

def find_paragraph_for_scene(paras, anchor: str):
    anchor_clean = anchor.lower().replace("\xa0", " ")
    for p in paras:
        if anchor_clean in p.lower():
            return p
    # Fallback: token matching
    tokens = [tok for tok in anchor_clean.split() if len(tok) > 2]
    for p in paras:
        p_low = p.lower()
        if all(tok in p_low for tok in tokens):
            return p
    return None

def excerpt(text: str, limit: int) -> str:
    """Обрезка по границе слова с многоточием (без «издер...» посреди слова)."""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    if cut < limit * 0.6:
        cut = limit
    return text[:cut].rstrip(" ,;:—-") + "…"


def build_card_html(header_text: str, full_paragraph: str, highlight_text: str,
                    source_label: str = "domdara.org / ЄДРСР",
                    badge: str = "СУД") -> str:
    # Highlight substring inside the paragraph (case-insensitive replace)
    clean_p = full_paragraph.replace("\xa0", " ")
    clean_h = highlight_text.replace("\xa0", " ").strip()

    # If highlight exists, wrap with <mark>
    if clean_h and clean_h.lower() in clean_p.lower():
        pattern = re.compile(re.escape(clean_h), re.IGNORECASE)
        body_html = pattern.sub(lambda m: f"<mark>{m.group(0)}</mark>", clean_p)
    else:
        # If partial match or slightly different wording, try first 30 chars
        tokens = [t for t in clean_h.split() if len(t) > 3]
        if tokens:
            pattern = re.compile(re.escape(tokens[0]) + r".*?" + re.escape(tokens[-1]), re.IGNORECASE | re.DOTALL)
            match = pattern.search(clean_p)
            if match:
                body_html = clean_p[:match.start()] + f"<mark>{match.group(0)}</mark>" + clean_p[match.end():]
            else:
                body_html = f"<mark>{clean_h}</mark><br><span style='color:#555;'>{excerpt(clean_p, 180)}</span>"
        else:
            body_html = clean_p

    # Limit total paragraph length to keep card compact and mobile-readable
    if len(body_html) > 450 and "</mark>" in body_html:
        # Keep window around <mark>
        idx = body_html.find("<mark>")
        start = max(0, idx - 80)
        end = min(len(body_html), idx + 280)
        # не режем слово посередине
        if end < len(body_html):
            cut = body_html.rfind(" ", start, end)
            if cut > start + 200:
                end = cut
        # не оставляем незакрытый <mark>
        if body_html[start:end].count("<mark>") > body_html[start:end].count("</mark>"):
            close = body_html.find("</mark>", end)
            if close != -1:
                end = close + len("</mark>")
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(body_html) else ""
        body_html = prefix + body_html[start:end].strip() + suffix

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: transparent;
    padding: 20px;
    font-family: 'Liberation Sans', 'DejaVu Sans', sans-serif;
    display: inline-block;
  }}
  .card {{
    width: 920px;
    background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%);
    border: 2px solid rgba(226, 232, 240, 0.9);
    border-radius: 24px;
    box-shadow: 0 25px 60px rgba(0, 0, 0, 0.65), 0 4px 12px rgba(0, 0, 0, 0.3);
    overflow: hidden;
  }}
  .card-header {{
    background: linear-gradient(90deg, #1E293B 0%, #0F172A 100%);
    color: #F8FAFC;
    padding: 16px 24px;
    display: flex;
    align-items: center;
    gap: 12px;
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 0.5px;
    border-bottom: 2px solid #3B82F6;
  }}
  .court-badge {{
    background: #2563EB;
    color: #FFFFFF;
    font-size: 13px;
    font-weight: 800;
    padding: 4px 10px;
    border-radius: 6px;
    text-transform: uppercase;
  }}
  .card-body {{
    padding: 24px 28px;
    color: #1E293B;
    font-size: 25px;
    line-height: 1.48;
    font-weight: 500;
  }}
  mark {{
    background: rgba(254, 240, 138, 0.95);
    color: #0F172A;
    font-weight: 800;
    padding: 2px 6px;
    border-radius: 6px;
    border-left: 4px solid #CA8A04;
    box-shadow: 0 2px 4px rgba(202, 138, 4, 0.2);
  }}
  .card-footer {{
    padding: 10px 28px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 15px;
    color: #64748B;
    font-weight: 600;
    border-top: 1px solid #E2E8F0;
    background: #F1F5F9;
  }}
</style>
</head>
<body>
  <div class="card" id="target-card">
    <div class="card-header">
      <span class="court-badge">{badge}</span>
      <span>{header_text}</span>
    </div>
    <div class="card-body">
      {body_html}
    </div>
    <div class="card-footer">
      <span>Источник: {source_label}</span>
      <span>Канал «Свободные Люди Запорожья»</span>
    </div>
  </div>
</body>
</html>"""

def main():
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    scenes = manifest["scenes"]
    # Карточки можно брать не со страницы, а прямо из манифеста: сцены с
    # полем card_text не требуют domdara.org (научные и обзорные ролики).
    # Сцены с card_skip карточки не получают вовсе — их в расчёт не берём.
    card_scenes = [s for s in scenes if not s.get("card_skip")]
    local_only = all(s.get("card_text") for s in card_scenes)
    paras = []
    if local_only:
        print("[Cards] Локальные карточки: текст берём из manifest.json")
    else:
        url = manifest["source_url"]
        print(f"[Cards] Fetching text from: {url}")
        paras = fetch_page_paragraphs(url)
        print(f"[Cards] Found {len(paras)} paragraphs on page.")

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
        page = browser.new_page(viewport={"width": 1080, "height": 1000}, device_scale_factor=2)

        for scene in scenes:
            sid = scene["scene_id"]

            if scene.get("card_skip"):
                print(f"[Cards] Scene {sid}: card_skip — карточка не нужна")
                continue

            highlight = scene["highlight_substring"]
            header = scene["overlay_header"]
            if scene.get("card_text"):
                print(f"[Cards] Processing Scene {sid} (локальный текст)...")
                html = build_card_html(
                    header, scene["card_text"], highlight,
                    scene.get("card_source", "manifest.json"),
                    scene.get("card_badge") or manifest.get("card_badge") or "СУД",
                )
            else:
                anchor = scene["dom_search_anchor"]
                print(f"[Cards] Processing Scene {sid} (anchor: '{anchor}')...")
                matched_p = find_paragraph_for_scene(paras, anchor)
                if not matched_p:
                    print(f"[Cards] Warning: Anchor not matched! Using highlight text as body.")
                    matched_p = highlight
                html = build_card_html(
                    header, matched_p, highlight,
                    badge=scene.get("card_badge") or manifest.get("card_badge") or "СУД",
                )

            page.set_content(html)
            page.wait_for_timeout(300)

            card_el = page.locator("#target-card")
            out_png = CARDS_DIR / f"card_{sid}.png"
            card_el.screenshot(path=str(out_png), omit_background=True)
            print(f"[Cards] Saved: {out_png} ({out_png.stat().st_size} bytes)")

        browser.close()
    print("[Cards] All cards generated successfully!")

if __name__ == "__main__":
    main()
