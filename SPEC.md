# Reddit-Story → TikTok Video Bot — Implementation Spec

> **Handoff document for Claude Code.** This is the authoritative build spec. Implement it phase by phase (see §8). Treat every "MUST" as a hard requirement and every acceptance criterion as a gate before moving to the next phase.

---

## 1. Goal

A fully-automated, $0/month pipeline that:
1. Pulls high-engagement stories from Reddit story subreddits.
2. Generates an AI voiceover of each story.
3. Lays it over Minecraft-parkour-style gameplay footage, cropped to TikTok vertical.
4. Burns in word-by-word "karaoke" captions.
5. Auto-uploads the finished vertical video to TikTok.

Runs unattended on a schedule, dedups stories, and requires near-zero manual input after setup.

---

## 2. Target Environment (LOCKED)

- **OS:** macOS (Apple Silicon, M-series).
- **Shell:** zsh. User home is `/Users/<user>` — never hardcode; read paths from config / `Path.home()`.
- **GPU:** Apple Metal (MPS). **No CUDA.** Do not install or assume CUDA/cuDNN. Whisper acceleration MUST use the Apple-Silicon path (`mlx-whisper`), not CUDA.
- **Python:** 3.11+ in a project-local `venv`.
- **Scheduler:** macOS **launchd** (`.plist`), not cron.
- **Upload target:** TikTok (auto-upload), with a mandatory review-gate safety toggle (see §6.9).

---

## 3. Final Tech Stack (use these exact choices)

| Concern | Library / Tool | Notes |
|---|---|---|
| Reddit fetch | `requests` + Reddit `.json` endpoint | No API key. Custom User-Agent MANDATORY. PRAW optional fallback only if user supplies approved creds. |
| Dedup store | `sqlite3` (stdlib) | Table of used post IDs. |
| TTS (default) | `edge-tts` | Free, no key, online, outputs MP3 + SRT. Default voice `en-US-GuyNeural`. |
| TTS (offline fallback) | `kokoro` (Kokoro-82M) | Local, runs on CPU/MPS. Use only if edge-tts rate-limits. |
| Transcription / word timing | `mlx-whisper` | **Apple-Silicon Metal-accelerated.** Fallback: `faster-whisper` (CPU) if mlx unavailable. |
| Captions | Custom ASS generator (karaoke `\k` tags) | Burned via FFmpeg libass. |
| Background download | `yt-dlp` | Download once, cache, reuse. |
| Video assembly | **FFmpeg via `subprocess`** | MoviePy NOT used (slower, dependency pain). |
| TikTok upload | `tiktok-uploader` (wkaisertexas) | Cookie/Selenium based. Dedicated throwaway account. |
| Config | `tomllib` (stdlib, read) + a `config.toml` | |
| Orchestration | `main.py` + launchd | |
| Logging | stdlib `logging` → `logs/` | |

Install system deps first: `brew install ffmpeg yt-dlp`. ChromeDriver for the uploader is handled by `webdriver_manager` (arm64 build).

---

## 4. Repository Structure

```
reddit-tiktok-bot/
├── config.toml                 # all tunables (see §5)
├── .env                        # secrets: TIKTOK cookies path, optional reddit creds (gitignored)
├── requirements.txt
├── main.py                     # orchestrator (§6.10)
├── pipeline/
│   ├── __init__.py
│   ├── scrape.py               # §6.1
│   ├── filter.py               # §6.2
│   ├── clean.py                # §6.3
│   ├── tts.py                  # §6.4
│   ├── transcribe.py           # §6.5
│   ├── captions.py             # §6.6
│   ├── background.py           # §6.7
│   ├── assemble.py             # §6.8
│   └── upload.py               # §6.9
├── core/
│   ├── config.py               # load + validate config.toml
│   ├── db.py                    # sqlite dedup helpers
│   └── logging_setup.py
├── data/
│   ├── used_stories.db
│   ├── backgrounds/            # cached gameplay loops (.mp4)
│   ├── cookies/                # tiktok cookies.txt (gitignored)
│   ├── temp/<post_id>/         # per-run working files
│   └── output/                 # finished MP4s (and review queue)
├── scripts/
│   └── com.user.reddittiktok.plist   # launchd template
└── logs/
```

