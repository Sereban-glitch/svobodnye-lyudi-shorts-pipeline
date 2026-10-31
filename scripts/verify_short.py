#!/usr/bin/env python3
"""
verify_short.py — приёмочная проверка готового Short.

Проверяет то, что реально ломалось на практике:
  * разрешение, fps, кодеки, длительность;
  * общую громкость (-14 LUFS) и пик (<= -1 dBTP);
  * что музыка слышна в паузах, но не лезет под голос (разница 8–16 дБ);
  * что субтитры не заходят в нижнюю зону интерфейса YouTube.

    python3 verify_short.py --project ~/projects/case_160_12541_26
"""

import argparse
import json
import math
import statistics
import re
from collections import Counter
import struct
import subprocess
import sys
import wave
from pathlib import Path


def _resolve_project() -> Path:
    for i, a in enumerate(sys.argv):
        if a == "--project" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).expanduser().resolve()
        if a.startswith("--project="):
            return Path(a.split("=", 1)[1]).expanduser().resolve()
    return Path(__file__).resolve().parent


PROJECT_DIR = _resolve_project()
VIDEO = PROJECT_DIR / "final_short.mp4"
TIMING = PROJECT_DIR / "source" / "audio_timing.json"
ASS = PROJECT_DIR / "subtitles.ass"

problems = []
notes = []


def ok(cond: bool, msg: str) -> None:
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        problems.append(msg)


def ffprobe(path: Path) -> dict:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_name,width,height,r_frame_rate,sample_rate,channels",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(res.stdout)


def loudness(path: Path) -> dict:
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    blob = res.stderr[res.stderr.rfind("{"):res.stderr.rfind("}") + 1]
    return json.loads(blob)


