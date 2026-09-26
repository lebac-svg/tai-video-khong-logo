#!/usr/bin/env python3
"""Tải video không logo — máy chủ cục bộ, lõi yt-dlp.

Chạy:   python app.py                (tự mở trình duyệt)
        python app.py --no-browser --port 8765
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yt_dlp
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

APP_VERSION = "1.1.2"
APP_NAME = "Tải video không logo"
IS_WIN = platform.system() == "Windows"
FROZEN = bool(getattr(sys, "frozen", False))  # đang chạy từ bản .exe đóng gói
# Bản .exe: thư mục chứa exe giữ ffmpeg.exe đi kèm; giao diện nằm trong gói giải nén tạm.
APP_DIR = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
STATIC_DIR = BUNDLE_DIR / "static"
# TAIVIDEO_DATA_DIR: chỉ định thư mục dữ liệu riêng (dùng khi chạy thử, để không đụng cài đặt thật).
_DATA_OVERRIDE = os.environ.get("TAIVIDEO_DATA_DIR")
DATA_DIR = Path(_DATA_OVERRIDE) if _DATA_OVERRIDE else (
    (Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "TaiVideo") if IS_WIN else (Path.home() / ".tai-video"))
# Bản .exe lưu cài đặt theo người dùng nên giữ nguyên khi thay bản mới; bản mã nguồn lưu cạnh app.py.
SETTINGS_FILE = (DATA_DIR / "settings.json") if (FROZEN or _DATA_OVERRIDE) else (APP_DIR / "settings.json")
# Bản .exe 1.1.0–1.1.1 lưu cài đặt cạnh exe: đọc lại nếu chưa có file ở chỗ mới.
LEGACY_SETTINGS_FILES = [APP_DIR / "settings.json"] if (FROZEN and not _DATA_OVERRIDE) else []
LOG_FILE = DATA_DIR / "app.log"
DEFAULT_DOWNLOAD_DIR = Path.home() / "Downloads" / "TaiVideo"
# Nơi app hỏi bản mới: file latest.json đính kèm bản phát hành mới nhất trên GitHub Releases.
DEFAULT_UPDATE_URL = "https://github.com/lebac-svg/tai-video-khong-logo/releases/latest/download/latest.json"
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")


# ----------------------------------------------------------------- cài đặt --
class Settings(BaseModel):
    download_dir: str = str(DEFAULT_DOWNLOAD_DIR)
    cookies_mode: str = "none"        # none | browser | file
    cookies_browser: str = "firefox"  # firefox | chrome | edge | brave | opera | vivaldi
    cookies_file: str = ""
    convert_hevc: bool = True         # chuyển H.265 → H.264 sau khi tải để mở được trên mọi máy
    update_url: str = DEFAULT_UPDATE_URL


def load_settings() -> Settings:
    for f in (SETTINGS_FILE, *LEGACY_SETTINGS_FILES):
        try:
            return Settings(**json.loads(f.read_text("utf-8")))
        except Exception:  # noqa: BLE001
            continue
    return Settings()


def save_settings(s: Settings) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s.model_dump(), ensure_ascii=False, indent=2), "utf-8")


SETTINGS = load_settings()


def app_log(msg: str) -> None:
    """Ghi một dòng vào nhật ký (bản .exe: %LOCALAPPDATA%\\TaiVideo\\app.log) để tra lỗi về sau."""
    try:
        print(f"[{time.strftime('%d/%m %H:%M:%S')}] {msg}", flush=True)
    except Exception:  # noqa: BLE001
        pass


def find_tool(name: str) -> str | None:
    """Tìm ffmpeg/ffprobe. Bản .exe ưu tiên file đi kèm cạnh TaiVideo.exe, rồi mới tới PATH.
    Luôn trả đường dẫn tuyệt đối: đường dẫn tương đối như .\\ffmpeg.exe hỏng ngay khi thư mục làm việc đổi."""
    exe = f"{name}.exe" if IS_WIN else name
    bundled = [APP_DIR / exe, APP_DIR / "ffmpeg" / "bin" / exe, APP_DIR / "ffmpeg" / exe]
    on_path = shutil.which(name)
    for cand in ([*bundled, on_path] if FROZEN else [on_path, *bundled]):
        if cand and Path(cand).is_file():
            return str(Path(cand).resolve())
    return None


def find_ffmpeg() -> str | None:
    return find_tool("ffmpeg")


def find_ffprobe() -> str | None:
    exe = "ffprobe.exe" if IS_WIN else "ffprobe"
    if FFMPEG and Path(FFMPEG).with_name(exe).is_file():  # ưu tiên ffprobe cùng bộ với ffmpeg đang dùng
        return str(Path(FFMPEG).with_name(exe))
    return find_tool("ffprobe")


FFMPEG = find_ffmpeg()
FFPROBE = find_ffprobe()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
HEVC_NAMES = ("hevc", "h265", "hvc1", "hev1")


def ensure_ffmpeg() -> str | None:
    """Kiểm tra lại ffmpeg ngay trước mỗi lần dùng: file có thể đã bị xoá hoặc di chuyển trong lúc app mở."""
    global FFMPEG, FFPROBE
    if not (FFMPEG and Path(FFMPEG).is_file()):
        old = FFMPEG
        FFMPEG = find_ffmpeg()
        FFPROBE = find_ffprobe()
        app_log(f"ffmpeg {old} không còn, chuyển sang: {FFMPEG or 'KHÔNG CÓ'}")
    elif FFPROBE and not Path(FFPROBE).is_file():
        FFPROBE = find_ffprobe()
    return FFMPEG


def probe_video(path: str) -> dict:
    """{'vcodec': 'hevc', 'duration': 12.3} bằng ffprobe, hoặc đọc từ 'ffmpeg -i' nếu không có ffprobe."""
    try:
        if FFPROBE:
            out = subprocess.run(
                [FFPROBE, "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name:format=duration", "-of", "json", path],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, creationflags=NO_WINDOW)
            data = json.loads(out.stdout or "{}")
            streams = data.get("streams") or []
            duration = float((data.get("format") or {}).get("duration") or 0)
            return {"vcodec": streams[0].get("codec_name") if streams else None, "duration": duration or None}
        if FFMPEG:
            err = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True,
                                 encoding="utf-8", errors="replace", timeout=60, creationflags=NO_WINDOW).stderr
            v = re.search(r"Video: (\w+)", err)
            d = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
            duration = (int(d.group(1)) * 3600 + int(d.group(2)) * 60 + float(d.group(3))) if d else None
            return {"vcodec": v.group(1) if v else None, "duration": duration}
    except Exception:  # noqa: BLE001
        pass
    return {}


def is_hevc(vcodec: str | None) -> bool:
    return (vcodec or "").lower().startswith(HEVC_NAMES)


class ConversionError(RuntimeError):
    """Chuyển mã thất bại; file gốc vẫn được giữ nguyên."""


def run_ffmpeg_progress(cmd: list, duration: float, on_progress=None, should_cancel=None) -> tuple[int, str]:
    """Chạy ffmpeg và đọc tiến độ từ '-progress pipe:1'. stderr gộp vào stdout nên ống dẫn không bao giờ bị đầy
    (tránh treo khi ffmpeg in nhiều cảnh báo). Trả về (mã thoát, các dòng lỗi cuối)."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    errors: list[str] = []
    try:
        for raw in proc.stdout:
            if should_cancel and should_cancel():
                proc.kill()
                raise yt_dlp.utils.DownloadCancelled("Đã huỷ")
            line = raw.strip()
            if line.startswith("out_time_us=") and duration and on_progress:
                try:
                    on_progress(min(100.0, int(line.split("=", 1)[1]) / 1_000_000 / duration * 100))
                except ValueError:
                    pass
            elif line and "=" not in line.split(" ", 1)[0]:  # dòng tiến độ có dạng khoá=giá trị
                errors.append(line)
                del errors[:-6]
        proc.wait()
    except BaseException:
        if proc.poll() is None:
            proc.kill()
        raise
    return proc.returncode, " | ".join(errors)


