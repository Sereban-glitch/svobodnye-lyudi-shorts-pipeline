#!/usr/bin/env python3
"""
Full Short Assembly Script (FFmpeg Engine v2.0 - Optimized).
Combines:
  1. Audio-First video matching (bg_1..4.mp4 speed-fitted to scene audio durations).
  2. Floating court cards (card_1..4.png) centered in Safe Zone (Y: 420..1050).
  3. Dynamic ASS subtitles in Safe Zone (Y: 1200..1420).
  4. Audio sidechain ducking (bgm.mp3 ducks under voiceover) + loudnorm (-14 LUFS).
  5. Top case banner (Y: 180..260).
"""

import json
import subprocess
import sys
from pathlib import Path

def _resolve_project() -> Path:
    """Проект задаётся --project, иначе папка рядом со скриптом."""
    for i, a in enumerate(sys.argv):
        if a == "--project" and i + 1 < len(sys.argv):
            return Path(sys.argv[i + 1]).expanduser().resolve()
        if a.startswith("--project="):
            return Path(a.split("=", 1)[1]).expanduser().resolve()
    return Path(__file__).resolve().parent


PROJECT_DIR = _resolve_project()
MANIFEST_FILE = PROJECT_DIR / "manifest.json"
TIMING_FILE = PROJECT_DIR / "source" / "audio_timing.json"
SOURCE_DIR = PROJECT_DIR / "source"
CARDS_DIR = PROJECT_DIR / "cards"
ASS_FILE = PROJECT_DIR / "subtitles.ass"
OUTPUT_VIDEO = PROJECT_DIR / "final_short.mp4"
BGM_FILE = SOURCE_DIR / "bgm.mp3"

# Музыкальная подложка. Источник legal_loop.mp3 ≈ -23 LUFS, голос ≈ -20 LUFS.
# Подложку сначала выравниваем по громкости (loudnorm на дорожке), затем
# глушим под речью. BGM_LUFS=-29 даёт подложку примерно на 13 дБ тише голоса:
# слышно, но не мешает разборчивости.
BGM_LUFS = -31
BGM_FADE_IN = 1.2
BGM_FADE_OUT = 2.5
TARGET_LUFS = -14.0
TARGET_TP = -1.5
TARGET_LRA = 11.0

def prepare_bgm_loop(bgm: Path) -> Path:
    """Готовит петлю подложки без плавного появления/затухания по краям.

    В исходниках подложки обычно есть fade-in в начале и длинный fade-out
    в конце. Прямое зацикливание (`-stream_loop -1`) тогда даёт «дыру»:
    музыка затухает до нуля и снова появляется — на 40-й секунде ролика
    это слышно как пропажа подложки.

    Берём ровный участок без краевых фейдов и сшиваем стык микрофейдами
    по 60 мс: на уровне подложки −31 LUFS такой стык не слышен, а щелчка
    не будет, потому что обе стороны приходят в ноль.

    (acrossfade в ffmpeg 6.1 на этих файлах обрезает результат до ~2.5 с,
    поэтому используется fade-сшивка, а не кроссфейд.)
    """
    out = SOURCE_DIR / "bgm_loop.wav"

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(bgm)],
        capture_output=True, text=True, check=True,
    )
    dur = float(probe.stdout.strip())

    if out.exists() and out.stat().st_mtime >= bgm.stat().st_mtime:
        return out

    edge = 0.06
    override = SOURCE_DIR / "bgm_loop.json"
    if override.exists():
        # Трек уже сделан бесшовным по кругу — берём его петлю как есть,
        # не вырезая края: обрезка «ровного участка» ломает гармонию.
        conf = json.loads(override.read_text(encoding="utf-8"))
        start = float(conf.get("start", 0.0))
        length = float(conf.get("length", dur - start))
        length = max(4.0, min(length, dur - start))
        end = start + length
    else:
        start = min(2.5, dur * 0.1)              # пропускаем появление
        end = max(start + 8.0, dur - 7.0)        # и затухание в конце
        end = min(end, dur)
        length = end - start

    graph = (f"[0:a]atrim={start:.3f}:{end:.3f},asetpts=N/SR/TB,"
             f"afade=t=in:st=0:d={edge},"
             f"afade=t=out:st={max(0.0, length - edge):.3f}:d={edge}[out]")

    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(bgm),
         "-filter_complex", graph, "-map", "[out]",
         "-ar", "48000", "-ac", "2", str(out)],
        check=True,
    )
    print(f"[Assembly] Подложка: петля {length:.1f} с "
          f"(источник {dur:.1f} с, стык сшит микрофейдами)")
    return out


def abbreviate_court(name: str) -> str:
    """«Днепропетровский окружной административный суд» -> «ДОАС»."""
    if not name:
        return "СУД"
    words = [w for w in name.replace("\xa0", " ").split() if len(w) > 2]
    if len(words) <= 1:
        return name.upper()[:18]
    return "".join(w[0] for w in words).upper()


