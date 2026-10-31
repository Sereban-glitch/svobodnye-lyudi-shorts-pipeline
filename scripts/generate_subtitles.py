#!/usr/bin/env python3
"""
Dynamic Subtitle Generator for YouTube Shorts (1080x1920).

Ключевая идея: текст озвучки известен заранее (manifest.json), поэтому
распознавать его заново не нужно — достаточно узнать ТАЙМИНГИ.

Faster-Whisper даёт слова с таймкодами (часто с ошибками: «АБЕРГ» вместо
«Оберіг», «админсут» вместо «админсуд»). Мы берём из него только время,
а текст подставляем из сценария, выравнивая последовательности слов
(SequenceMatcher). Итог: 100% правильный текст и живые таймкоды.
"""

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

from faster_whisper import WhisperModel

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
AUDIO_FILE = PROJECT_DIR / "source" / "vo_full.wav"
ASS_FILE = PROJECT_DIR / "subtitles.ass"

MAX_CUE_CHARS = 22          # длина одной реплики на экране
MAX_CUE_WORDS = 4
MIN_CUE_DUR = 0.35          # сек, чтобы реплика не мигала

# Служебные слова не должны висеть в конце реплики («ОТМЕТКУ ОБ» / «ИСКЛЮЧЕНИИ»)
FUNCTION_WORDS = {
    "в", "во", "и", "с", "со", "об", "о", "обо", "на", "по", "к", "ко", "у",
    "за", "из", "от", "до", "не", "ни", "а", "но", "или", "что", "как", "при",
    "для", "же", "ли", "это", "его", "ее", "их",
}

# Бренды и сокращения: как показывать на экране
DISPLAY_FIXES = [
    (r"РЕЗЕРВ\s+ПЛЮС", "РЕЗЕРВ+"),
    (r"РЕЗЕРВ\s*-\s*ПЛЮС", "РЕЗЕРВ+"),
    (r"ЧАТ\s+ДЖИ[\s\-]*ПИ[\s\-]*ТИ+", "ChatGPT"),
    (r"ЧАТ\s+ДЖИПИТИ", "ChatGPT"),
]


