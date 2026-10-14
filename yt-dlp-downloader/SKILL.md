---
name: yt-dlp-downloader
description: Download videos from YouTube, Bilibili, Twitter, and thousands of other sites using yt-dlp. Use when the user provides a video URL and wants to download it, extract audio (MP3), download subtitles, or select video quality. Triggers on phrases like "下载视频", "download video", "yt-dlp", "YouTube", "B站", "抖音", "提取音频", "extract audio".
---

# yt-dlp Video Downloader

Download videos from thousands of websites using yt-dlp.

## Prerequisites

Before downloading, verify dependencies are installed:

```bash
# Check yt-dlp
which yt-dlp || echo "yt-dlp not installed. Install with: pip install yt-dlp"

# Check ffmpeg (required for audio extraction and format merging)
which ffmpeg || echo "ffmpeg not installed. Install with: brew install ffmpeg"
```

If not installed, install them first:
```bash
pip install yt-dlp
brew install ffmpeg  # macOS
```

## Quick Start

### Basic Download (Best Quality)

```bash
yt-dlp -P "~/Downloads/yt-dlp" "VIDEO_URL"
```

### YouTube Download (Recommended - with cookies)

YouTube often blocks direct downloads with 403 errors. Always use browser cookies for YouTube.

Newer yt-dlp (2025.11+) also solves YouTube JS challenges (EJS), which requires:
1. The `yt-dlp-ejs` package — for pipx installs run: `pipx inject yt-dlp yt-dlp-ejs`
2. An enabled JS runtime — node 20+ works; pass it explicitly with `--js-runtimes node`

```bash
yt-dlp -P "~/Downloads/yt-dlp" --js-runtimes node --cookies-from-browser chrome "YOUTUBE_URL"
```

Supported browsers: `chrome`, `firefox`, `safari`, `edge`, `brave`, `opera`

### Download with Custom Output Path

```bash
yt-dlp -P "/path/to/save" -o "%(title)s.%(ext)s" "VIDEO_URL"
```

## Common Tasks

### 1. Download Video (Default - Best Quality)

```bash
yt-dlp -P "~/Downloads/yt-dlp" "VIDEO_URL"
```

### 2. Extract Audio Only (MP3)

```bash
yt-dlp -P "~/Downloads/yt-dlp" -x --audio-format mp3 "VIDEO_URL"
```

### 3. Download with Subtitles

```bash
yt-dlp -P "~/Downloads/yt-dlp" --write-subs --sub-langs all "VIDEO_URL"
```

### 4. Download Specific Quality

**720p:**
```bash
yt-dlp -P "~/Downloads/yt-dlp" -f "bestvideo[height<=720]+bestaudio/best[height<=720]" "VIDEO_URL"
```

**1080p:**
```bash
yt-dlp -P "~/Downloads/yt-dlp" -f "bestvideo[height<=1080]+bestaudio/best[height<=1080]" "VIDEO_URL"
```

**Best available:**
```bash
yt-dlp -P "~/Downloads/yt-dlp" -f "bestvideo+bestaudio/best" "VIDEO_URL"
```

### 5. List Available Formats (Before Download)

```bash
yt-dlp -F "VIDEO_URL"
```

Then download specific format by ID:
```bash
yt-dlp -P "~/Downloads/yt-dlp" -f FORMAT_ID "VIDEO_URL"
```

### 6. Download Playlist

```bash
# Download entire playlist
yt-dlp -P "~/Downloads/yt-dlp" -o "%(playlist)s/%(playlist_index)s - %(title)s.%(ext)s" "PLAYLIST_URL"

# Download specific range (e.g., items 1-5)
yt-dlp -P "~/Downloads/yt-dlp" -I 1:5 "PLAYLIST_URL"
```

### 7. Download with Thumbnail

```bash
yt-dlp -P "~/Downloads/yt-dlp" --write-thumbnail "VIDEO_URL"
```

### 8. Download MP4 (QuickTime/iOS compatible)

The default "best quality" pick often pairs an mp4 video stream with opus (webm) audio. Those two cannot merge into mp4, so yt-dlp falls back to an **.mkv** output — which macOS QuickTime cannot play. For maximum compatibility force H.264 + AAC in an mp4 container:

```bash
yt-dlp -P "~/Downloads/yt-dlp" -f "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b" --merge-output-format mp4 "VIDEO_URL"
```

Note: this caps quality at whatever mp4 (H.264) streams are available (usually 1080p). Above 1080p, YouTube only offers VP9/AV1 webm video, so >1080p downloads are always .webm/.mkv.

## Workflow

When user provides a video URL:

1. **Identify the platform**:
   - YouTube/YouTube Music → **Always use `--cookies-from-browser chrome`**
   - Other sites → Try without cookies first