def measure_loudness(path: Path) -> dict:
    """Первый проход loudnorm: замер громкости, пика, LRA и порога."""
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(path),
         "-af", f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json",
         "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    blob = res.stderr[res.stderr.rfind("{"):res.stderr.rfind("}") + 1]
    return json.loads(blob)


def get_media_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path)
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return float(res.stdout.strip())

def prepare_scene_video(scene_id: int, target_dur: float) -> Path:
    """Prepares fitted_bg_{scene_id}.mp4: uses real Flow video if present, or creates fast placeholder."""
    fitted_clip = SOURCE_DIR / f"fitted_bg_{scene_id}.mp4"
    raw_bg = SOURCE_DIR / f"bg_{scene_id}.mp4"

    # If raw video from Flow exists and is not a placeholder
    if raw_bg.exists() and raw_bg.stat().st_size > 150000:
        raw_dur = get_media_duration(raw_bg)
        factor = target_dur / raw_dur if raw_dur > 0 else 1.0
        print(f"[Assembly] Сцена {scene_id}: Flow {raw_dur:.2f}s -> голос {target_dur:.2f}s (x{factor:.2f})")

        scale_part = ("scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,"
                      "crop=1080:1920,fps=30")

        if abs(raw_dur - target_dur) < 0.1:
            graph = f"[0:v]setpts=PTS-STARTPTS,{scale_part}[v]"
        elif raw_dur > target_dur:
            graph = f"[0:v]trim=0:{target_dur:.3f},setpts=PTS-STARTPTS,{scale_part}[v]"
        elif factor <= 1.90:
            # Кинематографичный Slow-Mo strictly forward: плавное замедление движения вперёд
            # Идеально подходит для макросъёмки, рек, каньонов и природы — вода течёт строго вперёд
            graph = f"[0:v]setpts={factor:.4f}*PTS,{scale_part}[v]"
        else:
            # При очень длинной сцене — только прямой повтор вперёд (без реверса!)
            graph = (
                f"[0:v][0:v]concat=n=2:v=1:a=0,"
                f"trim=0:{target_dur:.3f},setpts=PTS-STARTPTS,{scale_part}[v]"
            )

        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", str(raw_bg),
            "-filter_complex", graph,
            "-map", "[v]", "-an",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(fitted_clip)
        ]
        subprocess.run(cmd, check=True)
        return fitted_clip

    # Procedural placeholder (clean, ultra-fast generation with color filter)
    print(f"[Assembly] Сцена {scene_id}: создание плейсхолдера фона ({target_dur:.2f} сек)...")
    colors = ["#0A1128", "#1C2541", "#0B132B", "#1F2937"]
    col = colors[(scene_id - 1) % len(colors)]

    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi",
        "-i", f"color=c={col}:s=1080x1920:d={target_dur:.3f}:r=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(fitted_clip)
    ]
    subprocess.run(cmd, check=True)
    return fitted_clip

