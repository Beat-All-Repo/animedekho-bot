"""Download manager — Multi-engine with N_m3u8DL-RE, Direct HTTP, and FFmpeg fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from bot.telegram import Client, enums
from bot.telegram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from api.models import Quality

log = logging.getLogger(__name__)

TG_UPLOAD_LIMIT = 2 * 1024 * 1024 * 1024

_TEMP_BASE = Path(tempfile.gettempdir()) / "animedekho_dl"
_TEMP_BASE.mkdir(parents=True, exist_ok=True)


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name)
    name = re.sub(r'[\s]+', ' ', name).strip()
    return name[:120] or "video"


def make_episode_filename(series_title: str, season: int, episode: int, quality: str) -> str:
    return f"{sanitize_filename(series_title)} S{season:01d}E{episode:02d} [{quality}].mp4"


def make_movie_filename(movie_title: str, quality: str) -> str:
    return f"{sanitize_filename(movie_title)} [{quality}].mp4"


# ── Stylish Progress ──────────────────────────────────────────────────


_SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_spin_idx = 0

def _spinner() -> str:
    global _spin_idx
    _spin_idx = (_spin_idx + 1) % len(_SPIN_FRAMES)
    return _SPIN_FRAMES[_spin_idx]


def _progress_bar(pct: float, width: int = 20) -> str:
    """Smooth animated progress bar with gradient fill."""
    filled_exact = max(0.0, min(100.0, pct)) / 100 * width
    filled = int(filled_exact)
    partials = ["", "▏", "▎", "▍", "▌", "▋", "▊", "▉"]
    partial_idx = int((filled_exact - filled) * len(partials))

    if filled >= width:
        return "█" * width
    bar = "█" * filled
    if partial_idx > 0:
        bar += partials[partial_idx]
        remaining = width - filled - 1
    else:
        remaining = width - filled
    bar += "░" * max(0, remaining)
    return bar


def _format_size(b: float) -> str:
    if b >= 1024**3: return f"{b / 1024**3:.2f} GB"
    if b >= 1024**2: return f"{b / 1024**2:.1f} MB"
    if b >= 1024: return f"{b / 1024:.1f} KB"
    return f"{b:.0f} B"


def _format_time(s: float) -> str:
    if s < 0: return "∞"
    if s < 60: return f"{int(s)}s"
    if s < 3600: return f"{int(s // 60)}m {int(s % 60)}s"
    return f"{int(s // 3600)}h {int((s % 3600) // 60)}m"


def _format_speed(bps: float) -> str:
    if bps >= 1024**2: return f"{bps / 1024**2:.1f} MB/s"
    if bps >= 1024: return f"{bps / 1024:.1f} KB/s"
    return f"{bps:.0f} B/s"


def _calc_eta(pct: float, elapsed: float) -> str:
    if pct <= 0 or elapsed <= 0:
        return "calculating..."
    remaining = elapsed / pct * (100 - pct)
    return _format_time(remaining)


def _download_progress_text(title: str, quality: str, pct: float, size_bytes: float, elapsed: float, speed: float, eta: str = "") -> str:
    spin = _spinner()
    speed_str = _format_speed(speed) if speed > 0 else "⏳ starting..."
    bar = _progress_bar(pct)
    eta_str = eta or _calc_eta(pct, elapsed)

    lines = [
        f"{spin} <b>⬇️ Downloading</b>",
        f"",
        f"<b>{title}</b>",
        f"🎬 {quality}",
        f"",
        f"<code>{bar}</code> <b>{pct:.1f}%</b>",
        f"",
        f"📦 {_format_size(size_bytes)}  ⚡ {speed_str}",
        f"⏱ {_format_time(elapsed)}  ⏳ ETA: {eta_str}",
    ]
    return "\n".join(lines)


def _upload_progress_text(title: str, quality: str, current: float, total: float, speed: float = 0, elapsed: float = 0) -> str:
    pct = current / total * 100 if total > 0 else 0
    spin = _spinner()
    bar = _progress_bar(pct)
    speed_str = _format_speed(speed) if speed > 0 else "⏳ starting..."
    eta_str = _calc_eta(pct, elapsed) if pct > 0 and elapsed > 0 else "calculating..."

    lines = [
        f"{spin} <b>⬆️ Uploading to Telegram</b>",
        f"",
        f"<b>{title}</b>",
        f"🎬 {quality}",
        f"",
        f"<code>{bar}</code> <b>{pct:.1f}%</b>",
        f"",
        f"📦 {_format_size(current)} / {_format_size(total)}",
        f"⚡ {speed_str}  ⏳ ETA: {eta_str}",
    ]
    return "\n".join(lines)


def _done_text(title: str, quality: str, size_bytes: float, elapsed: float) -> str:
    bar = _progress_bar(100)
    avg_speed = size_bytes / elapsed if elapsed > 0 else 0
    return (
        f"✅ <b>Upload Complete!</b>\n\n"
        f"<b>{title}</b>\n"
        f"🎬 {quality}\n\n"
        f"<code>{bar}</code> <b>100%</b>\n\n"
        f"📦 {_format_size(size_bytes)}  ⚡ avg {_format_speed(avg_speed)}\n"
        f"⏱ Total: {_format_time(elapsed)}"
    )


async def _update_progress(msg: Message | None, text: str, last_edit: list[float], interval: float = 3.0):
    if not msg:
        return
    now = time.time()
    if now - last_edit[0] < interval:
        return
    last_edit[0] = now
    try:
        await msg.edit_text(text, parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass


def _get_origin(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if not domain:
        return "https://animedekho.app"
    if "megacloud" in domain or "rabbit" in domain or "dokicloud" in domain:
        return "https://megacloud.tv"
    elif "vmeas" in domain or "vidmoly" in domain or "vmbox" in domain or "vmpx" in domain:
        return "https://vidmoly.to"
    elif "as-cdn" in domain or "fireplayer" in domain:
        return f"https://{domain}"
    elif "turboviplay" in domain or "turbosplayer" in domain or "emturbovid" in domain:
        return "https://emturbovid.com"
    elif "xerver" in domain or "vidsrc" in domain or "googleusercontent" in domain:
        return "https://mirror.xerver.xyz"
    elif "animedrive" in domain or "hubcloud" in domain or "gamerxyt" in domain:
        return "https://hubcloud.ist"
    elif "toonflix" in domain or "workers.dev" in domain:
        return "https://drive.toonflix.in"
    return f"https://{domain}"


# ── Engine 1: Direct HTTP Download (MP4 / Direct Streams) ─────────────


async def direct_http_download(
    url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    quality: str = "auto",
    referer: str = "",
) -> bool:
    """Download direct video file (MP4/MKV) via chunked HTTP stream with progress."""
    log.info("Direct HTTP download: url=%s quality=%s", url[:120], quality)
    last_edit = [0.0]
    start_time = time.time()

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Referer": referer or _get_origin(url) + "/",
        "Accept": "*/*",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=2400, connect=30, sock_read=60)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status not in (200, 206):
                    log.warning("Direct HTTP download failed with status %d", resp.status)
                    return False

                ctype = resp.headers.get("Content-Type", "").lower()
                if any(bad in ctype for bad in ("text/html", "text/plain", "application/json")):
                    log.warning("Direct HTTP download rejected: URL returned non-media Content-Type '%s'", ctype)
                    return False

                total_bytes = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                last_bytes = 0
                last_time = time.time()

                with open(output_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(1024 * 1024):  # 1MB chunks
                        f.write(chunk)
                        downloaded += len(chunk)

                        now = time.time()
                        dt = now - last_time
                        if dt >= 1.0:
                            speed = (downloaded - last_bytes) / dt
                            last_bytes = downloaded
                            last_time = now
                            elapsed = now - start_time
                            pct = (downloaded / total_bytes * 100) if total_bytes > 0 else 0
                            if progress_msg and downloaded > 50_000:
                                await _update_progress(
                                    progress_msg,
                                    _download_progress_text(title, quality, pct, downloaded, elapsed, speed),
                                    last_edit,
                                    interval=3.0,
                                )

        success = os.path.exists(output_path) and os.path.getsize(output_path) > 50_000
        if success:
            log.info("Direct HTTP download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
        else:
            if os.path.exists(output_path):
                try: os.remove(output_path)
                except Exception: pass
        return success

    except Exception as e:
        log.warning("Direct HTTP download error: %s", e)
        if os.path.exists(output_path):
            try: os.remove(output_path)
            except Exception: pass
        return False


# ── Engine 2: N_m3u8DL-RE (Multi-audio HLS/DASH) ───────────────────────


async def n_m3u8dl_re_download(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    variant_url: str = "",
) -> bool:
    """Download using N_m3u8DL-RE — video + all audio tracks simultaneously."""
    if not shutil.which("N_m3u8DL-RE"):
        log.warning("N_m3u8DL-RE not found in PATH!")
        return False

    log.info("N_m3u8DL-RE download: url=%s quality=%s", stream_url[:120], quality)

    stem = Path(output_path).stem
    save_dir = str(Path(output_path).parent)
    origin = _get_origin(stream_url)

    job_id = uuid.uuid4().hex[:8]
    job_temp_dir = _TEMP_BASE / f"re_{stem}_{job_id}"
    job_temp_dir.mkdir(parents=True, exist_ok=True)

    height = quality.replace("p", "") if quality.endswith("p") and quality[:-1].isdigit() else ""

    # Attempt 1: Try with resolution selector if height is specified
    async def _run_dl(target_url: str, select_res: bool) -> bool:
        cmd = [
            "N_m3u8DL-RE", target_url,
            "--save-dir", save_dir, "--save-name", stem,
            "--tmp-dir", str(job_temp_dir),
            "--del-after-done",
            "--thread-count", "16",
            "--download-retry-count", "5",
            "--binary-merge",
            "--no-ansi-color",
            "--no-log",
            "-M", "format=mp4",
            "--select-audio", "all",
            "--select-subtitle", "all",
            "--header", f"Referer: {origin}/",
            "--header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        ]

        if select_res and height:
            cmd.extend(["--select-video", f"res=.*{height}.*:for=best"])
        else:
            cmd.extend(["--auto-select"])

        env = os.environ.copy()
        env["TERM"] = "xterm"
        env["DOTNET_SYSTEM_GLOBALIZATION_INVARIANT"] = "1"
        env["DOTNET_SYSTEM_CONSOLE_ALLOW_ANSI_COLOR_REDIRECTION"] = "1"
        env["COMPlus_EnableDiagnostics"] = "0"
        env["DOTNET_EnableDiagnostics"] = "0"

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        last_edit = [0.0]
        start_time = time.time()
        _STALL_TIMEOUT = 120
        _stall_detected = asyncio.Event()

        async def _monitor():
            last_size = 0
            last_time = time.time()
            last_change_time = time.time()
            while proc.returncode is None:
                await asyncio.sleep(3)
                try:
                    total = sum(
                        f.stat().st_size for f in Path(save_dir).glob(f"*{stem}*")
                        if f.is_file()
                    )
                    total += sum(f.stat().st_size for f in job_temp_dir.glob("**/*") if f.is_file())

                    now = time.time()
                    dt = now - last_time
                    speed = max(0, (total - last_size)) / dt if dt > 0 else 0

                    if total > last_size:
                        last_change_time = now
                    elif total == last_size and total > 0:
                        if now - last_change_time > _STALL_TIMEOUT:
                            log.warning("N_m3u8DL-RE stalled for %ds, killing", _STALL_TIMEOUT)
                            _stall_detected.set()
                            proc.kill()
                            break

                    last_size = total
                    last_time = now
                    elapsed = now - start_time

                    est_total = {
                        "1080p": 400, "720p": 200, "480p": 100, "360p": 60, "240p": 30
                    }.get(quality, 200) * 1024 * 1024
                    pct = min(95, total / est_total * 100) if est_total > 0 else 0

                    stall_info = ""
                    if speed == 0 and total > 0:
                        stall_secs = int(now - last_change_time)
                        if stall_secs > 10:
                            stall_info = f"\n⚠️ Stalled for {stall_secs}s..."

                    if progress_msg and total > 100_000:
                        await _update_progress(
                            progress_msg,
                            _download_progress_text(title, quality, pct, total, elapsed, speed) + stall_info,
                            last_edit,
                            interval=3.0,
                        )
                except Exception:
                    pass

        monitor_task = asyncio.create_task(_monitor())
        stdout_data, stderr_data = b"", b""
        try:
            stdout_data, stderr_data = await asyncio.wait_for(proc.communicate(), timeout=1800)
        except asyncio.TimeoutError:
            proc.kill()
            log.error("N_m3u8DL-RE timed out")
            return False
        finally:
            monitor_task.cancel()
            try:
                await monitor_task
            except asyncio.CancelledError:
                pass

        if _stall_detected.is_set():
            return False

        if proc.returncode != 0:
            err_msg = stderr_data.decode("utf-8", errors="ignore").strip() or stdout_data.decode("utf-8", errors="ignore").strip()
            log.warning("N_m3u8DL-RE exit code %d: %s", proc.returncode, err_msg[-500:])

        # Look for produced files
        if not os.path.exists(output_path):
            for ext in [".mp4", ".mkv", ".ts"]:
                alt = str(Path(save_dir) / f"{stem}{ext}")
                if os.path.exists(alt) and alt != output_path:
                    os.rename(alt, output_path)
                    break

        return os.path.exists(output_path) and os.path.getsize(output_path) > 0

    try:
        # Step 1: If variant_url is given and different from master stream_url, try auto-select on variant directly
        if variant_url and variant_url != stream_url:
            log.info("N_m3u8DL-RE downloading targeted variant directly: %s", variant_url[:80])
            success = await _run_dl(variant_url, select_res=False)
            if success:
                log.info("N_m3u8DL-RE variant download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
                return True

        # Step 2: Run on stream_url with resolution filter
        success = await _run_dl(stream_url, select_res=bool(height))
        if success:
            log.info("N_m3u8DL-RE download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
            return True

        # Step 3: Retry with auto-select on variant_url (or stream_url) if select_res failed
        target = variant_url or stream_url
        log.info("N_m3u8DL-RE retrying with --auto-select on %s", target[:80])
        success = await _run_dl(target, select_res=False)
        if success:
            log.info("N_m3u8DL-RE auto-select complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
            return True

        return False
    finally:
        shutil.rmtree(job_temp_dir, ignore_errors=True)


# ── Engine 3: FFmpeg Fallback (M3U8 / Media Streams) ───────────────────


async def ffmpeg_download(
    stream_url: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    quality: str = "auto",
    referer: str = "",
) -> bool:
    """Download stream using FFmpeg as a reliable universal fallback."""
    if not shutil.which("ffmpeg"):
        log.error("FFmpeg not found in PATH!")
        return False

    log.info("FFmpeg fallback download: url=%s quality=%s", stream_url[:120], quality)
    origin = referer or _get_origin(stream_url)

    cmd = ["ffmpeg", "-y"]
    headers = f"Referer: {origin}/\r\nUser-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
    cmd.extend(["-headers", headers])
    cmd.extend(["-i", stream_url, "-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-bsf:a", "aac_adtstoasc", output_path])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )

    last_edit = [0.0]
    start_time = time.time()

    async def _monitor():
        last_size = 0
        last_time = time.time()
        while proc.returncode is None:
            await asyncio.sleep(3)
            try:
                total = os.path.getsize(output_path) if os.path.exists(output_path) else 0
                now = time.time()
                dt = now - last_time
                speed = max(0, (total - last_size)) / dt if dt > 0 else 0
                last_size = total
                last_time = now
                elapsed = now - start_time

                est_total = {
                    "1080p": 400, "720p": 200, "480p": 100, "360p": 60, "240p": 30
                }.get(quality, 200) * 1024 * 1024
                pct = min(95, total / est_total * 100) if est_total > 0 else 0

                if progress_msg and total > 100_000:
                    await _update_progress(
                        progress_msg,
                        _download_progress_text(title, quality, pct, total, elapsed, speed),
                        last_edit,
                        interval=3.0,
                    )
            except Exception:
                pass

    monitor_task = asyncio.create_task(_monitor())
    try:
        await asyncio.wait_for(proc.wait(), timeout=1800)
    except asyncio.TimeoutError:
        proc.kill()
        log.error("FFmpeg timed out for %s", stream_url[:60])
        return False
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass

    success = os.path.exists(output_path) and os.path.getsize(output_path) > 0
    if success:
        log.info("FFmpeg download complete: %s (%s)", output_path, _format_size(os.path.getsize(output_path)))
    return success


# ── Unified Media Downloader ──────────────────────────────────────────


async def download_media(
    stream_url: str,
    quality: str,
    output_path: str,
    progress_msg: Message | None = None,
    title: str = "video",
    variant_url: str = "",
    referer: str = "",
) -> bool:
    """
    Unified multi-engine downloader:
    1. If URL is MP4 / direct file: use direct HTTP stream download.
    2. If URL is M3U8: try N_m3u8DL-RE.
    3. If N_m3u8DL-RE fails or is unavailable: fallback to FFmpeg.
    """
    is_mp4 = (
        ".mp4" in stream_url.lower() or
        ".mkv" in stream_url.lower() or
        "googleusercontent" in stream_url or
        "instant_dl" in stream_url or
        (".m3u8" not in stream_url.lower() and ".m3u8" not in variant_url.lower())
    )

    if is_mp4:
        log.info("Detected direct MP4/file URL, using direct HTTP downloader")
        ok = await direct_http_download(stream_url, output_path, progress_msg, title, quality, referer=referer)
        if ok:
            return True
        log.warning("Direct HTTP download failed, falling back to FFmpeg")
        return await ffmpeg_download(stream_url, output_path, progress_msg, title, quality, referer=referer)

    # M3U8 stream
    if shutil.which("N_m3u8DL-RE"):
        ok = await n_m3u8dl_re_download(stream_url, quality, output_path, progress_msg, title, variant_url=variant_url)
        if ok:
            return True
        log.warning("N_m3u8DL-RE failed for %s, falling back to FFmpeg", stream_url[:60])

    # Fallback to FFmpeg on stream_url or variant_url
    target_url = variant_url or stream_url
    ok = await ffmpeg_download(target_url, output_path, progress_msg, title, quality, referer=referer)
    if not ok and variant_url and variant_url != stream_url:
        ok = await ffmpeg_download(stream_url, output_path, progress_msg, title, quality, referer=referer)

    return ok


_GENRE_EMOJIS = {
    "Action": "👊",
    "Adventure": "🗺",
    "Fantasy": "🌓",
    "Drama": "🎭",
    "Comedy": "😂",
    "Romance": "❤️",
    "Sci-Fi": "🚀",
    "Supernatural": "⚔️",
    "Mystery": "🔎",
    "Suspense": "😱",
    "Horror": "👻",
    "Slice of Life": "🍃",
    "Sports": "⚽",
    "Thriller": "⚡",
    "Psychological": "🧠",
    "Mecha": "🤖",
    "Music": "🎵",
}


async def _build_episode_caption_and_markup(
    title: str,
    quality: str,
    series_slug: str,
    client: Client,
    target_chat: int,
) -> tuple[str, InlineKeyboardMarkup | None]:
    import html as htmlmod
    from bot.database import db
    ep_style = await db.get_ep_style() if db else "classic"
    if ep_style != "modern":
        return f"📺 {title} [{quality}]", None

    # Parse Series Title, Season, Episode
    m = re.match(r"^(.*?)\s+[Ss](\d+)[Ee](\d+)", title)
    if m:
        s_title = m.group(1).strip()
        season_num = int(m.group(2))
        ep_num = int(m.group(3))
    else:
        s_title = title.split(" S")[0].strip() if " S" in title else title
        season_num = 1
        ep_num = 1

    # Fetch AniList metadata for genres, status, total episodes
    meta = None
    try:
        from utils.anilist import get_anilist_metadata
        meta = await get_anilist_metadata(s_title)
    except Exception:
        pass

    status = (meta.get("status") if meta else None) or "RELEASING"
    total_eps = (meta.get("episodes") if meta else None) or 12
    raw_genres = (meta.get("genres") if meta else None) or ["Action", "Adventure", "Fantasy"]

    formatted_genres = []
    for g in raw_genres[:3]:
        emoji = _GENRE_EMOJIS.get(g, "✨")
        clean_tag = re.sub(r'[^a-zA-Z0-9]', '', g)
        formatted_genres.append(f"{emoji} #{clean_tag}")
    genres_str = ", ".join(formatted_genres) or "✨ #Anime"

    audio_str = "Multi Audio [ESub]"

    bot_me = getattr(client, "me", None)
    bname = bot_me.username if bot_me and bot_me.username else "animedekho"

    caption = (
        f"✦ <b>{htmlmod.escape(s_title)}</b> ✦\n"
        f"Season {season_num:02d} • Episode {ep_num:02d}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⬡ <b>Audio:</b> {audio_str}\n"
        f"⬡ <b>Status:</b> {status}\n"
        f"⬡ <b>Total Episodes:</b> {total_eps}\n\n"
        f"✦ <b>Genres:</b> {genres_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✦ <b>Powered By:</b> @{bname}"
    )

    slug = series_slug or re.sub(r'[^a-zA-Z0-9]+', '-', s_title).strip('-').lower()
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("480P ↗", url=f"https://t.me/{bname}?start=get_{slug}_480p_S{season_num}E{ep_num:02d}"),
            InlineKeyboardButton("720P ↗", url=f"https://t.me/{bname}?start=get_{slug}_720p_S{season_num}E{ep_num:02d}"),
        ],
        [
            InlineKeyboardButton("1080P ↗", url=f"https://t.me/{bname}?start=get_{slug}_1080p_S{season_num}E{ep_num:02d}"),
            InlineKeyboardButton("HDRip ↗", url=f"https://t.me/{bname}?start=get_{slug}_auto_S{season_num}E{ep_num:02d}"),
        ]
    ])
    return caption, markup


# ── Main download + upload ────────────────────────────────────────────


async def download_and_upload(
    chat_id: int,
    stream_url: str,
    quality: str,
    filename: str,
    title: str,
    progress_msg: Message,
    client: Client,
    variant_url: str = "",
    referer: str = "",
    poster_url: str = "",
    destination_channel_id: int | None = None,
    series_slug: str = "",
    language: str = "",
) -> tuple[bool, Message | None]:
    """Download video + upload via Pyrogram MTProto with progress, custom thumbnail, and dump channel."""
    output_path = str(_TEMP_BASE / filename)
    overall_start = time.time()
    thumb_path = None
    custom_thumb_path = None

    try:
        # 1. Custom Thumbnail System (Point 5 - OFF by default, falls back to AniList poster)
        from bot.database import db
        if db:
            try:
                # Detect language if not specified
                if not language and title:
                    t_low = title.lower()
                    for l_candidate in ("hindi", "tamil", "telugu", "multi", "english"):
                        if l_candidate in t_low:
                            language = l_candidate
                            break
                custom_thumb_id = await db.get_custom_thumbnail(series_slug=series_slug, language=language)
                if custom_thumb_id:
                    c_path = str(_TEMP_BASE / f"thumb_{int(time.time())}_{uuid.uuid4().hex[:6]}.jpg")
                    dl_res = await client.download_media(custom_thumb_id, file_name=c_path)
                    if dl_res and os.path.exists(str(dl_res)):
                        thumb_path = str(dl_res)
                        custom_thumb_path = thumb_path
                        log.info("Using custom thumbnail for %s (type: %s)", title, language or series_slug or "global")
            except Exception as cte:
                log.debug("Custom thumbnail check failed: %s", cte)

        # 2. Poster Fallback (existing behavior)
        if not thumb_path:
            if not poster_url:
                try:
                    from utils.anilist import resolve_best_poster
                    poster_url = await resolve_best_poster(title, "")
                except Exception:
                    pass

            if poster_url:
                try:
                    from bot.library import _download_poster
                    thumb_path = await _download_poster(poster_url)
                except Exception as pe:
                    log.debug("Poster thumbnail download failed: %s", pe)

        success = await download_media(
            stream_url, quality, output_path, progress_msg, title, variant_url=variant_url, referer=referer
        )

        if not success:
            from bot.database import db
            if db:
                try:
                    await db.log_download_failure(
                        title=title, quality=quality, source=referer or "stream",
                        error="Could not download from media server", user_id=chat_id,
                    )
                except Exception:
                    pass
            await progress_msg.edit_text(
                f"❌ <b>Download Failed</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 Could not download from server",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            from bot.database import db
            if db:
                try:
                    await db.log_download_failure(
                        title=title, quality=quality, source=referer or "stream",
                        error="File is empty (0 bytes)", user_id=chat_id,
                    )
                except Exception:
                    pass
            await progress_msg.edit_text(
                f"❌ <b>Download Failed</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 File is empty",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        file_size = os.path.getsize(output_path)

        if file_size > TG_UPLOAD_LIMIT:
            from bot.database import db
            if db:
                try:
                    await db.log_download_failure(
                        title=title, quality=quality, source=referer or "stream",
                        error=f"File exceeds Telegram 2GB limit ({_format_size(file_size)})", user_id=chat_id,
                    )
                except Exception:
                    pass
            await progress_msg.edit_text(
                f"⚠️ <b>File Too Large</b>\n"
                f"┌ 📺 {title}\n"
                f"├ 💾 {_format_size(file_size)} (max 2 GB)\n"
                f"└ 💡 Try a lower quality",
                parse_mode=enums.ParseMode.HTML)
            return False, None

        # Upload to Telegram
        upload_last_edit = [0.0]
        upload_start = [0.0]
        upload_last_bytes = [0]
        upload_last_time = [0.0]
        upload_speed = [0.0]

        async def _upload_progress(current: int, total: int):
            if upload_start[0] == 0:
                upload_start[0] = time.time()
                upload_last_time[0] = time.time()
            now = time.time()
            dt = now - upload_last_time[0]
            if dt > 0.5:
                upload_speed[0] = (current - upload_last_bytes[0]) / dt
                upload_last_bytes[0] = current
                upload_last_time[0] = now
            await _update_progress(
                progress_msg,
                _upload_progress_text(title, quality, current, total, upload_speed[0], time.time() - upload_start[0]),
                upload_last_edit,
                interval=3.0,
            )

        target_upload_chat = destination_channel_id or chat_id
        await progress_msg.edit_text(
            f"📤 <b>Uploading to Telegram</b>\n"
            f"┌ 📺 {title}\n"
            f"├ 🎬 Quality: {quality}\n"
            f"├ 💾 Size: {_format_size(file_size)}\n"
            f"└ 🔄 Starting upload...",
            parse_mode=enums.ParseMode.HTML)

        # Dump / Storage Channel (Point 4 - OFF by default unless configured)
        from bot.database import db
        dump_channel_id = await db.get_dump_channel() if db else None

        # Build style-aware episode caption and quality buttons (Default: classic)
        caption_text, markup_obj = await _build_episode_caption_and_markup(
            title=title, quality=quality, series_slug=series_slug, client=client, target_chat=target_upload_chat
        )

        sent_msg = None
        if dump_channel_id and target_upload_chat != dump_channel_id:
            try:
                await progress_msg.edit_text(
                    f"📤 <b>Uploading to Dump Channel</b>\n"
                    f"┌ 📺 {title}\n"
                    f"├ 🎬 Quality: {quality}\n"
                    f"├ 💾 Size: {_format_size(file_size)}\n"
                    f"└ 🔄 Storing media in cache...",
                    parse_mode=enums.ParseMode.HTML)
                dump_msg = await client.send_document(
                    chat_id=dump_channel_id,
                    document=output_path,
                    thumb=thumb_path,
                    file_name=filename,
                    caption=f"📺 {title} [{quality}] #dump",
                    progress=_upload_progress,
                )
                if dump_msg:
                    fid = dump_msg.video.file_id if dump_msg.video else (dump_msg.document.file_id if dump_msg.document else None)
                    if fid:
                        sent_msg = await client.send_document(
                            chat_id=target_upload_chat,
                            document=fid,
                            caption=caption_text,
                            reply_markup=markup_obj,
                        )
            except Exception as de:
                log.warning("Dump channel upload failed, falling back to direct upload: %s", de)

        if not sent_msg:
            try:
                sent_msg = await client.send_document(
                    chat_id=target_upload_chat,
                    document=output_path,
                    thumb=thumb_path,
                    file_name=filename,
                    caption=caption_text,
                    reply_markup=markup_obj,
                    progress=_upload_progress,
                )
            except Exception as te:
                if thumb_path:
                    log.warning("Upload with thumb failed, retrying without thumb: %s", te)
                    sent_msg = await client.send_document(
                        chat_id=target_upload_chat,
                        document=output_path,
                        file_name=filename,
                        caption=caption_text,
                        reply_markup=markup_obj,
                        progress=_upload_progress,
                    )
                else:
                    raise

        user_file_msg = None
        # If uploaded to dedicated channel and chat_id is user PM, send file to user via file_id
        if destination_channel_id and chat_id != destination_channel_id:
            try:
                fid = sent_msg.video.file_id if sent_msg.video else (sent_msg.document.file_id if sent_msg.document else None)
                if fid:
                    user_file_msg = await client.send_document(
                        chat_id=chat_id,
                        document=fid,
                        caption=f"📺 {title} [{quality}]",
                    )
            except Exception as ue:
                log.warning("Forward/send to user chat %d failed: %s", chat_id, ue)
        elif not destination_channel_id and chat_id > 0:
            user_file_msg = sent_msg

        # Auto-delete scheduling if delivered in user PM
        if user_file_msg and chat_id > 0:
            try:
                from bot.auto_delete import auto_delete_service
                bot_user = getattr(client, "me", None)
                bname = bot_user.username if bot_user else ""
                get_link = f"https://t.me/{bname}?start=help" if bname else ""
                await auto_delete_service.schedule_deletion(
                    client=client,
                    chat_id=chat_id,
                    message_id=user_file_msg.id,
                    get_file_link=get_link,
                    file_title=title,
                )
            except Exception as ade:
                log.debug("Auto-delete scheduling in downloader failed: %s", ade)

        total_time = time.time() - overall_start
        await progress_msg.edit_text(
            _done_text(title, quality, file_size, total_time),
            parse_mode=enums.ParseMode.HTML)
        return True, sent_msg

    except Exception as e:
        log.exception("Download/upload error for %s", title)
        from bot.database import db
        if db:
            try:
                await db.log_download_failure(
                    title=title, quality=quality, source=referer or "stream",
                    error=str(e), user_id=chat_id,
                )
            except Exception:
                pass
        try:
            await progress_msg.edit_text(
                f"❌ <b>Error</b>\n"
                f"┌ 📺 {title}\n"
                f"└ 💔 {str(e)[:200]}",
                parse_mode=enums.ParseMode.HTML)
        except Exception:
            pass
        return False, None
    finally:
        try:
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)
        except Exception:
            pass
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
            stem = Path(output_path).stem
            for f in _TEMP_BASE.glob(f"*{stem}*"):
                if f.is_file():
                    f.unlink(missing_ok=True)
                elif f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
        except Exception:
            pass
