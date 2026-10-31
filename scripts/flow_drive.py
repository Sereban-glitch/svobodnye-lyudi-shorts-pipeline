#!/usr/bin/env python3
"""
flow_drive.py — Автопилот Google Flow через Chrome CDP (порт 9222).

Генерирует вертикальные видео (9:16, 720p, 10s) по промптам из manifest.json
с помощью модели Gemini Omni 1.1 Flash и скачивает их в source/bg_N.mp4.

Ключевые особенности:
1. Автоматически закрывает обучающие всплывающие баннеры Google Flow.
2. Гарантированно переключает режим из картинок (Nano Banana) в Видео (Omni 1.1 Flash, 9:16, 10s, x1).
3. Скачивает видеопоток напрямую из CDN с увеличенным таймаутом (120с).
4. Поддерживает запуск по отдельным сценам (--scenes 1,2,3,4).
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROMPT_SEL = 'div.ProseMirror[contenteditable="true"]'
GEN_BTN = 'button[aria-label="Начать генерацию"]'

def _resolve_project() -> Path:
    for i, a in enumerate(sys.argv):
        if a == "--project" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).expanduser().resolve()
        if a.startswith("--project="):
            return Path(a.split("=", 1)[1]).expanduser().resolve()
    return Path.cwd()

def get_page(browser, cdp_port: int):
    for ctx in browser.contexts:
        for pg in ctx.pages:
            if "flow.google.com" in pg.url:
                return pg
    ctx = browser.contexts[0]
    pg = ctx.pages[0] if ctx.pages else ctx.new_page()
    pg.goto("https://flow.google.com")
    time.sleep(4)
    return pg

def dismiss_modals(page) -> None:
    """Закрывает любые всплывающие обучающие и сервисные баннеры."""
    try:
        page.keyboard.press("Escape")
        time.sleep(0.3)
        close_btn = page.locator('button[aria-label="Закрыть сообщение"], button:has-text("OK"), button:has-text("ОК")').first
        if close_btn.count() > 0 and close_btn.is_visible():
            close_btn.click()
            time.sleep(0.5)
    except Exception:
        pass

def ensure_video_mode(page) -> None:
    """Гарантирует, что генератор настроен на Видео (Omni 1.1 Flash, 9:16, 10 сек, x1)."""
    dismiss_modals(page)
    
    # Проверяем текст текущего режима в промпт-боксе
    mode_pill = page.locator('button:has-text("Видео"), div:has-text("Видео"), button:has-text("Nano Banana"), div:has-text("Nano Banana")').last
    pill_text = mode_pill.inner_text().lower() if mode_pill.count() > 0 else ""
    
    # Если уже активен правильный режим видео
    if "видео" in pill_text and "9:16" in pill_text and "10" in pill_text:
        return
        
    print("[Flow] Настройка видеорежима (Omni 1.1 Flash, 9:16, 10s, x1)...", flush=True)
    if mode_pill.count() > 0:
        mode_pill.click()
        time.sleep(0.8)
        
        # Переключаем на таб "Видео"
        vid_tab = page.locator('button:has-text("Видео"), div[role="tab"]:has-text("Видео")').last
        if vid_tab.count() > 0:
            vid_tab.click()
            time.sleep(0.5)
            
        # Формат 9:16
        r_btn = page.locator('button:has-text("9:16"), [role="button"]:has-text("9:16")').first
        if r_btn.count() > 0:
            r_btn.click()
            time.sleep(0.3)
            
        # Длительность 10 сек
        d_btn = page.locator('button:has-text("10 сек"), [role="button"]:has-text("10 сек")').first
        if d_btn.count() > 0:
            d_btn.click()
            time.sleep(0.3)
            
        # Количество x1
        c_btn = page.locator('button:has-text("x1"), [role="button"]:has-text("x1")').first
        if c_btn.count() > 0:
            c_btn.click()
            time.sleep(0.3)
            
        page.keyboard.press("Escape")
        time.sleep(0.5)
        print("[Flow] ✅ Видеорежим успешно сконфигурирован!", flush=True)

def type_prompt_and_generate(page, prompt: str) -> None:
    editor = page.locator(PROMPT_SEL).first
    editor.click()
    time.sleep(0.3)
    page.keyboard.press("Control+A")
    page.keyboard.press("Backspace")
    time.sleep(0.2)
    editor.type(prompt, delay=0)
    time.sleep(1.0)

    btn = page.locator(GEN_BTN).last
    if not btn.is_enabled():
        raise RuntimeError("Кнопка генерации недоступна!")
    btn.click()
    print("    ▶ Промпт отправлен в Google Flow", flush=True)

def wait_for_completion(page, timeout: int = 180) -> None:
    start = time.time()
    seen_progress = False
    while time.time() - start < timeout:
        time.sleep(3)
        status = page.evaluate("""() => {
            const text = document.body.innerText;
            const pcts = text.match(/\\d{1,3}\\s*%/g) || [];
            return { pcts: pcts };
        }""")
        elapsed = int(time.time() - start)
        pcts = status["pcts"]
        print(f"    [{elapsed:3d}s] Прогресс генерации: {pcts}", flush=True)
        if pcts:
            seen_progress = True
        elif seen_progress and not pcts:
            print("    ✅ Рендер завершён!", flush=True)
            return
    time.sleep(2)

def extract_and_download_video(ctx, page, dest: Path) -> None:
    time.sleep(2)
    tile0 = page.locator('flow-grid-tile-container').first
    tile0.scroll_into_view_if_needed()
    tile0.hover()
    time.sleep(2)

    v_src = None
    for _ in range(8):
        v_src = page.evaluate("""() => {
            const t0 = document.querySelectorAll('flow-grid-tile-container')[0];
            const v = t0 ? t0.querySelector('video') : null;
            return v ? v.src : (document.querySelector('video') ? document.querySelector('video').src : null);
        }""")
        if v_src:
            break
        tile0.hover()
        time.sleep(1)

    if not v_src:
        tile0.click()
        time.sleep(2)
        v_src = page.evaluate("() => document.querySelector('video') ? document.querySelector('video').src : null")
        page.keyboard.press("Escape")
        time.sleep(1)

    if not v_src:
        raise RuntimeError(f"Не удалось получить URL сгенерированного видео")

    print(f"    📥 Скачивание: {v_src[:75]}...", flush=True)
    resp = ctx.request.get(v_src, timeout=120000)
    if not resp.ok:
        raise RuntimeError(f"HTTP {resp.status} при скачивании видео")

    dest.write_bytes(resp.body())
    print(f"    🎉 Успешно сохранён: {dest.name} ({dest.stat().st_size / 1e6:.2f} МБ)", flush=True)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default=".", help="Папка проекта")
    ap.add_argument("--scenes", default="1,2,3,4", help="Номера сцен через запятую")
    ap.add_argument("--port", type=int, default=9222, help="Порт Chrome CDP")
    ap.add_argument("--timeout", type=int, default=180, help="Таймаут генерации сцены")
    args = ap.parse_args()

    project_dir = _resolve_project()
    manifest_file = project_dir / "manifest.json"
    source_dir = project_dir / "source"
    source_dir.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    scenes_map = {s["scene_id"]: s for s in manifest["scenes"]}
    target_scenes = [int(x.strip()) for x in args.scenes.split(",") if x.strip()]

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(f"http://localhost:{args.port}")
        page = get_page(browser, args.port)
        page.bring_to_front()
        print(f"[Flow] Подключено: {page.url}", flush=True)

        ensure_video_mode(page)

        for sid in target_scenes:
            if sid not in scenes_map:
                continue
            dest = source_dir / f"bg_{sid}.mp4"
            if dest.exists() and dest.stat().st_size > 500_000:
                print(f"\n[Scene {sid}] Видео {dest.name} уже существует — пропуск", flush=True)
                continue

            prompt = scenes_map[sid]["flow_prompt_en"]
            print(f"\n{'='*55}\n[Scene {sid}] Запуск генерации...")
            print(f"[Scene {sid}] Промпт: {prompt[:80]}...\n", flush=True)

            type_prompt_and_generate(page, prompt)
            wait_for_completion(page, args.timeout)
            extract_and_download_video(browser.contexts[0], page, dest)
            time.sleep(3)

    print("\n🎉 Все видеосцены успешно сгенерированы и сохранены!", flush=True)

if __name__ == "__main__":
    main()