def main():
    if not TIMING_FILE.exists():
        print("Error: audio_timing.json not found! Run generate_audio.py first.")
        sys.exit(1)

    with open(TIMING_FILE, "r", encoding="utf-8") as f:
        timing_data = json.load(f)

    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    scenes_data = timing_data["scenes"]
    total_dur = timing_data["total_duration"]
    print(f"[Assembly] Старт сборки Shorts: общая длительность = {total_dur:.2f} сек")

    # Step 1: Prepare video clips for all 4 scenes
    prepared_segments = []
    for s_key, s_info in sorted(scenes_data.items(), key=lambda x: x[1]["scene_id"]):
        sid = s_info["scene_id"]
        d_target = s_info["duration"]
        clip = prepare_scene_video(sid, d_target)
        prepared_segments.append(clip)

    # Step 2: Concat video segments into base_video.mp4
    concat_txt = SOURCE_DIR / "concat_video.txt"
    with open(concat_txt, "w", encoding="utf-8") as f:
        for p in prepared_segments:
            f.write(f"file '{p.name}'\n")

    base_video = SOURCE_DIR / "base_video.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_txt),
        "-c", "copy",
        str(base_video)
    ], check=True)
    concat_txt.unlink(missing_ok=True)

    # Step 3: Filter complex for video overlays, cards, ASS subtitles, and audio ducking
    print("[Assembly] Формирование слоев: карточки суда, плашка дела, субтитры, ducking музыки...")

    # Состав карточек зависит от сцен: у сцены может стоять card_skip
    # (например, финальная шутка в несудёбном ролике — карточка там мешает).
    scene_flags = {s["scene_id"]: s for s in manifest.get("scenes", [])}
    ordered = sorted(scenes_data.items(), key=lambda x: x[1]["scene_id"])
    active = [(k, v) for k, v in ordered
              if not scene_flags.get(v["scene_id"], {}).get("card_skip")]

    cards = [CARDS_DIR / f"card_{v['scene_id']}.png" for _, v in active]
    missing = [c.name for c in cards if not c.exists()]
    if missing:
        print(f"Error: нет карточек: {', '.join(missing)}")
        sys.exit(1)
    print(f"[Assembly] Карточек в ролике: {len(cards)}")

    filter_complex = []

    # Subtle dimming in center zone (Y: 380..1060)
    filter_complex.append("[0:v]drawbox=x=0:y=0:w=1080:h=1920:color=black@0.20:t=fill[dimmed]")

    current_v = "[dimmed]"

    # Overlay cards one by one
    for i, (s_key, s_info) in enumerate(active):
        sid = s_info["scene_id"]
        card_in = f"[{i+1}:v]"
        st = s_info["start_time"]
        et = s_info["end_time"]
        out_v = f"[v_card_{sid}]"

        filter_complex.append(
            f"{card_in}scale=920:-1[c_scaled_{sid}]; "
            f"{current_v}[c_scaled_{sid}]overlay=80:430:enable='between(t,{st:.3f},{et:.3f})':format=auto{out_v}"
        )
        current_v = out_v

    # Top Case banner in Safe Zone (Y: 180..260)
    top_label = manifest.get("case_number", "")
    court_full = manifest.get("court_name", "")
    top_court = manifest.get("court_short") or abbreviate_court(court_full)
    # Верхняя плашка: для судебных роликов собирается из номера дела,
    # для остальных тем задаётся готовым banner_text в манифесте.
    if manifest.get("banner_text"):
        banner_text = manifest["banner_text"].upper()
    else:
        banner_text = f"ДЕЛО № {top_label} • {top_court}".upper()

    filter_complex.append(
        f"{current_v}drawbox=x=40:y=170:w=1000:h=76:color=#0F172A@0.88:t=fill,"
        f"drawbox=x=40:y=242:w=1000:h=4:color=#3B82F6@1.0:t=fill,"
        f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf:"
        f"text='{banner_text}':fontcolor=white:fontsize=28:x=(w-text_w)/2:y=190,"
        f"subtitles={ASS_FILE.as_posix()}:fontsdir=/usr/share/fonts/truetype/dejavu[v_final]"
    )

    # ------------------------------------------------------------------ #
    #  АУДИО: отдельный проход микса + двухпроходный loudnorm
    # ------------------------------------------------------------------ #
    # Почему отдельным проходом:
    #   однопроходный loudnorm динамический — в паузах он поднимает музыку
    #   почти до уровня речи («качает»). Двухпроходный (измерение -> линейное
    #   усиление) даёт ровный уровень и сохраняет разницу голос/подложка.
    fade_out_start = max(0.0, total_dur - BGM_FADE_OUT)
    mix_graph = (
        f"[1:a]highpass=f=140,lowpass=f=12000,"   # убираем бубнёж и шипение
        f"loudnorm=I={BGM_LUFS}:TP=-8:LRA=8,"
        f"afade=t=in:st=0:d={BGM_FADE_IN},"
        f"afade=t=out:st={fade_out_start:.2f}:d={BGM_FADE_OUT}[bg];"
        "[0:a]asplit=2[voice][sc];"
        "[bg][sc]sidechaincompress=threshold=0.04:ratio=5:attack=15:release=350[duck];"
        "[voice][duck]amix=inputs=2:duration=first:normalize=0[a]"
    )
    bgm_loop = prepare_bgm_loop(BGM_FILE)

    mix_wav = SOURCE_DIR / "mix_raw.wav"
    print("[Assembly] Аудио: микс голоса и подложки...")
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-t", f"{total_dur:.3f}",
         "-i", str(SOURCE_DIR / "vo_full.wav"),
         "-stream_loop", "-1", "-i", str(bgm_loop),
         "-filter_complex", mix_graph, "-map", "[a]",
         "-c:a", "pcm_s16le", "-ar", "48000", str(mix_wav)],
        check=True,
    )

    measured = measure_loudness(mix_wav)
    print(f"[Assembly] Замер микса: {measured['input_i']} LUFS, "
          f"пик {measured['input_tp']} dBTP -> цель -14 LUFS")

    loudnorm = (
        f"loudnorm=I=-14:TP=-1.5:LRA=11"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        f":linear=true:print_format=summary"
    )

    mix_index = 1 + len(cards)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-stats", "-y",
        "-t", f"{total_dur:.3f}",
        "-i", str(base_video),
    ]
    for card in cards:
        cmd += ["-i", str(card)]
    cmd += [
        "-i", str(mix_wav),
        "-filter_complex", ";".join(filter_complex),
        "-map", "[v_final]", "-map", f"{mix_index}:a",
        "-af", loudnorm,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-t", f"{total_dur:.3f}",
        str(OUTPUT_VIDEO)
    ]

    print("[Assembly] Запуск финального рендера FFmpeg...")
    subprocess.run(cmd, check=True)
    print(f"\n[Assembly] 🎉 ГОТОВО! Финальный ролик собран:")
    print(f"--> {OUTPUT_VIDEO} ({OUTPUT_VIDEO.stat().st_size} bytes, длительность: {total_dur:.2f} сек)")

if __name__ == "__main__":
    main()