def convert_to_h264(path: str, on_progress=None, should_cancel=None) -> str:
    """Chuyển video H.265 sang H.264 (giữ nguyên tiếng), thay file gốc. Trả về đường dẫn mới.
    Thất bại thì file gốc còn nguyên và báo ConversionError."""
    ffmpeg = ensure_ffmpeg()
    if not ffmpeg:
        raise ConversionError("không tìm thấy ffmpeg")
    src = Path(path)
    tmp = src.with_name(src.stem + ".h264.tmp.mp4")
    duration = probe_video(path).get("duration") or 0
    video = ["-map", "0:v:0", "-map", "0:a:0?",
             "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",  # libx264 với yuv420p cần kích thước chẵn
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    audio_choices = [["-c:a", "aac", "-b:a", "192k"]]
    if src.suffix.lower() in (".mp4", ".mov", ".m4v"):
        audio_choices.insert(0, ["-c:a", "copy"])  # giữ nguyên tiếng; không được thì mã hoá lại AAC
    last_error = ""
    for audio in audio_choices:
        cmd = [ffmpeg, "-y", "-v", "error", "-nostdin", "-i", str(src), *video, *audio,
               "-movflags", "+faststart", "-progress", "pipe:1", str(tmp)]
        try:
            code, last_error = run_ffmpeg_progress(cmd, duration, on_progress, should_cancel)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        if code == 0 and tmp.is_file() and tmp.stat().st_size > 0:
            dst = src.with_suffix(".mp4")
            src.unlink()
            tmp.replace(dst)
            return str(dst)
        tmp.unlink(missing_ok=True)
    raise ConversionError(last_error or "ffmpeg báo lỗi không rõ")


# ------------------------------------------------------- chọn định dạng --
# yt-dlp gắn format_note chứa "watermarked" cho bản có logo (TikTok, Douyin).
NO_WM = "[format_note!*=watermark]"

PRESETS = {
    "best": {"label": "Tốt nhất", "height": None},
    "1080": {"label": "1080p", "height": 1080},
    "720": {"label": "720p", "height": 720},
    "480": {"label": "480p", "height": 480},
    "audio": {"label": "Chỉ âm thanh (MP3)", "height": None},
}

# Trong cùng độ phân giải, ưu tiên h264 + aac để file mp4 chạy được ở mọi nơi.
FORMAT_SORT = ["hasvid", "ie_pref", "lang", "quality", "res", "fps", "hdr:12",
               "vcodec:h264", "channels", "acodec:aac", "size", "br", "asr",
               "proto", "ext", "hasaud", "source", "id"]


COMBINED_FIRST_HOSTS = ("tiktok.com", "douyin.com", "iesdouyin.com")


def prefers_combined(url: str) -> bool:
    """TikTok/Douyin: luôn lấy file có sẵn tiếng, không ghép video rời với nhạc nền
    (nhạc nền có thể khác tiếng thật của video)."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in COMBINED_FIRST_HOSTS)


def preset_spec(preset: str, can_merge: bool = True, combined_first: bool = False) -> str:
    """Chuỗi chọn định dạng cho yt-dlp: luôn thử bản không logo trước.
    Giới hạn độ phân giải nằm ở sort_fields() (res:N), không ở bộ lọc."""
    if preset == "audio":
        return "ba/b"
    combined = f"b{NO_WM}/b"
    if not can_merge:  # không có ffmpeg: chỉ lấy file đã ghép sẵn
        return combined
    merged = f"bv*{NO_WM}+ba/b{NO_WM}/bv*+ba/b"
    return f"{combined}/{merged}" if combined_first else merged


def sort_fields(preset: str) -> list[str]:
    """Thứ tự ưu tiên định dạng cho từng mức chất lượng.
    res:N = cạnh ngắn của khung hình tối đa N (đúng cho cả video dọc lẫn ngang);
    đặt trước 'quality' để giới hạn có hiệu lực."""
    h = PRESETS[preset]["height"]
    if not h:
        return FORMAT_SORT
    fields = [f for f in FORMAT_SORT if f != "res"]
    fields.insert(fields.index("quality"), f"res:{h}")
    return fields


def res_of(fmt: dict) -> int | None:
    """Cạnh ngắn của khung hình (720 cho cả 1280x720 lẫn 720x1280)."""
    for p in parts_of(fmt):
        if p.get("vcodec") != "none":
            dims = [d for d in (p.get("width"), p.get("height")) if d]
            if dims:
                return int(min(dims))
    return None


class QuietLogger:
    def __init__(self, sink=None):
        self.sink = sink

    def _emit(self, prefix, msg):
        if self.sink:
            self.sink(f"{prefix}{msg}"[:400])

    def debug(self, msg):
        if not msg.startswith("[debug] "):
            self._emit("", msg)

    def info(self, msg):
        self._emit("", msg)

    def warning(self, msg):
        self._emit("⚠ ", msg)

    def error(self, msg):
        self._emit("✖ ", msg)


def base_opts(sink=None) -> dict:
    s = SETTINGS
    ensure_ffmpeg()
    opts = {
        "quiet": True,
        "noprogress": True,
        "logger": QuietLogger(sink),
        "noplaylist": True,
        "windowsfilenames": True,
        "retries": 10,            # mạng chập chờn: thử lại và tải tiếp từ chỗ dở
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "socket_timeout": 20,
        "format_sort": FORMAT_SORT,
        # YouTube cần chạy JS để mở khoá đủ định dạng: dùng Node hoặc Deno nếu máy có.
        "js_runtimes": {"node": {}, "deno": {}},
    }
    if FFMPEG:
        opts["ffmpeg_location"] = FFMPEG
    if s.cookies_mode == "file" and s.cookies_file:
        opts["cookiefile"] = s.cookies_file
    elif s.cookies_mode == "browser" and s.cookies_browser:
        opts["cookiesfrombrowser"] = (s.cookies_browser,)
    return opts


# ------------------------------------------------------------ tiện ích --
SITE_NAMES = {
    "tiktok": "TikTok", "douyin": "Douyin", "youtube": "YouTube",
    "facebook": "Facebook", "instagram": "Instagram", "twitter": "X (Twitter)",
    "threads": "Threads", "reddit": "Reddit", "pinterest": "Pinterest",
    "bilibili": "Bilibili", "vimeo": "Vimeo", "dailymotion": "Dailymotion",
    "twitch": "Twitch", "kuaishou": "Kuaishou", "xiaohongshu": "Xiaohongshu",
    "likee": "Likee", "snapchat": "Snapchat", "vk": "VK", "weibo": "Weibo",
    "linkedin": "LinkedIn", "generic": "Trang web",
}
# Các trang phát file gốc không chèn logo (ngoài TikTok/Douyin có cờ riêng).
KNOWN_CLEAN = ("tiktok", "douyin", "youtube", "facebook", "instagram", "twitter",
               "threads", "reddit", "pinterest", "vimeo", "dailymotion", "twitch",
               "bilibili", "snapchat", "vk", "linkedin")


def extract_first_url(text: str) -> str | None:
    text = (text or "").strip()
    m = URL_RE.search(text)
    if m:
        return m.group(0).rstrip(".,;)]")
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/\S*)?$", text, re.I):
        return "https://" + text
    return None


DOUYIN_ID_RE = re.compile(r"/(?:share/)?video/(\d{15,})")


def normalize_url(url: str) -> str:
    """Đưa link Douyin về dạng douyin.com/video/<id> mà yt-dlp nhận diện.
    Link rút gọn v.douyin.com đi theo chuyển hướng công khai; link chia sẻ iesdouyin lấy id."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == "v.douyin.com":
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                url = r.geturl()
        except Exception:  # noqa: BLE001
            return url
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host.endswith("douyin.com"):
        m = DOUYIN_ID_RE.search(urllib.parse.urlparse(url).path)
        if m:
            return f"https://www.douyin.com/video/{m.group(1)}"
    return url


def site_name(info: dict) -> str:
    key = (info.get("extractor_key") or info.get("extractor") or "").lower()
    for k, name in SITE_NAMES.items():
        if key.startswith(k):
            return name
    return info.get("extractor_key") or "Trang web"


def first_thumb(info: dict) -> str:
    if info.get("thumbnail"):
        return info["thumbnail"]
    thumbs = [t for t in (info.get("thumbnails") or []) if t.get("url")]
    if thumbs:
        thumbs.sort(key=lambda t: (t.get("preference") or 0, t.get("width") or 0))
        return thumbs[-1]["url"]
    return ""


def parts_of(fmt: dict) -> list:
    return fmt.get("requested_formats") or [fmt]


def size_of(fmt: dict) -> int | None:
    total = 0
    for p in parts_of(fmt):
        n = p.get("filesize") or p.get("filesize_approx")
        if not n:
            return None
        total += int(n)
    return total


def is_watermarked(fmt: dict) -> bool:
    return any("watermark" in (p.get("format_note") or "").lower() for p in parts_of(fmt))


def merged_ext(fmt: dict) -> str:
    """Đuôi file sau khi ghép, theo đúng cách yt-dlp chọn với merge_output_format mp4/mkv."""
    parts = parts_of(fmt)
    if len(parts) == 1:
        return parts[0].get("ext") or ""
    v = [p for p in parts if p.get("vcodec") != "none"]
    a = [p for p in parts if p.get("acodec") != "none"]
    try:
        from yt_dlp.utils import get_compatible_ext
        return get_compatible_ext(
            vcodecs=[p.get("vcodec") for p in v], acodecs=[p.get("acodec") for p in a],
            vexts=[p.get("ext") for p in v], aexts=[p.get("ext") for p in a],
            preferences=["mp4", "mkv"]) or "mkv"
    except Exception:  # noqa: BLE001
        return "mkv"


def fmt_size(n: int | None) -> str:
    if not n:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return (f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}").replace(".", ",")
        n /= 1024
    return ""


def choose(ydl: yt_dlp.YoutubeDL, info: dict, spec: str, fields: list[str] | None = None) -> dict | None:
    """Chạy bộ chọn định dạng của yt-dlp trên info đã lấy, không tải gì."""
    formats = copy.deepcopy(info.get("formats") or [])
    if not formats:
        return None
    try:
        if fields is not None:
            from yt_dlp.utils import FormatSorter
            saved = ydl.params.get("format_sort")
            ydl.params["format_sort"] = fields
            try:
                sorter = FormatSorter(ydl, info.get("_format_sort_fields") or [])
            finally:
                ydl.params["format_sort"] = saved
            formats.sort(key=sorter.calculate_preference)
        selector = ydl.build_format_selector(spec)
        chosen = list(selector({
            "formats": formats,
            "has_merged_format": any("none" not in (f.get("acodec"), f.get("vcodec")) for f in formats),
            "incomplete_formats": (all(f.get("vcodec") == "none" for f in formats)
                                   or all(f.get("acodec") == "none" for f in formats)),
        }))
    except Exception:
        return None
    return chosen[0] if chosen else None


MSG_NO_FFMPEG = ("Không tìm thấy ffmpeg (cần để ghép hình với tiếng). Với bản .exe: giữ ffmpeg.exe cùng thư mục "
                 "với TaiVideo.exe (giải nén lại đầy đủ gói app) rồi mở lại app.")
MSG_NETWORK = "Kết nối bị ngắt giữa chừng. Bấm Thử lại: app tải tiếp từ chỗ đang dở."

ERROR_RULES = [
    ("fresh cookies", "Douyin chỉ phát cho trình duyệt thật: mở douyin.com trong Firefox một lần (không cần đăng nhập), "
                      "rồi vào Cài đặt → Cookies → \"Lấy từ trình duyệt: Firefox\" và thử lại."),
    ("unsupported url", "Trang này chưa được hỗ trợ."),
    ("is not a valid url", "Link không hợp lệ."),
    ("sign in to confirm", "YouTube yêu cầu xác minh không phải bot. Thêm cookies trình duyệt trong Cài đặt rồi thử lại."),
    ("login required", "Video này cần đăng nhập. Thêm cookies trong Cài đặt rồi thử lại."),
    ("log in", "Video này cần đăng nhập. Thêm cookies trong Cài đặt rồi thử lại."),
    ("logged in", "Video này cần đăng nhập. Thêm cookies trong Cài đặt rồi thử lại."),
    ("use --cookies", "Trang yêu cầu đăng nhập. Thêm cookies trong Cài đặt rồi thử lại."),
    ("rate-limit", "Trang tạm chặn vì tải quá nhiều. Đợi vài phút hoặc thêm cookies."),
    ("rate limit", "Trang tạm chặn vì tải quá nhiều. Đợi vài phút hoặc thêm cookies."),
    ("private video", "Video ở chế độ riêng tư."),
    ("is private", "Video ở chế độ riêng tư."),
    ("age-restricted", "Video giới hạn độ tuổi, cần cookies đăng nhập."),
    ("video unavailable", "Video không tồn tại hoặc đã bị xoá."),
    ("is unavailable", "Video không tồn tại hoặc đã bị xoá."),
    ("has been removed", "Video không tồn tại hoặc đã bị xoá."),
    ("does not exist", "Video không tồn tại hoặc đã bị xoá."),
    ("not available", "Video không xem được ở khu vực này hoặc đã bị xoá."),
    ("http error 404", "Video không tồn tại hoặc đã bị xoá."),
    ("http error 403", "Máy chủ từ chối truy cập (403). Thử lại sau hoặc cập nhật yt-dlp trong Cài đặt."),
    ("http error 429", "Trang tạm chặn vì tải quá nhiều (429). Đợi vài phút rồi thử lại."),
    ("ffmpeg is not installed", MSG_NO_FFMPEG),
    ("ffmpeg not found", MSG_NO_FFMPEG),
    ("không tìm thấy ffmpeg", MSG_NO_FFMPEG),
    ("cannot find the file specified", "Thiếu file cần thiết của app (thường là ffmpeg.exe). "
                                       "Giải nén lại đầy đủ gói app rồi mở lại."),
    ("was not closed cleanly", MSG_NETWORK),
    ("connection reset", MSG_NETWORK),
    ("connection aborted", MSG_NETWORK),
    ("connection broken", MSG_NETWORK),
    ("remote end closed", MSG_NETWORK),
    ("incompleteread", MSG_NETWORK),
    ("did not get any data blocks", MSG_NETWORK),
    ("timed out", "Kết nối quá chậm hoặc bị gián đoạn. Bấm Thử lại."),
    ("unable to download webpage", "Không kết nối được tới trang. Kiểm tra mạng rồi thử lại."),
    ("unable to extract", "Trang đã đổi cấu trúc. Cập nhật yt-dlp trong Cài đặt rồi thử lại."),
    ("requested format is not available", "Không có định dạng phù hợp."),
    ("no video formats", "Bài này không có video (có thể là ảnh hoặc chữ)."),
]


def humanize_error(msg: str) -> str:
    low = (msg or "").lower()
    for key, text in ERROR_RULES:
        if key in low:
            return text
    m = re.sub(r"^ERROR:\s*", "", msg or "").strip()
    m = re.sub(r"^\[[^\]]+\]\s*[\w-]*:?\s*", "", m)
    return "Không tải được: " + (m[:200] or "lỗi không rõ")


# ------------------------------------------------------------- công việc --
class Job:
    def __init__(self, url: str, preset: str, title: str, thumbnail: str, kind: str):
        self.id = uuid.uuid4().hex[:10]
        self.url, self.preset, self.title, self.thumbnail, self.kind = url, preset, title, thumbnail, kind
        self.status = "queued"   # queued | downloading | processing | done | error | cancelled
        self.progress = 0.0
        self.speed = None
        self.eta = None
        self.downloaded = 0
        self.total = None
        self.item = None
        self.n_items = None
        self.filepath = None
        self.files: list[str] = []
        self.error = None
        self.note = None
        self.warning = None      # tải xong nhưng có việc chưa làm được (vd. chưa chuyển được H.265)
        self.log: list[str] = []
        self.created = time.time()
        self.finished = None
        self.cancel = False
        self.tmp_files: set[str] = set()

    def public(self) -> dict:
        return {
            "id": self.id, "url": self.url, "preset": self.preset, "kind": self.kind,
            "title": self.title, "thumbnail": self.thumbnail, "status": self.status,
            "progress": round(self.progress, 1), "speed": self.speed, "eta": self.eta,
            "downloaded": self.downloaded, "total": self.total,
            "item": self.item, "n_items": self.n_items,
            "filepath": self.filepath, "files": self.files, "error": self.error, "note": self.note,
            "warning": self.warning,
            "log": self.log[-12:], "created": self.created, "finished": self.finished,
        }


JOBS: "OrderedDict[str, Job]" = OrderedDict()
EXECUTOR = ThreadPoolExecutor(max_workers=2)
MSG_HEVC_NOT_CONVERTED = ("Đã tải xong nhưng chưa chuyển được sang H.264, máy có thể chỉ phát tiếng. "
                          "Bấm Thử lại, hoặc mở bằng VLC.")


def convert_hevc_files(job: Job, log, check_cancel) -> None:
    """Douyin/TikTok hay phát H.265, Windows không có sẵn bộ giải mã nên chỉ nghe tiếng: chuyển sang H.264.
    Lỗi ở bước này không làm hỏng lượt tải: giữ file gốc và ghi cảnh báo."""
    if not (SETTINGS.convert_hevc and job.preset != "audio" and job.files):
        return
    if not ensure_ffmpeg():
        job.warning = MSG_HEVC_NOT_CONVERTED
        log("✖ không có ffmpeg nên không kiểm tra/chuyển được H.265")
        return
    n = len(job.files)
    result = []
    for i, f in enumerate(list(job.files)):
        if not os.path.exists(f) or not is_hevc(probe_video(f).get("vcodec")):
            result.append(f)
            continue
        check_cancel()
        job.status = "processing"
        job.note = "chuyển H.265 → H.264 để mở được trên mọi máy"
        job.progress = i / n * 100
        log(f"[chuyển mã] {os.path.basename(f)}: H.265 → H.264")

        def on_progress(p, i=i):
            job.progress = (i + p / 100) / n * 100

        try:
            result.append(convert_to_h264(f, on_progress, lambda: job.cancel))
        except ConversionError as e:
            result.append(f)
            job.warning = MSG_HEVC_NOT_CONVERTED
            log(f"✖ chuyển mã lỗi: {e}")
            app_log(f"chuyển mã lỗi: {f}: {e}")
    job.files = result
    job.filepath = result[-1] if result else job.filepath
    job.note = None


def run_job(job: Job) -> None:
    if job.cancel:
        job.status = "cancelled"
        job.finished = time.time()
        return
    job.status = "downloading"
    out_dir = Path(SETTINGS.download_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    def log(msg: str) -> None:
        job.log.append(msg)
        del job.log[:-40]

    def check_cancel() -> None:
        if job.cancel:
            raise yt_dlp.utils.DownloadCancelled("Đã huỷ")

    def hook(d: dict) -> None:
        check_cancel()
        info = d.get("info_dict") or {}
        n = info.get("n_entries") or info.get("playlist_count")
        if n:
            job.n_items = n
            job.item = info.get("playlist_index") or info.get("playlist_autonumber") or job.item
        if not job.title and info.get("title"):
            job.title = info["title"]
        if d.get("filename"):
            job.tmp_files.add(d["filename"])
        st = d.get("status")
        if st == "downloading":
            job.status = "downloading"
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            job.downloaded = d.get("downloaded_bytes") or 0
            job.total = total
            frac = (job.downloaded / total) if total else 0.0
            if job.n_items and job.item:
                job.progress = ((job.item - 1) + frac) / job.n_items * 100
            elif total:
                job.progress = frac * 100
            job.speed = d.get("speed")
            job.eta = d.get("eta")
        elif st == "finished":
            job.speed = None
            job.eta = None
            job.status = "processing"
            if not job.n_items:
                job.progress = 100.0

    def pp_hook(d: dict) -> None:
        check_cancel()
        if d.get("status") == "started":
            job.status = "processing"
        if d.get("status") == "finished" and d.get("postprocessor") == "MoveFiles":
            fp = (d.get("info_dict") or {}).get("filepath")
            if fp and fp not in job.files:
                job.files.append(fp)
                job.filepath = fp

    can_merge = bool(ensure_ffmpeg())
    opts = {
        **base_opts(log),
        "format": preset_spec(job.preset, can_merge, prefers_combined(job.url)),
        "format_sort": sort_fields(job.preset),
        "outtmpl": {"default": str(out_dir / "%(playlist_title&{}/|)s%(title).100B [%(id)s].%(ext)s")},
        "merge_output_format": "mp4/mkv",
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "noplaylist": job.kind != "playlist",
    }
    if job.preset == "audio":
        if not can_merge:
            job.status, job.error, job.finished = "error", "Cần ffmpeg để xuất MP3. Cài ffmpeg rồi mở lại app.", time.time()
            return
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]
        opts.pop("merge_output_format", None)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(job.url, download=True)
        if not job.files and info:
            entries = (info.get("entries") or []) if info.get("_type") == "playlist" else [info]
            for e in entries:
                for rd in (e or {}).get("requested_downloads") or []:
                    if rd.get("filepath"):
                        job.files.append(rd["filepath"])
        if job.files:
            job.filepath = job.files[-1]
        convert_hevc_files(job, log, check_cancel)
        job.status = "done"
        job.progress = 100.0
        if job.n_items:
            job.item = job.n_items
    except yt_dlp.utils.DownloadCancelled:
        job.status = "cancelled"
        for f in job.tmp_files:
            for cand in (f, f + ".part", f + ".ytdl"):
                try:
                    if os.path.exists(cand) and cand not in job.files:
                        os.remove(cand)
                except OSError:
                    pass
    except yt_dlp.utils.DownloadError as e:
        job.status = "error"
        job.error = humanize_error(str(e))
        log(str(e))
        app_log(f"LỖI {job.url}: {e}")
    except Exception as e:  # noqa: BLE001
        job.status = "error"
        job.error = humanize_error(str(e))
        log(f"{type(e).__name__}: {e}")
        app_log(f"LỖI {job.url}: {type(e).__name__}: {e}")
    finally:
        job.speed = None
        job.eta = None
        job.finished = time.time()


# ------------------------------------------------------------------ API --
app = FastAPI(title="Tải video không logo")


class UrlIn(BaseModel):
    url: str


class DownloadIn(BaseModel):
    url: str
    preset: str = "best"
    title: str = ""
    thumbnail: str = ""
    kind: str = "video"


class OpenIn(BaseModel):
    job_id: str = ""


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/health")
def api_health():
    return {
        "app": "tai-video",
        "version": APP_VERSION,
        "frozen": FROZEN,
        "yt_dlp": yt_dlp.version.__version__,
        "ffmpeg": FFMPEG,
        "download_dir": SETTINGS.download_dir,
        "python": platform.python_version(),
    }


# ------------------------------------------------------------- cập nhật --
# Mỗi bản phát hành đính kèm file latest.json: {"version", "page_url", "zip_url", "notes"}.
# App chỉ đọc file này để báo có bản mới và đưa nút mở trang tải; người dùng tự tải và thay file.
UPDATE_CACHE: dict = {"at": 0.0, "data": None}


def parse_version(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:4]) or (0,)


