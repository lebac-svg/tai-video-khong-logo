"""Chạy thử file exe đã dựng: mở máy chủ ẩn ở cổng 8799, kiểm tra health, xem trước và tải một video
nhỏ (YouTube "Me at the zoo", 19 giây), rồi tắt. Không đụng tới cài đặt hay cookies của bản đang dùng.

    .venv\\Scripts\\python.exe tools\\test_exe.py [đường dẫn TaiVideo.exe]
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "bin" / "TaiVideo.exe"
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
TEST_URL = "https://www.youtube.com/watch?v=jNQXAC9IVRw"


def call(path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))


def main() -> None:
    if not EXE.exists():
        sys.exit(f"Không thấy {EXE}")
    t0 = time.time()
    proc = subprocess.Popen([str(EXE), "--no-browser", "--port", str(PORT)])
    try:
        for _ in range(90):
            try:
                h = call("/api/health")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            sys.exit("exe không lên máy chủ sau 45 giây, xem %LOCALAPPDATA%\\TaiVideo\\app.log")
        print(f"khởi động: {time.time() - t0:.1f}s | health:", {k: h.get(k) for k in ("app", "version", "frozen", "yt_dlp", "ffmpeg")})
        r = call("/api/preview", {"url": TEST_URL})
        print("xem trước:", r["site"], "|", r["title"], "| tốt nhất:", r["presets"]["best"]["detail"])
        job = call("/api/download", {"url": TEST_URL, "preset": "best", "title": r["title"], "thumbnail": "", "kind": "video"})
        for _ in range(180):
            j = next(x for x in call("/api/jobs") if x["id"] == job["id"])
            if j["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(1)
        print("tải:", j["status"], "|", j.get("filepath"), "|", j.get("error"))
        if j.get("filepath") and os.path.exists(j["filepath"]):
            print("kích thước KB:", os.path.getsize(j["filepath"]) // 1024)
            os.remove(j["filepath"])
        print("kiểm tra cập nhật:", call("/api/update/check"))
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    main()
