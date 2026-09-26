#!/usr/bin/env python3
"""Đóng gói bản phát hành Windows.

    .venv\\Scripts\\python.exe build_release.py                 # dựng dist\\TaiVideo-<ver>-win64.zip + latest.json
    .venv\\Scripts\\python.exe build_release.py --publish       # ...rồi đăng lên GitHub Releases bằng gh
    .venv\\Scripts\\python.exe build_release.py --notes "Sửa Douyin"

Kết quả trong dist/:
    TaiVideo-<ver>/            thư mục chạy được (TaiVideo.exe + ffmpeg.exe + DOC-TOI.txt)
    TaiVideo-<ver>-win64.zip   file gửi cho người dùng
    latest.json                app trên máy khác đọc file này để biết có bản mới

Muốn ra bản mới: tăng APP_VERSION trong app.py rồi chạy lại script này.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
PY = Path(sys.executable)

DOC = """TẢI VIDEO KHÔNG LOGO {ver}

CÁCH DÙNG
1. Giải nén cả thư mục này vào một chỗ tuỳ ý (ví dụ D:\\TaiVideo). Giữ TaiVideo.exe và ffmpeg.exe cạnh nhau.
2. Nháy đúp TaiVideo.exe. Lần đầu Windows có thể hiện "Windows đã bảo vệ PC của bạn":
   bấm "Thông tin thêm" rồi "Vẫn chạy" (app chưa mua chữ ký số).
3. Cửa sổ app hiện ra, dán link video và bấm Tải về. Video lưu vào Downloads\\TaiVideo (đổi trong Cài đặt).

CẬP NHẬT
- Khi có bản mới, app hiện dòng "Có bản mới ..." ở đầu trang. Bấm "Mở trang tải", tải file zip mới,
  giải nén đè lên thư mục cũ. Cài đặt của bạn được giữ nguyên.
- Trang phát hành: https://github.com/{repo}/releases

GHI CHÚ
- Cần Edge hoặc Chrome trên máy để hiện cửa sổ app (Windows 10/11 có sẵn Edge).
- YouTube đủ mức chất lượng nhất khi máy có Node.js hoặc Deno; không có vẫn tải được nhưng có thể thiếu vài mức.
- Douyin và các trang cần đăng nhập: xem hướng dẫn cookies trong Cài đặt.
- ffmpeg.exe kèm theo là bản dựng của gyan.dev (giấy phép GPL), mã nguồn tại https://ffmpeg.org
- Nhật ký lỗi: %LOCALAPPDATA%\\TaiVideo\\app.log
"""


def app_version() -> str:
    m = re.search(r'^APP_VERSION = "([^"]+)"', (ROOT / "app.py").read_text("utf-8"), re.M)
    if not m:
        sys.exit("Không thấy APP_VERSION trong app.py")
    return m.group(1)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_ffmpeg(explicit: str | None) -> Path | None:
    for c in (explicit, ROOT / "build" / "ffmpeg" / "ffmpeg.exe", shutil.which("ffmpeg")):
        if c and Path(c).exists():
            return Path(c)
    return None


def run(cmd: list, **kw) -> None:
    print("+", " ".join(str(c) for c in cmd))
    subprocess.run([str(c) for c in cmd], check=True, **kw)


def main() -> None:
    ap = argparse.ArgumentParser(description="Đóng gói bản phát hành Windows")
    ap.add_argument("--repo", default="lebac-svg/tai-video-khong-logo", help="kho GitHub owner/repo để ghi link tải")
    ap.add_argument("--ffmpeg", help="ffmpeg.exe để kèm theo (mặc định: build/ffmpeg/ffmpeg.exe hoặc ffmpeg trên PATH)")
    ap.add_argument("--notes", default="", help="ghi chú ngắn cho bản này, hiện trong thông báo có bản mới")
    ap.add_argument("--publish", action="store_true", help="đăng lên GitHub Releases bằng gh (cần gh auth login)")
    ap.add_argument("--skip-build", action="store_true", help="dùng lại dist/bin/TaiVideo.exe đã dựng")
    a = ap.parse_args()

    ver = app_version()
    exe = DIST / "bin" / "TaiVideo.exe"
    if not a.skip_build:
        sep = ";" if os.name == "nt" else ":"
        run([PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile", "--noconsole",
             "--name", "TaiVideo", "--icon", ROOT / "build" / "icon.ico",
             "--add-data", f"{ROOT / 'static'}{sep}static",
             "--collect-all", "curl_cffi", "--collect-data", "certifi",
             "--distpath", DIST / "bin", "--workpath", ROOT / "build" / "work",
             "--specpath", ROOT / "build", ROOT / "app.py"])
    if not exe.exists():
        sys.exit(f"Không thấy {exe}")

    rel = DIST / f"TaiVideo-{ver}"
    shutil.rmtree(rel, ignore_errors=True)
    if rel.exists():  # thư mục đang bị giữ (cửa sổ dòng lệnh đứng trong đó, exe đang chạy...)
        for f in rel.iterdir():
            try:
                f.unlink()
            except OSError:
                sys.exit(f"Không xoá được {f}: đóng TaiVideo.exe hoặc cửa sổ đang mở thư mục này rồi chạy lại.")
    rel.mkdir(parents=True, exist_ok=True)
    shutil.copy2(exe, rel / "TaiVideo.exe")
    ff = find_ffmpeg(a.ffmpeg)
    if ff:
        shutil.copy2(ff, rel / "ffmpeg.exe")
    else:
        print("CẢNH BÁO: không có ffmpeg.exe để kèm theo, máy khác sẽ thiếu ghép video HD và MP3")
    (rel / "DOC-TOI.txt").write_text(DOC.format(ver=ver, repo=a.repo), "utf-8")

    zip_path = DIST / f"TaiVideo-{ver}-win64.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(rel.iterdir()):
            z.write(f, f"TaiVideo-{ver}/{f.name}")

    manifest = {
        "version": ver,
        "page_url": f"https://github.com/{a.repo}/releases/latest",
        "zip_url": f"https://github.com/{a.repo}/releases/download/v{ver}/TaiVideo-{ver}-win64.zip",
        "zip_sha256": sha256(zip_path),
        "notes": a.notes,
    }
    (DIST / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), "utf-8")

    mb = zip_path.stat().st_size / 1e6
    print(f"\nXong bản {ver}:")
    print(f"  {rel}")
    print(f"  {zip_path}  ({mb:.0f} MB)")
    print(f"  {DIST / 'latest.json'}")
    if a.publish:
        run(["gh", "release", "create", f"v{ver}", zip_path, DIST / "latest.json",
             "--repo", a.repo, "--title", f"Bản {ver}", "--notes", a.notes or f"Bản {ver}"])
        print(f"Đã đăng: https://github.com/{a.repo}/releases/tag/v{ver}")
    else:
        print("Để đăng lên GitHub Releases: chạy lại với --publish (hoặc tải zip + latest.json lên tay).")


if __name__ == "__main__":
    main()