def update_status(force: bool = False) -> dict:
    cache = UPDATE_CACHE
    if force or cache["data"] is None or time.time() - cache["at"] > 6 * 3600:
        data = None
        url = (SETTINGS.update_url or "").strip()
        if url.startswith(("https://", "http://127.0.0.1")):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": f"TaiVideo/{APP_VERSION}", "Cache-Control": "no-cache"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = json.loads(r.read(100_000).decode("utf-8"))
            except Exception:  # noqa: BLE001
                data = None
        cache.update(at=time.time(), data=data)
    m = cache["data"] or {}
    latest = str(m.get("version") or "")
    return {
        "current": APP_VERSION,
        "latest": latest or None,
        "checked": cache["data"] is not None,
        "available": bool(latest) and parse_version(latest) > parse_version(APP_VERSION),
        "notes": str(m.get("notes") or ""),
        "page_url": str(m.get("page_url") or ""),
        "zip_url": str(m.get("zip_url") or ""),
    }


@app.get("/api/update/check")
def api_update_check(force: bool = False):
    return update_status(force)


@app.post("/api/quit")
def api_quit():
    """Nút Thoát trong Cài đặt: tắt app (dùng khi giao diện mở trong tab trình duyệt, không có cửa sổ riêng)."""
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


@app.post("/api/preview")
def api_preview(body: UrlIn):
    url = extract_first_url(body.url)
    if not url:
        raise HTTPException(400, "Không tìm thấy link hợp lệ. Dán link bắt đầu bằng http hoặc https.")
    url = normalize_url(url)
    can_merge = bool(ensure_ffmpeg())
    combined_first = prefers_combined(url)
    opts = {**base_opts(), "extract_flat": "in_playlist", "format": preset_spec("best", can_merge, combined_first)}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                raise HTTPException(422, "Không đọc được thông tin video.")
            if info.get("_type") == "playlist":
                entries = [e for e in (info.get("entries") or []) if e]
                return {
                    "kind": "playlist", "url": url, "id": info.get("id"),
                    "title": info.get("title") or "Danh sách video",
                    "site": site_name(info),
                    "uploader": info.get("uploader") or info.get("channel") or "",
                    "count": len(entries),
                    "thumbnail": first_thumb(info) or (first_thumb(entries[0]) if entries else ""),
                    "entries": [{"title": e.get("title") or e.get("id"), "duration": e.get("duration")} for e in entries[:6]],
                    "watermark": "unknown",
                    "presets": {k: {"label": v["label"], "available": True, "detail": "cả danh sách"} for k, v in PRESETS.items()},
                }
            presets = {}
            for key, meta in PRESETS.items():
                chosen = choose(ydl, info, preset_spec(key, can_merge, combined_first), sort_fields(key))
                if not chosen:
                    presets[key] = {"label": meta["label"], "available": False, "detail": "không có"}
                    continue
                if key == "audio":
                    ap = [p for p in parts_of(chosen) if p.get("acodec") != "none"] or parts_of(chosen)
                    detail = " · ".join(x for x in ["mp3", fmt_size(size_of(ap[0])) if ap else ""] if x)
                    presets[key] = {"label": meta["label"], "available": True, "detail": detail, "watermarked": False}
                    continue
                # Mức chọn là "tối đa N": nếu không có bản nhỏ hơn N thì lấy bản nhỏ nhất,
                # và dòng chi tiết luôn ghi đúng độ phân giải sẽ nhận được.
                res = res_of(chosen)
                res_text = f"{res}p" if res else (chosen.get("resolution") or "")
                ext = merged_ext(chosen)
                vparts = [p for p in parts_of(chosen) if p.get("vcodec") not in (None, "none")]
                hevc = is_hevc(vparts[0].get("vcodec")) if vparts else False
                detail = " · ".join(x for x in [res_text, ext, "H.265" if hevc else "", fmt_size(size_of(chosen))] if x)
                presets[key] = {"label": meta["label"], "available": True, "detail": detail,
                                "watermarked": is_watermarked(chosen), "res": res, "hevc": hevc}
            has_wm_flag = any("watermark" in (f.get("format_note") or "").lower() for f in info.get("formats") or [])
            best = presets.get("best") or {}
            ekey = (info.get("extractor_key") or "").lower()
            if has_wm_flag:
                watermark = "watermarked" if best.get("watermarked") else "clean"
            elif ekey.startswith(KNOWN_CLEAN):
                watermark = "clean"
            else:
                watermark = "unknown"
            return {
                "kind": "video", "url": url, "id": info.get("id"),
                "title": info.get("title") or info.get("id") or "Video",
                "site": site_name(info),
                "uploader": info.get("uploader") or info.get("channel") or info.get("uploader_id") or "",
                "duration": info.get("duration"),
                "thumbnail": first_thumb(info),
                "webpage_url": info.get("webpage_url") or url,
                "watermark": watermark,
                "presets": presets,
                "convert_hevc": bool(SETTINGS.convert_hevc and FFMPEG),
            }
    except HTTPException:
        raise
    except yt_dlp.utils.DownloadError as e:
        raise HTTPException(422, humanize_error(str(e)))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, humanize_error(f"{type(e).__name__}: {e}"))