def mono_samples(path: Path, rate: int = 16000) -> tuple:
    tmp = Path("/tmp/opencode/_verify_mono.wav")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(path), "-vn",
         "-ac", "1", "-ar", str(rate), str(tmp)],
        check=True,
    )
    w = wave.open(str(tmp))
    sr = w.getframerate()
    raw = w.readframes(w.getnframes())
    return sr, struct.unpack("<%dh" % (len(raw) // 2), raw)


def rms_db(samples, sr: int, a: float, b: float) -> float:
    chunk = samples[int(a * sr):int(b * sr)]
    if not chunk:
        return -99.0
    ms = sum(x * x for x in chunk) / len(chunk)
    return 20 * math.log10(math.sqrt(ms) / 32768) if ms > 0 else -99.0


def silence_windows(samples, sr: int, threshold=-45.0, min_len=0.4) -> list:
    """Окна реальной тишины в озвучке (0.1 с), длиной >= min_len."""
    win = int(0.1 * sr)
    gaps, start, last = [], None, None
    for i in range(0, len(samples) - win, win):
        chunk = samples[i:i + win]
        ms = sum(x * x for x in chunk) / win
        db = 20 * math.log10(math.sqrt(ms) / 32768) if ms > 0 else -99.0
        t = i / sr
        if db < threshold:
            if start is None:
                start = t
            last = t
        else:
            if start is not None and last - start >= min_len:
                gaps.append((start, last + 0.1))
            start = None
    if start is not None and last - start >= min_len:
        gaps.append((start, last + 0.1))
    return gaps


def check_media() -> None:
    print("\n[1/5] Контейнер и дорожки")
    if not VIDEO.exists():
        print(f"  ❌ нет файла {VIDEO}")
        problems.append("нет final_short.mp4")
        return
    info = ffprobe(VIDEO)
    v = next((s for s in info["streams"] if s.get("width")), {})
    a = next((s for s in info["streams"] if s.get("sample_rate")), {})
    dur = float(info["format"]["duration"])

    ok(v.get("width") == 1080 and v.get("height") == 1920,
       f"кадр {v.get('width')}x{v.get('height')} (нужно 1080x1920)")
    ok(v.get("r_frame_rate") == "30/1", f"fps {v.get('r_frame_rate')} (нужно 30/1)")
    ok(v.get("codec_name") == "h264", f"видеокодек {v.get('codec_name')}")
    ok(a.get("codec_name") == "aac", f"аудиокодек {a.get('codec_name')}")
    ok(a.get("sample_rate") == "48000", f"частота {a.get('sample_rate')} Гц")

    if TIMING.exists():
        total = json.loads(TIMING.read_text(encoding="utf-8"))["total_duration"]
        ok(abs(dur - total) < 0.25, f"длительность {dur:.2f} с (озвучка {total:.2f} с)")
    notes.append(f"размер {VIDEO.stat().st_size / 1e6:.1f} МБ, {dur:.2f} с")


def check_loudness() -> None:
    print("\n[2/5] Громкость")
    m = loudness(VIDEO)
    integrated = float(m["input_i"])
    peak = float(m["input_tp"])
    ok(-15.0 <= integrated <= -13.0, f"интегральная {integrated:.1f} LUFS (цель -14)")
    ok(peak <= -1.0, f"истинный пик {peak:.1f} dBTP (нужно <= -1.0)")


def check_ducking() -> None:
    print("\n[3/5] Музыка и голос")
    sr, mix = mono_samples(VIDEO)
    vo_file = PROJECT_DIR / "source" / "vo_full.wav"
    if not vo_file.exists():
        notes.append("vo_full.wav нет — пропускаю анализ пауз")
        return

    sr_vo, vo = mono_samples(vo_file)
    gaps = silence_windows(vo, sr_vo)
    if not gaps:
        notes.append("в озвучке нет пауз — анализ ducking пропущен")
        return

    # середины пауз, чтобы не попасть на хвост голоса
    gap_db = [rms_db(mix, sr, a + (b - a) * 0.35, a + (b - a) * 0.75) for a, b in gaps]
    speech_db = []
    # речь: окна сразу после паузы
    for a, b in gaps:
        s = b + 0.3
        if s + 0.6 < len(mix) / sr:
            speech_db.append(rms_db(mix, sr, s, s + 0.6))
    if not speech_db:
        return

    g = statistics.median(gap_db)
    v = statistics.median(speech_db)
    diff = v - g
    print(f"  музыка в паузах: {g:.1f} дБ, речь: {v:.1f} дБ, разница {diff:.1f} дБ")
    ok(-34.0 <= g <= -20.0, f"подложка слышна ({g:.1f} дБ, нужно -34…-20)")
    ok(8.0 <= diff <= 16.0, f"голос выше подложки на {diff:.1f} дБ (нужно 8–16)")


def check_bgm() -> None:
    """Подложка не должна быть метрономом или перкуссией.

    Реальный случай: в ролик попал файл-метроном — под голосом он читается
    как треск и «помехи», хотя по громкости всё в норме. На слух это
    замечаешь не сразу, поэтому проверяем машинно: у метронома много
    ритмических ударов с повторяющимся интервалом, у мягкой подложки
    удары случайны или их нет.
    """
    print("\n[4/5] Подложка (без ритмических ударов)")
    bgm = PROJECT_DIR / "source" / "bgm.mp3"
    if not bgm.exists():
        notes.append("source/bgm.mp3 нет — проверка подложки пропущена")
        return

    sr, s = mono_samples(bgm, rate=16000)
    hop = int(0.01 * sr)
    env = []
    for i in range(0, len(s) - hop, hop):
        # прокси спектрального потока: сумма |разностей| = доля высоких частот
        env.append(sum(abs(s[j + 1] - s[j]) for j in range(i, i + hop - 1)) / hop)
    if len(env) < 100:
        notes.append("подложка слишком короткая для анализа")
        return

    med = statistics.median(env) or 1
    peaks = [i for i in range(2, len(env) - 2)
             if env[i] > env[i - 1] and env[i] >= env[i + 1] and env[i] > med * 3]
    deltas = [peaks[k + 1] - peaks[k] for k in range(len(peaks) - 1)
              if 0 < peaks[k + 1] - peaks[k] < 500]
    repeats = max(Counter(deltas).values()) if deltas else 0

    print(f"  ударов: {len(peaks)}, самый частый интервал повторяется {repeats} раз")
    ok(repeats < 5, f"подложка без ритма (повторов {repeats}, нужно < 5)")


def check_safe_zone() -> None:
    print("\n[5/5] Субтитры и Safe Zone")
    if not ASS.exists():
        ok(False, "нет subtitles.ass")
        return
    text = ASS.read_text(encoding="utf-8")
    # Style: Name,Font,Size,Primary,Secondary,Outline,Back,Bold,...,MarginL,MarginR,MarginV,Encoding
    margin_v = None
    for line in text.splitlines():
        if line.startswith("Style: Highlight,"):
            fields = line.split(",")
            if len(fields) >= 23:
                margin_v = int(fields[21])   # MarginV
            break
    ok(margin_v is not None and margin_v >= 480,
       f"MarginV={margin_v} (нужно >= 480, чтобы не лезть под интерфейс YouTube)")
    cues = [l for l in text.splitlines() if l.startswith("Dialogue")]
    ok(len(cues) >= 10, f"реплик: {len(cues)}")
    long_cues = [c for c in cues if len(c.split(",", 9)[-1].split("}", 1)[-1]) > 26]
    ok(not long_cues, f"слишком длинных реплик: {len(long_cues)} (нужно 0)")


def main() -> None:
    print(f"Проверка: {VIDEO}")
    check_media()
    check_loudness()
    check_ducking()
    check_bgm()
    check_safe_zone()

    print("\n" + "=" * 50)
    if problems:
        print(f"❌ ПРОБЛЕМЫ ({len(problems)}):")
        for p in problems:
            print("   -", p)
        sys.exit(1)
    print("✅ ВСЁ В НОРМЕ")
    for n in notes:
        print("   ·", n)


if __name__ == "__main__":
    main()
