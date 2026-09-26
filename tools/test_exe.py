"""Chạy thử file exe đã dựng: mở máy chủ ẩn ở cổng 8799 với thư mục dữ liệu riêng (không đụng cài đặt,
cookies hay thư mục tải của người dùng), xem trước và tải từng link, soi file bằng ffprobe, rồi tắt.

    .venv\\Scripts\\python.exe tools\\test_exe.py [TaiVideo.exe] [link[@chất_lượng] ...]

Mặc định thử "Me at the zoo" (YouTube, 19 giây, cần ghép hình + tiếng bằng ffmpeg).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


def call(path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=180))


def probe(path: str) -> str:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return "(không có ffprobe để soi)"
    out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height",
                          "-of", "csv=p=0", path], capture_output=True, text=True)
    return " ".join(out.stdout.split())


def main() -> None:
    args = sys.argv[1:]
    exe = Path(args.pop(0)) if args and args[0].lower().endswith(".exe") else ROOT / "dist" / "bin" / "TaiVideo.exe"
    tests = args or ["https://www.youtube.com/watch?v=jNQXAC9IVRw@best"]
    if not exe.exists():
        sys.exit(f"Không thấy {exe}")

    data_dir = Path(tempfile.mkdtemp(prefix="taivideo-test-"))
    out_dir = data_dir / "downloads"
    env = {**os.environ, "TAIVIDEO_DATA_DIR": str(data_dir)}
    t0 = time.time()
    proc = subprocess.Popen([str(exe), "--no-browser", "--port", str(PORT)], env=env)
    try:
        for _ in range(90):
            try:
                h = call("/api/health")
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            sys.exit(f"exe không lên máy chủ sau 45 giây, xem {data_dir / 'app.log'}")
        s = call("/api/settings", {"download_dir": str(out_dir), "cookies_mode": "none",
                                   "cookies_browser": "firefox", "cookies_file": "", "convert_hevc": True})
        print(f"khởi động {time.time() - t0:.1f}s | bản {h.get('version')} | ffmpeg {h.get('ffmpeg')} | cookies: {s['cookies_mode']}")
        ok = True
        for t in tests:
            url, _, preset = t.partition("@")
            preset = preset or "best"
            r = call("/api/preview", {"url": url})
            print(f"\n{r['site']} | {r['title'][:60]} | {preset}: {r['presets'][preset]['detail']}")
            job = call("/api/download", {"url": url, "preset": preset, "title": r["title"], "thumbnail": "", "kind": r["kind"]})
            for _ in range(600):
                j = next(x for x in call("/api/jobs") if x["id"] == job["id"])
                if j["status"] in ("done", "error", "cancelled"):
                    break
                time.sleep(1)
            print(f"  kết quả: {j['status']} | lỗi: {j.get('error')} | cảnh báo: {j.get('warning')}")
            if j.get("filepath") and os.path.exists(j["filepath"]):
                print(f"  file: {Path(j['filepath']).name[:70]} | {os.path.getsize(j['filepath']) // 1024} KB | {probe(j['filepath'])}")
            ok = ok and j["status"] == "done" and not j.get("warning")
        print("\nTẤT CẢ ĐẠT" if ok else "\nCÓ MỤC KHÔNG ĐẠT")
    finally:
        # exe onefile của PyInstaller chạy thành 2 tiến trình (bộ nạp + app): tắt cả cây
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.terminate()
        try:
            proc.wait(5)
        except Exception:  # noqa: BLE001
            proc.kill()
        shutil.rmtree(data_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