@app.post("/api/download")
def api_download(body: DownloadIn):
    url = extract_first_url(body.url)
    if not url:
        raise HTTPException(400, "Link không hợp lệ.")
    url = normalize_url(url)
    if body.preset not in PRESETS:
        raise HTTPException(400, "Chất lượng không hợp lệ.")
    job = Job(url, body.preset, body.title, body.thumbnail, "playlist" if body.kind == "playlist" else "video")
    JOBS[job.id] = job
    EXECUTOR.submit(run_job, job)
    return job.public()


@app.get("/api/jobs")
def api_jobs():
    return [j.public() for j in reversed(JOBS.values())]


@app.post("/api/jobs/clear")
def api_clear():
    for jid in [j.id for j in JOBS.values() if j.status in ("done", "error", "cancelled")]:
        JOBS.pop(jid, None)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Không thấy mục này.")
    if job.status in ("queued", "downloading", "processing"):
        job.cancel = True
        if job.status == "queued":
            job.status = "cancelled"
            job.finished = time.time()
    return job.public()


@app.post("/api/jobs/{job_id}/retry")
def api_retry(job_id: str):
    """Tải lại mục lỗi/đã huỷ, cùng link và chất lượng. File .part còn lại sẽ được tải tiếp từ chỗ dở."""
    old = JOBS.get(job_id)
    if not old:
        raise HTTPException(404, "Không thấy mục này.")
    if old.status in ("queued", "downloading", "processing"):
        return old.public()
    job = Job(old.url, old.preset, old.title, old.thumbnail, old.kind)
    JOBS.pop(job_id, None)
    JOBS[job.id] = job
    EXECUTOR.submit(run_job, job)
    return job.public()


