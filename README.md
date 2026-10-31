# 🎬 Svobodnye Lyudi YouTube Shorts Pipeline

> **AI Legal & Sci-Fi VFX YouTube Shorts Factory**  
> *Google Flow (Gemini Omni 1.1 Flash) + FFmpeg + Playwright CDP + Edge-TTS + Faster-Whisper*

Автоматизированный конвейер полного цикла для производства вертикальных **YouTube Shorts (1080x1920)** на русском языке для канала **«Свободные Люди Запорожья»**. Конвейер генерирует уникальный видеоряд с эффектами «Невозможной камеры» (Impossible VFX), накладывает всплывающие карточки цитат с подсветкой, караоке-субтитры в безопасной зоне и озвучивает ролик эталонным мужским голосом `ru-RU-DmitryNeural`.

---

## 🌟 Витрина эталонных роликов (Showcase)

Все 6 роликов созданы конвейером, прошли контроль качества 5/5 и опубликованы на YouTube:

| # | Тема / Сюжет | Ключевые спецэффекты (Impossible VFX) | Длительность | Ссылка на YouTube Short |
|---|---|---|---|---|
| **1** | **🐋 Алфавит кашалотов** | Подводный сканер частот, 3D-фонемы китов в толще воды | 58.9 с | [Смотреть Short](https://www.youtube.com/shorts/QOPSX3P65oI) |
| **2** | **🏛️ Река — это гражданин** | Изумрудный каньон, макро-вода на гальке, радужные водопады | 58.9 с | [Смотреть Short](https://www.youtube.com/shorts/zR_63VnEimQ) |
| **3** | **🤖 ChatGPT судья снесен** | Голографический робот-судья, отмена приговора апелляцией | 59.8 с | [Смотреть Short](https://www.youtube.com/shorts/7ZUAvCP5lC8) |
| **4** | **🌊 Океан, спутник и $2,2 млн** | Разрез океана как аквариум, застывший 3D-кильватер, снос заборов | 50.9 с | [Смотреть Short](https://www.youtube.com/shorts/8UplhLITKyY) |
| **5** | **🌲 Голос леса и LiDAR** | Рентген дуба с нейро-импульсами, звуковая волна пилы, LiDAR | 49.7 с | [Смотреть Short](https://www.youtube.com/shorts/_dWDaIozmkQ) |
| **6** | **⚖️ ИИ-рентген договоров** | Каньон мелкого шрифта с капканами, весы Фемиды, срез газовой трубы | 39.8 с | [Смотреть Short](https://www.youtube.com/shorts/s5sJKPo7DnA) |

---

## 🛠️ Архитектура конвейера (Audio-First)

```text
               +----------------------------------+
               |          manifest.json           |
               +-----------------+----------------+
                                 |
        +------------------------+------------------------+
        |                                                 |
        v                                                 v
[1. Озвучка Edge-TTS]                          [2. Видеоряд Google Flow]
 ru-RU-DmitryNeural                             Gemini Omni 1.1 Flash
 (Строго 100% мужской голос,                     (CDP 9222, 9:16, 720p, 10s,
  без женских gTTS фолбэков)                     Impossible VFX промпты)
        |                                                 |
        v                                                 v
[audio_timing.json]                            [source/bg_1..4.mp4]
 Миллисекундная разметка                                  |
        |                                                 |
        +------------------------+------------------------+
                                 |
                                 v
                     [3. Подгонка видео (FFmpeg)]
                      Кинематографичный Slow-Mo вперёд
                      (setpts={factor}*PTS, factor <= 1.90)
                      ЗАПРЕТ на реверс ("пинг-понг")
                                 |
        +------------------------+------------------------+
        |                                                 |
        v                                                 v
[4. Карточки документов]                       [5. Субтитры Faster-Whisper]
 HTML/CSS + Playwright                          Стиль Highlight (.ass)
 Жёлтый маркер <mark>                           Safe Zone (MarginV = 520)
        |                                                 |
        +------------------------+------------------------+
                                 |
                                 v
                     [6. Финальная сборка FFmpeg]
                      Нормализация: -14.1 LUFS (Shorts)
                      Сайдчейн-дакинг музыки (-14 дБ)
                      Масштабирование: Lanczos 1080x1920
                                 |
                                 v
                     [7. Контроль качества (5/5)]
                      verify_short.py: разрешение, FPS,
                      Safe Zone, отсутствие женского голоса
                                 |
                                 v
                     [8. Автозагрузка YouTube API]
                      Unlisted загрузка + thumbnail.jpg
                      Проверка плеера: check_shorts.py
```

---

## 🚀 Быстрый старт (Quickstart)

### 1. Установка зависимостей
```bash
git clone git@github.com:Sereban-glitch/svobodnye-lyudi-shorts-pipeline.git
cd svobodnye-lyudi-shorts-pipeline
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 2. Запуск Chrome с открытым портом отладки
```bash
google-chrome --remote-debugging-port=9222
```
В открывшемся браузере перейдите на [flow.google.com](https://flow.google.com).

### 3. Сборка нового ролика из готового манифеста
```bash
# 1. Синтез дикторской речи
python3 scripts/generate_audio.py --project projects/my_case

# 2. Генерация карточек цитат
python3 scripts/generate_cards.py --project projects/my_case

# 3. Генерация субтитров
python3 scripts/generate_subtitles.py --project projects/my_case

# 4. Генерация видеосцен в Google Flow
python3 scripts/flow_drive.py --project projects/my_case

# 5. Сборка финального Shorts
python3 scripts/build_short.py --project projects/my_case

# 6. Авто-тест качества (5/5)
python3 scripts/verify_short.py --project projects/my_case

# 7. Загрузка на YouTube (unlisted)
python3 scripts/upload_youtube.py --project projects/my_case
```

---

## 📁 Структура репозитория

* **`scripts/`** — автономные модули сборки, генерации, проверки и публикации.
* **`manifests/`** — каталог проверенных манифестов с таймингами, русским дикторским текстом и английскими промптами.
* **`docs/`** — технические стандарты:
  * `domdara_google_flow_playbook.md` — мастер-плейбук автоматизации Google Flow и приёмы Impossible VFX.
  * `safe_zones_and_typography.md` — безопасные зоны мобильного экрана, отступы и правила вёрстки.
* **`skills/flow-shorts/`** — упакованный локальный навык для интеграции в OpenCode.
* **`config.example.json`** — шаблон конфигурации YouTube API.
* **`requirements.txt`** — список необходимых библиотек Python.

---

## 🔒 Безопасность и правила

Репозиторий защищён строгим `.gitignore`:
* Токены доступа (`token.json`), ключи Google Cloud (`client_secret*.json`) и сессионные куки гарантированно исключены из контроля версий.
* Все медиафайлы (`*.mp4`, `*.wav`, `*.mp3`) и временные кэши рендеринга удалены из индекса git.
* В коде скриптов используются динамические пути (`Path.home()`), обеспечивающие переносимость между машинами.

---

## 📜 Лицензия
MIT License. Создано для канала «Свободные Люди Запорожья».