`data/`, `.env`, `logs/`, and cookies MUST be gitignored.

---

## 5. `config.toml` Schema

Implement `core/config.py` to load and validate this. Provide sane defaults; fail loudly on missing required keys.

```toml
[reddit]
subreddits   = ["tifu", "AmItheAsshole", "stories", "confession", "offmychest"]
listing      = "top"          # top | hot | new
time_filter  = "week"         # hour|day|week|month|year|all (top only)
limit        = 25             # posts fetched per subreddit per run
user_agent   = "Reddit-Story-Bot/1.0 (by /u/CHANGE_ME)"  # MUST be customized

[filter]
min_words      = 80
max_words      = 600
min_score      = 500
allow_nsfw     = false
profanity_mode = "soft"        # off | soft (mask) | strict (skip post)

[tts]
engine = "edge"                # edge | kokoro
voice  = "en-US-GuyNeural"
rate   = "+8%"
pause_between_sentences_ms = 120

[whisper]
backend    = "mlx"             # mlx | faster
model      = "small.en"        # small.en is a good speed/quality balance on M-series
word_level = true

[captions]
font          = "Arial Black"
font_size     = 22
primary_color = "&H00FFFFFF"   # ASS BGR: white
highlight     = "&H0000FFFF"   # ASS BGR: yellow karaoke highlight
outline       = 3
words_per_cue = 1              # 1 = one-word "brainrot" style
margin_v      = 360            # keep captions in safe zone (above TikTok UI)

[background]
source_urls = ["https://www.youtube.com/watch?v=CHANGE_ME"]  # "no copyright" parkour loop(s)
cache_dir   = "data/backgrounds"
audio_volume = 0.0            # game audio under VO; 0.0–0.15

[video]
width = 1080
height = 1920
fps = 30
video_bitrate = "10M"
audio_bitrate = "192k"
target_max_seconds = 90        # skip stories whose VO exceeds this

[upload]
platform   = "tiktok"
review_gate = true             # TRUE = save to output/ and WAIT for manual approval; FALSE = auto-post
cookies_file = "data/cookies/tiktok_cookies.txt"
caption_template = "{title} 😳 #reddit #story #storytime #fyp"
schedule_minutes_apart = 0     # 0 = post immediately

[run]
videos_per_run = 1
```

---

## 6. Module Specifications

Each module is a pure-ish function with explicit inputs/outputs so stages are independently testable and resumable. Write to `data/temp/<post_id>/` so a crash can resume.

### 6.1 `scrape.py`
- **Function:** `fetch_candidates(cfg) -> list[Story]`
- Build URL: `https://www.reddit.com/r/{sub}/{listing}.json?limit={limit}&t={time_filter}`.
- **MUST** send the custom `User-Agent` header. Reddit 403s generic agents.
- Add a 2–4s delay between subreddit requests; retry with exponential backoff on 429/403/5xx (max 4 tries).
- Parse `data.data.children[].data`; extract `id, title, selftext, score, num_comments, over_18, subreddit, permalink`.
- Return a `Story` dataclass (`core` or `scrape` level). Drop posts with empty `selftext` (link posts).
- **Optional PRAW fallback:** if `.env` has `REDDIT_CLIENT_ID/SECRET`, allow `--source praw`. Not required for v1.
- **Acceptance:** prints N candidate titles + word counts to console with no key configured.

### 6.2 `filter.py`
- **Function:** `keep(story, cfg, db) -> bool`
- Reject if: `db.is_used(story.id)`; `over_18 and not allow_nsfw`; word count outside `[min_words, max_words]`; `score < min_score`; profanity per `profanity_mode` (`strict` → skip; `soft` → handled in clean).
- Maintain a small configurable profanity wordlist in `core/`.
- **Acceptance:** given a list of candidates, returns only novel, in-range, SFW stories.

### 6.3 `clean.py`
- **Function:** `normalize(story) -> str`
- Strip markdown (`**`, `>`, links `[text](url)`), remove raw URLs, collapse whitespace, fix Reddit-isms.
- Expand abbreviations for natural TTS: `AITA→"Am I the asshole"`, `TIFU→"Today I fucked up"` (or masked variant in soft mode), `WIBTA`, `edit:` handling, `f→female / m→male` age tags like `(28F)` → "28 female".
- Soft profanity: mask to keep TikTok-friendly if `profanity_mode="soft"`.
- Prepend the title as the spoken hook.
- **Acceptance:** clean, punctuation-correct plain text ready for TTS.