@app.delete("/api/jobs/{job_id}")
def api_delete(job_id: str):
    job = JOBS.get(job_id)
    if job and job.status in ("queued", "downloading", "processing"):
        job.cancel = True
    JOBS.pop(job_id, None)
    return {"ok": True}


@app.post("/api/open")
def api_open(body: OpenIn):
    target = None
    job = JOBS.get(body.job_id) if body.job_id else None
    if job and job.filepath and Path(job.filepath).exists():
        target = Path(job.filepath)
    folder = Path(SETTINGS.download_dir)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        if IS_WIN:
            if target:
                subprocess.Popen(["explorer", "/select,", str(target)])
            else:
                os.startfile(str(folder))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", "-R", str(target)] if target else ["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(target.parent if target else folder)])
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Không mở được thư mục: {e}")
    return {"ok": True, "path": str(target or folder)}


@app.get("/api/settings")
def api_get_settings():
    return {**SETTINGS.model_dump(), "yt_dlp": yt_dlp.version.__version__, "ffmpeg": FFMPEG}


@app.post("/api/settings")
def api_set_settings(body: Settings):
    global SETTINGS
    d = body.download_dir.strip() or str(DEFAULT_DOWNLOAD_DIR)
    try:
        Path(d).mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"Không tạo được thư mục: {e}")
    if body.cookies_mode == "file" and body.cookies_file and not Path(body.cookies_file).exists():
        raise HTTPException(400, "Không thấy file cookies ở đường dẫn đã nhập.")
    SETTINGS = Settings(download_dir=d, cookies_mode=body.cookies_mode,
                        cookies_browser=body.cookies_browser, cookies_file=body.cookies_file.strip(),
                        convert_hevc=body.convert_hevc)
    save_settings(SETTINGS)
    return api_get_settings()


