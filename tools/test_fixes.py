"""Kiểm tra nhanh các chỗ đã sửa ở bản 1.1.2, không cần mạng, không đụng cài đặt thật.

    .venv\\Scripts\\python.exe tools\\test_fixes.py

1. ffmpeg bị xoá trong lúc app đang mở: app tự tìm lại, không dùng đường dẫn chết.
2. Chuyển mã H.265 lỗi: giữ file gốc, lượt tải vẫn "xong" kèm cảnh báo, không báo hỏng.
3. Chuyển mã H.265 bình thường vẫn chạy.
4. Thông báo lỗi dễ hiểu cho hai lỗi đã gặp.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = Path(tempfile.mkdtemp(prefix="taivideo-fix-"))
os.environ["TAIVIDEO_DATA_DIR"] = str(TMP / "data")  # không đọc settings.json thật (có cookies)
sys.path.insert(0, str(ROOT))
import app  # noqa: E402

results = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append(cond)
    print(("ĐẠT  " if cond else "HỎNG ") + name + (f"  ({detail})" if detail else ""))


def make_hevc(path: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=2",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx265", "-preset", "ultrafast",
                    "-tag:v", "hvc1", "-c:a", "aac", "-shortest", str(path)], check=True)


class FakeJob(app.Job):
    def __init__(self, f: str):
        super().__init__("https://example.com/v", "best", "thử", "", "video")
        self.files, self.filepath = [f], f


def noop(*_):
    return None


try:
    check("cài đặt thử nằm ở thư mục riêng", str(app.SETTINGS_FILE).startswith(str(TMP)), str(app.SETTINGS_FILE))
    check("cookies tắt trong lần thử", app.SETTINGS.cookies_mode == "none")

    # 1. ffmpeg biến mất khi app đang mở -> tự tìm lại
    real = app.FFMPEG
    app.FFMPEG = str(TMP / "khong-con" / "ffmpeg.exe")
    found = app.ensure_ffmpeg()
    check("ffmpeg mất thì tự tìm lại", bool(found) and Path(found).is_file() and Path(found).is_absolute(), str(found))
    app.FFMPEG = real

    # 2. chuyển mã lỗi -> giữ file, lượt tải vẫn xong kèm cảnh báo
    bad = TMP / "hong.mp4"
    make_hevc(bad)
    orig_convert = app.convert_to_h264
    app.convert_to_h264 = lambda *a, **k: (_ for _ in ()).throw(app.ConversionError("giả lập lỗi"))
    job = FakeJob(str(bad))
    app.convert_hevc_files(job, noop, noop)
    app.convert_to_h264 = orig_convert
    check("chuyển mã lỗi: file gốc còn nguyên", bad.is_file() and job.filepath == str(bad))
    check("chuyển mã lỗi: có cảnh báo, không thành lỗi", job.warning == app.MSG_HEVC_NOT_CONVERTED and job.error is None)

    # 3. chuyển mã thật
    good = TMP / "tot.mp4"
    make_hevc(good)
    job = FakeJob(str(good))
    app.convert_hevc_files(job, noop, noop)
    codec = app.probe_video(job.filepath).get("vcodec")
    check("H.265 được chuyển sang H.264", codec == "h264" and job.warning is None, f"codec={codec}")
    leftovers = [p.name for p in TMP.glob("*.tmp.mp4")]
    check("không để lại file tạm", not leftovers, ", ".join(leftovers))

    # 4. thông báo lỗi
    m1 = app.humanize_error("[WinError 2] The system cannot find the file specified")
    m2 = app.humanize_error("ERROR: You have requested merging of multiple formats but ffmpeg is not installed.")
    m3 = app.humanize_error("ERROR: unable to download video data: curl: (92) HTTP/2 stream 1 was not closed cleanly")
    check("lỗi thiếu file có hướng dẫn", "ffmpeg.exe" in m1, m1)
    check("lỗi thiếu ffmpeg có hướng dẫn", "ffmpeg.exe" in m2, m2)
    check("lỗi mạng gợi ý Thử lại", "Thử lại" in m3, m3)
finally:
    shutil.rmtree(TMP, ignore_errors=True)

print(f"\n{sum(results)}/{len(results)} đạt")
sys.exit(0 if all(results) else 1)
