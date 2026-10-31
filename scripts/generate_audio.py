#!/usr/bin/env python3
"""
Audio-First Voiceover & Timing Generator.
Priority:
  1. User recording (source/vo_{sid}.wav if manually provided).
  2. Edge-TTS (ru-RU-DmitryNeural) with retry logic.
  3. Google TTS (gTTS) as reliable automatic fallback.
Measures exact scene durations via ffprobe and outputs audio_timing.json.
"""

import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

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
SOURCE_DIR = PROJECT_DIR / "source"
SOURCE_DIR.mkdir(parents=True, exist_ok=True)
PYTHON_VENV = Path(sys.executable)

# --------------------------------------------------------------------------- #
#  Произношение: текст для озвучки ≠ текст на экране
# --------------------------------------------------------------------------- #

SKILL_PRONUNCIATION = (Path.home() / ".config/opencode/skills/flow-shorts"
                       / "references" / "pronunciation.json")
if not SKILL_PRONUNCIATION.exists():
    SKILL_PRONUNCIATION = (Path(__file__).resolve().parent.parent / "references" / "pronunciation.json")
UK_LETTERS = re.compile(r"[іїєґІЇЄҐ]")


def load_pronunciation(project_dir: Path) -> dict:
    """Сливает общую карту навыка и локальную карту проекта.

    Нужно потому, что синтезатор читает украинские слова по-русски
    («Оберіг» → «Аберг») и путает ударения в сложных словах («админсуд»,
    «высветился»), а на экране написание должно остаться официальным.
    """
    merged = {}
    for path in (SKILL_PRONUNCIATION, project_dir / "pronunciation.json"):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[Audio] Карта произношения {path} не прочитана: {e}")
            continue
        merged.update(data.get("replacements", {}))
    return merged


def apply_pronunciation(text: str, mapping: dict) -> str:
    """Заменяет слова по карте, начиная с самых длинных ключей."""
    for key in sorted(mapping, key=len, reverse=True):
        pattern = rf"(?<![\w-]){re.escape(key)}(?![\w-])"
        text = re.sub(pattern, mapping[key], text)
    return text


def get_audio_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())

async def synthesize_edge(text: str, out_mp3: Path, attempts: int = 15) -> bool:
    """edge-tts периодически отдаёт NoAudioReceived (rate limit).

    Поэтому: повторы с нарастающей паузой. Голос строго ru-RU-DmitryNeural.
    Никаких fallback на роботизированные женские голоса!
    """
    import edge_tts
    for attempt in range(1, attempts + 1):
        try:
            comm = edge_tts.Communicate(text, "ru-RU-DmitryNeural")
            await comm.save(str(out_mp3))
            if out_mp3.exists() and out_mp3.stat().st_size > 1000:
                return True
        except Exception as e:
            print(f"    edge-tts попытка {attempt}/{attempts}: {type(e).__name__}")
        await asyncio.sleep(2.0 + 1.5 * attempt)
    return False

def synthesize_scene_audio(text: str, wav_out: Path, sid: int, force: bool = False):
    # Check if manual wav already provided
    if not force and wav_out.exists() and wav_out.stat().st_size > 10000:
        print(f"[Audio] Сцена {sid}: используется существующий файл {wav_out.name}")
        return

    mp3_temp = SOURCE_DIR / f"temp_vo_{sid}.mp3"
    mp3_temp.unlink(missing_ok=True)

    print(f"[Audio] Сцена {sid}: синтез речи (ru-RU-DmitryNeural)...")
    success = False
    try:
        success = asyncio.run(synthesize_edge(text, mp3_temp))
    except Exception as e:
        print(f"[Audio] Сцена {sid}: ошибка: {e}")
        success = False

    if not success or not mp3_temp.exists() or mp3_temp.stat().st_size < 1000:
        raise RuntimeError(f"[Audio] Сцена {sid}: Не удалось синтезировать ru-RU-DmitryNeural. Падение для исключения подмены голоса!")

    # Convert to 48kHz stereo WAV
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-i", str(mp3_temp),
        "-ar", "48000", "-ac", "2",
        str(wav_out)
    ], check=True)
    mp3_temp.unlink(missing_ok=True)

def main():
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    timings = {}
    current_offset = 0.0
    wav_files = []

    force = "--force" in sys.argv
    pron = load_pronunciation(PROJECT_DIR)
    if pron:
        print(f"[Audio] Карта произношения: {len(pron)} замен(ы)")

    print("[Audio] Запуск конвейера Audio-First для Кейса №1...")

    for scene in manifest["scenes"]:
        sid = scene["scene_id"]
        display_text = scene["voiceover_ru"]
        text = apply_pronunciation(display_text, pron)
        wav_out = SOURCE_DIR / f"vo_{sid}.wav"

        if text != display_text:
            # Показываем ровно те слова, которые разойдутся с субтитром:
            # читаешь одно, слышишь другое — так и появляется «стрёмно».
            shown, spoken = display_text.split(), text.split()
            diffs = [(a, b) for a, b in zip(shown, spoken) if a != b]
            print(f"[Audio] Сцена {sid}: озвучка отличается от текста на экране")
            for a, b in diffs[:6]:
                print(f"        на экране «{a}» → в озвучке «{b}»")
        leftover = UK_LETTERS.findall(text)
        if leftover:
            print(f"[Audio] Сцена {sid}: ⚠ украинские буквы в озвучке: "
                  f"{''.join(sorted(set(leftover)))} — добавь слово в карту произношения")

        synthesize_scene_audio(text, wav_out, sid, force=force)

        dur = get_audio_duration(wav_out)
        start_t = current_offset
        end_t = current_offset + dur
        current_offset = end_t

        timings[f"scene_{sid}"] = {
            "scene_id": sid,
            "duration": round(dur, 3),
            "start_time": round(start_t, 3),
            "end_time": round(end_t, 3),
            "wav_path": str(wav_out),
            "text": display_text,
            "tts_text": text
        }
        wav_files.append(wav_out)
        print(f"[Audio] Сцена {sid}: {dur:.2f} сек (таймкод: {start_t:.2f}s -> {end_t:.2f}s)")
        time.sleep(0.5)

    # Concat all scenes into vo_full.wav
    concat_list = SOURCE_DIR / "concat_audio.txt"
    with open(concat_list, "w", encoding="utf-8") as f:
        for w in wav_files:
            f.write(f"file '{w.name}'\n")

    vo_full = SOURCE_DIR / "vo_full.wav"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_list),
        "-c", "copy",
        str(vo_full)
    ], check=True)
    concat_list.unlink(missing_ok=True)

    total_duration = get_audio_duration(vo_full)
    timings_data = {
        "total_duration": round(total_duration, 3),
        "scenes": timings
    }

    timing_file = SOURCE_DIR / "audio_timing.json"
    with open(timing_file, "w", encoding="utf-8") as f:
        json.dump(timings_data, f, indent=2, ensure_ascii=False)

    print(f"\n[Audio] ✅ Итоговая озвучка: {vo_full.name} ({total_duration:.2f} сек)")
    print(f"[Audio] Полная карта таймингов записана в {timing_file.name}")

if __name__ == "__main__":
    main()