@app.post("/api/settings/pick-folder")
def api_pick_folder():
    path = ""
    try:
        if IS_WIN:  # hộp thoại chọn thư mục của Windows, chạy được cả khi máy không cài Python
            initial = SETTINGS.download_dir.replace("'", "''")
            ps = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
                  "Add-Type -AssemblyName System.Windows.Forms; "
                  "$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}; "
                  "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
                  "$d.Description = 'Chọn thư mục lưu video'; $d.ShowNewFolderButton = $true; "
                  f"$d.SelectedPath = '{initial}'; "
                  "if ($d.ShowDialog($owner) -eq 'OK') { $d.SelectedPath }")
            out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                                 capture_output=True, text=True, encoding="utf-8", timeout=600, creationflags=NO_WINDOW)
        else:
            code = ("import sys, tkinter as tk\nfrom tkinter import filedialog\n"
                    "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
                    "print(filedialog.askdirectory(initialdir=sys.argv[1], title='Chọn thư mục lưu video') or '')\n")
            out = subprocess.run([sys.executable, "-c", code, SETTINGS.download_dir],
                                 capture_output=True, text=True, encoding="utf-8", timeout=600)
        path = (out.stdout or "").strip()
    except Exception:  # noqa: BLE001
        path = ""
    return {"path": path.replace("/", os.sep) if path else ""}