def format_ass_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int(round((seconds - int(seconds)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h:01d}:{m:02d}:{s:02d}.{cs:02d}"


def build_ass_header() -> str:
    return """[Script Info]
Title: Case Shorts Subtitles
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,DejaVu Sans,52,&H00FFFFFF,&H0000FFFF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,1,5,2,2,40,40,520,1
Style: Highlight,DejaVu Sans,54,&H0000FFFF,&H00FFFFFF,&H00000000,&H99000000,-1,0,0,0,100,100,0,0,1,6,3,2,40,40,520,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def norm_words(token: str) -> list:
    """Слово для сравнения: нижний регистр, без пунктуации, ё -> е."""
    t = token.lower().replace("ё", "е")
    t = re.sub(r"[^\w]+", " ", t, flags=re.UNICODE)
    return [w for w in t.split() if w]


def script_tokens() -> list:
    """Токены сценария по порядку сцен: [(display_token, scene_id), ...]."""
    manifest = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    tokens = []
    for scene in sorted(manifest["scenes"], key=lambda s: s["scene_id"]):
        for tok in scene["voiceover_ru"].split():
            tokens.append(tok)
    return tokens


def asr_words(model, audio: Path) -> list:
    """Слова Whisper с таймкодами: [(norm_word, start, end), ...]."""
    segments, _ = model.transcribe(str(audio), language="ru", word_timestamps=True)
    out = []
    for seg in segments:
        for w in seg.words or []:
            for nw in norm_words(w.word):
                out.append((nw, w.start, w.end))
    return out


def align_times(script: list, asr: list) -> list:
    """Возвращает список времён (start, end) для каждого слова сценария."""
    s_norm = [nw for tok in script for nw in norm_words(tok)]
    s_flat = []          # индекс слова сценария -> индекс токена
    for i, tok in enumerate(script):
        s_flat.extend([i] * len(norm_words(tok)))

    a_norm = [w[0] for w in asr]
    times = [None] * len(s_norm)

    sm = SequenceMatcher(None, s_norm, a_norm, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                times[i1 + k] = (asr[j1 + k][1], asr[j1 + k][2])

    # Несопоставленные слова — интерполируем между соседями
    known = [i for i, t in enumerate(times) if t]
    if not known:
        return []
    for i in range(len(times)):
        if times[i]:
            continue
        prev = max((k for k in known if k < i), default=None)
        nxt = min((k for k in known if k > i), default=None)
        if prev is not None and nxt is not None:
            span = (times[nxt][0] - times[prev][1]) / (nxt - prev)
            start = times[prev][1] + span * (i - prev - 1)
            times[i] = (start, start + span)
        elif prev is not None:
            times[i] = (times[prev][1], times[prev][1] + 0.2)
        else:
            times[i] = (times[nxt][0] - 0.2, times[nxt][0])

    # Сводим слова к токенам (у токена может быть несколько слов)
    tok_times = []
    for idx, tok in enumerate(script):
        ws = [times[i] for i, t_i in enumerate(s_flat) if t_i == idx]
        ws = [w for w in ws if w]
        if ws:
            tok_times.append((min(w[0] for w in ws), max(w[1] for w in ws)))
        else:
            tok_times.append(None)
    return tok_times


def clean_display(text: str) -> str:
    text = text.upper()
    text = text.replace(",", "").replace(".", "").replace("!", "").replace("?", "")
    text = text.replace(" -", "-").replace("- ", "-")
    for pattern, repl in DISPLAY_FIXES:
        text = re.sub(pattern, repl, text)
    return text.strip()


def build_cues(script: list, times: list) -> list:
    """Группирует токены в короткие реплики 2–4 слова / до 22 символов."""
    groups = []          # [[(token, start, end), ...], ...]
    buf = []

    for tok, t in zip(script, times):
        if not t:
            continue
        start, end = t
        candidate = " ".join([b[0] for b in buf] + [tok])

        if buf and (len(candidate) > MAX_CUE_CHARS or len(buf) >= MAX_CUE_WORDS):
            carry = []
            # предлог/союз в конце реплики переносим в следующую
            if len(buf) > 1 and buf[-1][0].lower().strip(".,!?:;") in FUNCTION_WORDS:
                carry = [buf.pop()]
            groups.append(buf)
            buf = carry

        buf.append((tok, start, end))
        if re.search(r"[.!?:;—]$", tok):
            groups.append(buf)
            buf = []

    if buf:
        groups.append(buf)

    # «Резерв» + «плюс» не разрываем: на экране это РЕЗЕРВ+
    merged = []
    for g in groups:
        if (merged and g and merged[-1]
                and merged[-1][-1][0].lower().strip(".,!?:;") == "резерв"
                and g[0][0].lower().strip(".,!?:;") == "плюс"):
            merged[-1].append(g.pop(0))
            if not g:
                continue
        merged.append(g)

    cues = []
    for g in merged:
        text = clean_display(" ".join(t[0] for t in g))
        if text:
            cues.append((g[0][1], g[-1][2], text))

    # Не допускаем наложения и слишком коротких реплик
    fixed = []
    prev_end = 0.0
    for start, end, text in cues:
        start = max(start, prev_end + 0.01)
        end = max(end, start + MIN_CUE_DUR)
        fixed.append((start, end, text))
        prev_end = end
    return fixed


def generate_subtitles():
    if not AUDIO_FILE.exists():
        print(f"Error: {AUDIO_FILE} does not exist. Run generate_audio.py first.")
        sys.exit(1)

    print(f"[Subtitles] Транскрибация {AUDIO_FILE.name} (только таймкоды)...")
    model = WhisperModel("base", device="cpu", compute_type="int8")
    asr = asr_words(model, AUDIO_FILE)

    script = script_tokens() if MANIFEST_FILE.exists() else []
    if script:
        times = align_times(script, asr)
        cues = build_cues(script, times)
        print(f"[Subtitles] Выравнивание по сценарию: {len(script)} слов -> {len(cues)} реплик")
    else:
        # Фолбэк: текст из Whisper (может содержать ошибки распознавания)
        print("[Subtitles] manifest.json не найден — текст берём из Whisper")
        cues = []
        for i in range(0, len(asr), 3):
            group = asr[i:i + 3]
            cues.append((group[0][1], group[-1][2],
                         clean_display(" ".join(w[0] for w in group))))

    events = []
    for start, end, text in cues:
        if not text:
            continue
        events.append(
            f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},"
            f"Highlight,,0,0,0,,{{\\fad(60,60)}}{text}\n"
        )

    with open(ASS_FILE, "w", encoding="utf-8") as f:
        f.write(build_ass_header() + "".join(events))

    print(f"[Subtitles] ✅ Создан файл субтитров: {ASS_FILE.name} ({len(events)} реплик)")


if __name__ == "__main__":
    generate_subtitles()