### 6.4 `tts.py`
- **Function:** `synthesize(text, cfg, out_dir) -> AudioResult(path, duration_s)`
- Split text into sentences. For each sentence call the engine; concatenate with a short silence (`pause_between_sentences_ms`) into one MP3/WAV via FFmpeg concat.
- **edge-tts:** `edge_tts.Communicate(sentence, voice, rate=cfg.rate)`; `await asyncio.sleep(1.0)` between calls to dodge rate limits; retry on transient errors.
- **kokoro fallback:** load model once, generate per sentence on MPS/CPU.
- Compute total duration; if `> video.target_max_seconds`, signal "too long" so orchestrator skips.
- **Acceptance:** single audio file plays the full story cleanly; duration returned.

### 6.5 `transcribe.py`
- **Function:** `word_timestamps(audio_path, cfg) -> list[Word(text, start, end)]`
- `backend="mlx"` → use `mlx_whisper.transcribe(audio_path, word_timestamps=True, ...)`.
- `backend="faster"` → `faster_whisper.WhisperModel(model, device="cpu", compute_type="int8")`, iterate `segment.words`.
- Since the exact transcript is known, prefer feeding/initial-prompting the known text to improve alignment; flatten to a word list with start/end seconds.
- **Acceptance:** word list whose timings visually line up with the audio (spot-check a few words).

### 6.6 `captions.py`
- **Function:** `build_ass(words, cfg, out_path) -> ass_path`
- Group words into cues of `words_per_cue` (default 1). Generate an **ASS** file with a styled `[V4+ Styles]` block (font, size, colors, outline, `MarginV`) and karaoke `\k` timing so the active word highlights.
- Colors are ASS BGR `&HAABBGGRR` — convert carefully, do NOT use CSS hex.
- Keep `MarginV` high enough that text sits above TikTok's bottom UI and clear of the right-edge action buttons.
- **Acceptance:** burning the ASS over a test clip shows correctly-timed one-word captions in the safe zone.

### 6.7 `background.py`
- **Functions:** `ensure_cached(cfg) -> list[Path]` and `make_clip(bg_path, duration_s, cfg, out_path) -> Path`
- `ensure_cached`: for each `source_urls`, if not already in `cache_dir`, download best MP4 with `yt-dlp` (`-f "bv*[ext=mp4]"` or merged) into cache.
- `make_clip`: pick a random cached bg, choose a random start offset such that `start + duration_s` fits the source length, trim, then crop-fill to 9:16:
  `scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}`.
- **Acceptance:** outputs a silent (or low-audio) 1080×1920 clip exactly matching VO duration.

### 6.8 `assemble.py`
- **Function:** `render(bg_clip, voice_audio, ass_file, cfg, out_path) -> Path`
- Single FFmpeg pass: map bg video + VO audio, mix in low game audio at `background.audio_volume`, burn `ass`, encode H.264 + AAC, `+faststart`.
- Reference command (Claude Code: parametrize from config):
  ```
  ffmpeg -y -i bg.mp4 -i voice.mp3 \
    -filter_complex "[0:v]ass=captions.ass[v];[0:a]volume=0.0[ga];[1:a][ga]amix=inputs=2:duration=first[a]" \
    -map "[v]" -map "[a]" \
    -c:v libx264 -b:v 10M -pix_fmt yuv420p -r 30 \
    -c:a aac -b:a 192k -ar 44100 \
    -shortest -movflags +faststart out.mp4
  ```
- Normalize VO loudness (loudnorm) before/within the pass.
- **Acceptance:** final MP4 meets §7 specs and plays correctly with synced captions and audible VO.