@app.post("/api/update")
def api_update():
    if FROZEN:
        raise HTTPException(400, "Bản .exe đã kèm sẵn yt-dlp. Bấm \"Kiểm tra cập nhật\" để biết có bản app mới không.")
    old = yt_dlp.version.__version__
    try:
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        v = subprocess.run([sys.executable, "-c", "import yt_dlp;print(yt_dlp.version.__version__)"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        new = (v.stdout or "").strip() or old
        return {"ok": r.returncode == 0, "old": old, "new": new, "restart_needed": new != old,
                "log": ((r.stdout or "") + (r.stderr or ""))[-1500:]}
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"Không cập nhật được: {e}")


THUMB_CACHE: "OrderedDict[str, tuple[str, bytes]]" = OrderedDict()


@app.get("/api/thumb")
def api_thumb(u: str):
    if not u.startswith(("http://", "https://")):
        raise HTTPException(400, "bad url")
    if u in THUMB_CACHE:
        ctype, data = THUMB_CACHE[u]
    else:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "image/*,*/*;q=0.8"})
            with urllib.request.urlopen(req, timeout=25) as r:
                data = r.read(8_000_000)
                ctype = (r.headers.get("Content-Type") or "image/jpeg").split(";")[0]
        except Exception:  # noqa: BLE001
            raise HTTPException(502, "no thumb")
        THUMB_CACHE[u] = (ctype, data)
        while len(THUMB_CACHE) > 200:
            THUMB_CACHE.popitem(last=False)
    return Response(content=data, media_type=ctype, headers={"Cache-Control": "max-age=3600"})


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ----------------------------------------------------------------- main --
def free_port(start: int = 8765) -> int:
    for p in range(start, start + 30):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return 0