2. **Ask what they want** (if not specified):
   - Just download the video?
   - Extract audio only?
   - Need subtitles?
   - Specific quality?

3. **Construct the command** based on requirements

4. **Execute the download** using Shell tool with `required_permissions: ["all", "network"]`

5. **Handle errors**:
   - 403 Forbidden → Retry with `--cookies-from-browser`
   - Connection issues → yt-dlp auto-resumes, just retry
   - Format unavailable → Use `-F` to list formats, then select

6. **Report the result** - file location and any errors

## Example Interaction

User: "帮我下载这个视频 https://www.youtube.com/watch?v=xxx"

Response:
```bash
# YouTube - use cookies to avoid 403 errors
yt-dlp -P "~/Downloads/yt-dlp" --cookies-from-browser chrome "https://www.youtube.com/watch?v=xxx"
```

User: "下载这个视频的音频 https://www.bilibili.com/video/xxx"

Response:
```bash
# Bilibili - extracting audio as MP3
yt-dlp -P "~/Downloads/yt-dlp" -x --audio-format mp3 "https://www.bilibili.com/video/xxx"
```

User: "下载这个 Twitter 视频 https://twitter.com/xxx/status/123"

Response:
```bash
# Twitter/X - direct download usually works
yt-dlp -P "~/Downloads/yt-dlp" "https://twitter.com/xxx/status/123"
```

## Supported Sites

yt-dlp supports thousands of sites including:
- YouTube, YouTube Music
- Bilibili (B站)
- Twitter/X
- TikTok, Douyin (抖音)
- Vimeo
- Twitch
- And many more...

Full list: https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md

## Troubleshooting

### Common Errors and Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| Signature solving failed / "n challenge solving failed" / "The page needs to be reloaded" | Missing `yt-dlp-ejs` solver package or no JS runtime enabled | `pipx inject yt-dlp yt-dlp-ejs`, then add `--js-runtimes node` (node 20+) |
| HTTP 403 Forbidden | YouTube blocks unauthenticated requests | Use `--cookies-from-browser chrome` |
| Video unavailable | Geo-restricted or private | Use cookies or VPN |
| Output is .mkv and QuickTime can't open it | Merged mp4 video + opus audio forces mkv container; QuickTime lacks mkv/opus support | Re-download with `-f "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b" --merge-output-format mp4` |
| Download interrupted | Network issues | Retry - yt-dlp auto-resumes |
| Format not available | Requested format doesn't exist | Use `-F` to list formats |

### Error: "yt-dlp: command not found"
```bash
pip install yt-dlp
```

### Error: "ffmpeg not found" (for audio extraction)
```bash
brew install ffmpeg  # macOS
```

### Error: HTTP 403 Forbidden (YouTube)

This is the most common YouTube error. **Always use cookies for YouTube:**

```bash
# Recommended approach for YouTube
yt-dlp -P "~/Downloads/yt-dlp" --cookies-from-browser chrome "YOUTUBE_URL"
```

Supported browsers: `chrome`, `firefox`, `safari`, `edge`, `brave`, `opera`

### Error: "Signature solving failed" / "The page needs to be reloaded" (YouTube)

Newer yt-dlp versions solve YouTube's JS challenges via the EJS framework. If you see `Signature solving failed` or `n challenge solving failed` warnings followed by `ERROR: The page needs to be reloaded`, you're missing one of:

1. **Solver scripts** — install into the same environment as yt-dlp:
   ```bash
   pipx inject yt-dlp yt-dlp-ejs   # for pipx installs; pip install yt-dlp-ejs for pip venvs
   ```
2. **JS runtime** — node 20+, deno, bun, or quickjs. Enable explicitly:
   ```bash
   yt-dlp --js-runtimes node --cookies-from-browser chrome "URL"
   ```
   deno is yt-dlp's default runtime if installed (`brew install deno`).

### Error: Video unavailable or geo-restricted
```bash
# Try with cookies from browser
yt-dlp --cookies-from-browser chrome "VIDEO_URL"

# Or use a specific format
yt-dlp -F "VIDEO_URL"  # List formats first
yt-dlp -f FORMAT_ID "VIDEO_URL"
```

### Error: Download keeps failing
```bash
# Update yt-dlp to latest version
pip install -U yt-dlp

# Force IPv4 (sometimes helps with connection issues)
yt-dlp -4 "VIDEO_URL"
```

### Best Practices

1. **YouTube downloads**: Always use `--cookies-from-browser chrome`
2. **Large files**: yt-dlp auto-resumes, just retry if interrupted
3. **Keep yt-dlp updated**: `pip install -U yt-dlp`
4. **Check formats first**: Use `-F` before downloading if unsure