### 6.9 `upload.py`
- **Function:** `publish(video_path, title, cfg, db) -> None`
- **Review gate (default ON):** if `upload.review_gate = true`, copy the video to `data/output/_review/` and STOP — do not post. The user inspects, then runs `python main.py --approve <post_id>` (or drops/renames the file into an `approved/` dir) to actually post. This protects the account during early runs.
- **Auto-post path:** use `tiktok-uploader` with `cookies_file`. Build the caption from `caption_template`. On success, `db.mark_used(post_id, platform="tiktok")`.
- **Account safety:** use a DEDICATED/throwaway TikTok account. Cookie uploaders violate TikTok ToS and break on UI changes — wrap in try/except, log failures, never crash the batch.
- **Acceptance:** with review_gate on, video lands in review folder + DB NOT marked; with a valid cookie and gate off, a test post appears on the throwaway account.

### 6.10 `main.py` (orchestrator)
- Load config + DB + logging. For `run.videos_per_run` iterations:
  1. `fetch_candidates` → first one passing `filter.keep`.
  2. `clean.normalize` → `tts.synthesize` (skip if too long) → `transcribe` → `captions.build_ass` → `background.make_clip` → `assemble.render` → `upload.publish`.
  3. On any stage failure: log, clean temp, skip to next candidate (don't abort the run).
- CLI flags: `--dry-run` (no upload), `--approve <id>`, `--limit N`.
- **MUST be idempotent:** mark a story used only on terminal success (or after upload), and check the DB before processing.

---

## 7. TikTok Output Specs (assemble.py must hit these)

- Resolution **1080×1920**, aspect **9:16**.
- Container MP4, video **H.264**, `yuv420p`, **30 fps**.
- Audio **AAC**, 44.1 kHz, ~192 kbps.
- Video bitrate ~**8–12 Mbps** (survives TikTok re-encode).
- `+faststart` enabled.
- Keep captions/important visuals out of the bottom ~15% and clear of the right edge (TikTok UI overlays).
- Hook in the first 1–2 seconds; target length ~21–60s, hard cap configurable (`target_max_seconds`).

---

## 8. Build Order (phased — implement and verify in this sequence)

**Phase 0 — Scaffold.** Repo structure, `config.py` loader, `logging_setup`, `db.py` (create table `used(post_id TEXT PRIMARY KEY, title, platform, created_at)`), `requirements.txt`, `.gitignore`.
✅ *Gate:* `python main.py --dry-run` runs and loads config without error.

**Phase 1 — Scrape + filter + dedup.** `scrape.py`, `filter.py`, wire into `main.py` to print chosen story.
✅ *Gate:* prints one novel, in-range, SFW story; re-running skips it (DB works).

**Phase 2 — Clean + TTS.** `clean.py`, `tts.py` (edge-tts).
✅ *Gate:* produces a clean audio file of the full story with correct duration.

**Phase 3 — Background.** `background.py` (yt-dlp cache + random-trim + 9:16 crop).
✅ *Gate:* silent 1080×1920 clip matching VO length.

**Phase 4 — Assemble (no captions).** `assemble.py` muxing audio+video.
✅ *Gate:* playable vertical MP4, VO over gameplay, meets §7.

**Phase 5 — Captions.** `transcribe.py` (mlx-whisper) + `captions.py` (ASS karaoke) + burn-in.
✅ *Gate:* one-word highlighted captions, synced, in safe zone.

**Phase 6 — Upload with review gate.** `upload.py` in `review_gate=true` mode.
✅ *Gate:* finished video lands in review folder; DB not yet marked.

**Phase 7 — Live auto-upload + scheduling.** Flip `review_gate=false` on a throwaway account; add launchd plist; full unattended run.
✅ *Gate:* scheduled run posts to the test account and marks the story used.

---

## 9. macOS / Apple Silicon Setup Notes (for the README Claude Code writes)

```bash
# system deps
brew install ffmpeg yt-dlp python@3.11

# project
python3.11 -m venv venv && source venv/bin/activate
pip install -U pip
pip install -r requirements.txt   # edge-tts, requests, mlx-whisper, tiktok-uploader, kokoro (optional), webdriver_manager
```

- `mlx-whisper` is the Metal-accelerated Whisper for M-series — install it instead of any CUDA build. If it can't be installed, fall back to `faster-whisper` on CPU (`compute_type="int8"`); short clips are still fast.
- Any PyTorch use (Kokoro) should select `mps` device when available, else `cpu`.
- ChromeDriver is auto-managed by `webdriver_manager` (arm64). Ensure Google Chrome is installed for the uploader.

---

## 10. Scheduling via launchd (not cron)

Provide `scripts/com.user.reddittiktok.plist` that runs `main.py` on an interval, e.g. daily:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.user.reddittiktok</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/CHANGE_ME/reddit-tiktok-bot/venv/bin/python</string>
    <string>/Users/CHANGE_ME/reddit-tiktok-bot/main.py</string>
  </array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>10</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/Users/CHANGE_ME/reddit-tiktok-bot/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>/Users/CHANGE_ME/reddit-tiktok-bot/logs/launchd.err.log</string>
  <key>WorkingDirectory</key><string>/Users/CHANGE_ME/reddit-tiktok-bot</string>
</dict></plist>
```
Load: `launchctl load ~/Library/LaunchAgents/com.user.reddittiktok.plist`.

---

## 11. TikTok Auth Setup (one-time, manual)

1. Log into the **dedicated throwaway** TikTok account in Chrome.
2. Export cookies with a "Get cookies.txt" browser extension → save to `data/cookies/tiktok_cookies.txt`.
3. Point `upload.cookies_file` at it. Cookies expire periodically — re-export when uploads start failing auth.

---

## 12. Cross-Cutting Requirements

- **Idempotency & resume:** per-stage artifacts in `data/temp/<post_id>/`; re-running reuses existing artifacts.
- **Dedup is non-negotiable:** check `db.is_used` before processing; mark used only on success/upload.
- **Retries/backoff** on all network calls (Reddit, edge-tts, yt-dlp, upload).
- **Never crash the batch:** a failing story is logged and skipped.
- **Secrets** only in `.env`/cookies, never committed.
- **Config-driven:** no magic numbers in code; everything tunable lives in `config.toml`.

---

## 13. Testing Checklist

- [ ] `.json` fetch returns posts with the custom UA (and 403s without it — confirm UA matters).
- [ ] Dedup prevents reprocessing across two runs.
- [ ] Filters correctly drop NSFW / out-of-range / low-score / profane posts.
- [ ] TTS handles a long multi-paragraph story without truncation.
- [ ] Whisper word timings align (spot-check 5 words).
- [ ] Captions sit in the safe zone on a real phone preview.
- [ ] Output MP4 validated against §7 (`ffprobe`).
- [ ] Review-gate path stops before posting; approve path posts once.
- [ ] launchd job fires and completes unattended.

---

## 14. Risks & Caveats (build defensively around these)

- **Reddit `.json`** works as of mid-2026 but is contested/undocumented; keep the PRAW fallback path stubbed so a future shutdown is a config switch, not a rewrite. Data-center IPs get 403s more than residential.
- **edge-tts** can rate-limit on heavy batches and is a gray-area use of Microsoft's service; the Kokoro local fallback exists for this reason.
- **yt-dlp** downloads and **"no-copyright" footage** are ToS/licensing gray areas — fine for personal experimentation; verify licenses before monetizing.
- **TikTok cookie uploader** violates TikTok ToS, can get the account banned, and breaks on UI changes. Use a throwaway account; keep the review gate on until you trust output quality; expect maintenance.
- **Pin dependency versions** in `requirements.txt`; this ecosystem churns fast.
- **Content suitability:** story subreddits contain profanity/sensitive themes even when SFW — keep filtering on and the review gate on initially.

---

## 15. Reference Projects (read before/while building — do not copy GPL code into an MIT project)

- **RedditVideoMakerBot** (elebumm, GPL-3.0) — canonical Reddit→video architecture; lift the config schema, sentence splitting, and FFmpeg patterns conceptually.
- **MoneyPrinterTurbo** (harry0703, MIT) — clean modular reference: TTS → Whisper subtitles → FFmpeg 9:16 assembly.
- **FullyAutomatedRedditVideoMakerBot** (raga70) — reference for the uploader integration the original omits.
- **tiktok-uploader** (wkaisertexas, MIT) — the upload dependency itself.

---

### First instruction to Claude Code
> Implement Phase 0 then Phase 1 from §8. Create the repo structure in §4, the `config.toml` from §5, and `core/config.py`, `core/db.py`, `core/logging_setup.py`, `pipeline/scrape.py`, `pipeline/filter.py`, and a minimal `main.py` that prints the chosen story. Stop at the Phase 1 gate and show me the output before continuing.