def find_running_instance(port: int = 0) -> tuple[str | None, dict]:
    """App đã mở sẵn trên máy thì trả về (địa chỉ, thông tin) của nó, để mở thêm cửa sổ thay vì chạy bản thứ hai."""
    for p in ([port] if port else range(8765, 8795)):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/api/health", timeout=1.5) as r:
                info = json.load(r)
            if info.get("app") == "tai-video":
                return f"http://127.0.0.1:{p}", info
        except Exception:  # noqa: BLE001
            continue
    return None, {}


def retire_old_instance(base: str) -> None:
    """Bản cũ hơn đang chạy: nhờ nó tắt nếu đang rảnh (bản ≥1.1.1 có /api/quit). Bản quá cũ thì để yên,
    bản mới chạy máy chủ riêng ở cổng khác nên không bị bản cũ chen vào."""
    try:
        with urllib.request.urlopen(base + "/api/jobs", timeout=2) as r:
            jobs = json.load(r)
        if any(j.get("status") in ("queued", "downloading", "processing") for j in jobs):
            return
        req = urllib.request.Request(base + "/api/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=2).close()
        time.sleep(1.0)
        app_log(f"đã nhờ bản cũ ở {base} tắt")
    except Exception:  # noqa: BLE001
        pass


def find_browser_app() -> str | None:
    """Edge hoặc Chrome: chế độ --app của chúng hiện giao diện như một cửa sổ ứng dụng riêng."""
    cands: list[Path] = []
    if IS_WIN:
        for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                cands += [Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                          Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"]
    for name in ("msedge", "chrome", "google-chrome", "chromium", "brave"):
        w = shutil.which(name)
        if w:
            cands.append(Path(w))
    return next((str(c) for c in cands if c.exists()), None)


def open_app_window(url: str):
    exe = find_browser_app()
    if not exe:
        return None
    profile = DATA_DIR / "window-profile"  # hồ sơ riêng để cửa sổ này tách khỏi trình duyệt đang mở
    profile.mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(
        [exe, f"--app={url}", f"--user-data-dir={profile}", "--window-size=1040,860",
         "--no-first-run", "--no-default-browser-check", "--disable-sync",
         "--disable-features=Translate,msEdgeSidebar,msHubApps"],
        creationflags=NO_WINDOW)


def launch_ui(url: str, window: bool, own_server: bool) -> None:
    """Mở giao diện. Ở chế độ cửa sổ riêng, đóng cửa sổ là tắt app (đợi các mục đang tải xong trước)."""
    if own_server:
        time.sleep(1.0)  # đợi máy chủ lên
    print(f"[giao diện] cửa sổ riêng: {'có' if window else 'không'} · trình duyệt: {find_browser_app()}")
    proc = open_app_window(url) if window else None
    if proc is None:
        webbrowser.open(url)
        return
    if not own_server:
        return
    started = time.time()
    proc.wait()
    if time.time() - started < 3:  # cửa sổ được giao cho phiên trình duyệt khác, không theo dõi được: giữ máy chủ chạy
        return
    while any(j.status in ("queued", "downloading", "processing") for j in JOBS.values()):
        time.sleep(2)
    os._exit(0)


def main() -> None:
    ap = argparse.ArgumentParser(description=APP_NAME)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--no-browser", action="store_true", help="chỉ chạy máy chủ, không mở giao diện")
    ap.add_argument("--browser", action="store_true", help="mở bằng trình duyệt thường thay vì cửa sổ riêng")
    ap.add_argument("--window", action="store_true", help="mở cửa sổ ứng dụng riêng (mặc định với bản .exe)")
    ap.add_argument("--diag", action="store_true", help="in thông tin chẩn đoán rồi thoát")
    a = ap.parse_args()
    if a.diag:
        info = {"version": APP_VERSION, "frozen": FROZEN, "app_dir": str(APP_DIR), "bundle_dir": str(BUNDLE_DIR),
                "data_dir": str(DATA_DIR), "settings_file": str(SETTINGS_FILE), "ffmpeg": FFMPEG, "ffprobe": FFPROBE,
                "browser_app": find_browser_app(), "python": platform.python_version()}
        text = json.dumps(info, ensure_ascii=False, indent=2)
        print(text)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            (DATA_DIR / "diag.json").write_text(text, "utf-8")
        except OSError:
            pass
        return
    if FROZEN:  # bản .exe không có cửa sổ dòng lệnh: ghi log ra file (giữ tối đa ~2 MB)
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            if LOG_FILE.is_file() and LOG_FILE.stat().st_size > 2_000_000:
                LOG_FILE.replace(LOG_FILE.with_suffix(".log.1"))
            sys.stdout = sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        except OSError:
            pass
    window = (a.window or FROZEN) and not a.browser
    running, running_info = find_running_instance(a.port)
    if running and not a.no_browser:
        if running_info.get("version") == APP_VERSION:
            launch_ui(running, window, own_server=False)
            return
        retire_old_instance(running)  # bản khác phiên bản: không dùng lại, chạy máy chủ của bản này
    port = a.port or free_port()
    url = f"http://127.0.0.1:{port}"
    app_log(f"{APP_NAME} {APP_VERSION} đang chạy tại {url}   (Ctrl+C để thoát)")
    app_log(f"yt-dlp {yt_dlp.version.__version__} · ffmpeg: {FFMPEG or 'KHÔNG THẤY (không ghép được video HD, không xuất MP3)'}"
            f" · cài đặt: {SETTINGS_FILE}")
    if not a.no_browser:
        threading.Thread(target=launch_ui, args=(url, window, True), daemon=True).start()
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
