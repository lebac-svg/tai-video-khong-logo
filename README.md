# Tải video không logo

App chạy trên máy (Windows/macOS/Linux) để dán link TikTok, Douyin, YouTube, Instagram, X,
Threads, Reddit… và tải video gốc về máy, ưu tiên bản **không chèn logo**.
Lõi là [yt-dlp](https://github.com/yt-dlp/yt-dlp) (hỗ trợ hơn 1.000 trang), giao diện web tiếng Việt mở trong trình duyệt.

## Chạy

Cần **Python 3.10+** (tick "Add python.exe to PATH" khi cài) và nên có **ffmpeg** trên PATH.

- Windows: nháy đúp `run.bat`. Lần đầu sẽ tự tạo `.venv` và cài thư viện, các lần sau mở ngay.
- macOS/Linux:
  ```bash
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  .venv/bin/python app.py
  ```

App mở `http://127.0.0.1:8765` trong trình duyệt. Thêm `--no-browser` nếu không muốn tự mở, `--port 9000` để đổi cổng.

Video lưu vào `Downloads\TaiVideo` (đổi trong Cài đặt).

## Bản .exe cho máy khác

- `build.bat` (hoặc `.venv\Scripts\python.exe build_release.py`) dựng `dist\TaiVideo-<phiên bản>-win64.zip`
  gồm `TaiVideo.exe` (đã kèm Python, yt-dlp, giao diện) + `ffmpeg.exe` + `DOC-TOI.txt`. Máy khác chỉ cần giải nén và nháy đúp.
- Bản .exe mở trong **cửa sổ ứng dụng riêng** (dùng Edge hoặc Chrome ở chế độ app, không thanh địa chỉ).
  Đóng cửa sổ là tắt app; nếu còn video đang tải thì app đợi tải xong rồi mới tắt. Bản chạy từ mã nguồn vẫn mở trình duyệt như cũ, thêm `--window` nếu muốn cửa sổ riêng.
- Thư mục dữ liệu: `%LOCALAPPDATA%\TaiVideo` (nhật ký `app.log`, hồ sơ cửa sổ). Cài đặt nằm cạnh file exe (`settings.json`).

### Phát hành bản mới để máy khác được báo

1. Tăng `APP_VERSION` trong `app.py` (ví dụ `1.1.0` → `1.2.0`).
2. Chạy `build_release.py --publish --notes "Sửa Douyin"`: script dựng exe, nén zip, tạo `latest.json`
   rồi đăng cả hai lên GitHub Releases của kho `lebac-svg/tai-video-khong-logo` (cần `gh auth login` một lần).
3. App trên máy khác đọc `latest.json` mỗi lần mở (tối đa 6 giờ một lần), thấy số phiên bản lớn hơn thì hiện
   dòng "Có bản mới" kèm nút mở trang tải. Người dùng tải zip mới, giải nén đè lên thư mục cũ, cài đặt giữ nguyên.

App không tự tải và thay file exe: cách đó hay bị Windows Defender chặn và khó kiểm soát; báo có bản mới rồi để người dùng tự tải an toàn hơn.

## Cách "không logo" hoạt động

- **TikTok / Douyin**: máy chủ phát hai bản, một bản có logo (download) và một bản sạch (play).
  yt-dlp đánh dấu bản có logo, app luôn chọn bản sạch trước và chỉ lấy bản có logo khi trang
  không cung cấp bản nào khác. Khi đó con dấu đổi thành "CÓ LOGO" để bạn biết trước.
- **YouTube, Instagram, X, Threads, Reddit…**: file gốc trên máy chủ không có logo, app tải thẳng bản gốc.
- **Trang chèn sẵn logo vào khung hình** (Kuaishou, Likee, Snack Video…): app tải bản gốc như trang phát,
  nhưng không gỡ được logo đã "in" vào video.

## Đã kiểm tra (24/09/2026, yt-dlp 2026.08.19)

| Trang | Kết quả |
|---|---|
| TikTok (link video công khai) | Xem trước + tải bản không logo, cần `curl_cffi` (có trong requirements) |
| YouTube (video, danh sách phát) | Xem trước đủ mức 480p → 2160p, tải và ghép hình + tiếng bằng ffmpeg |
| Instagram (reel công khai) | Xem trước và tải được, không cần cookies |
| X / Twitter (bài công khai) | Đọc được định dạng tới 4K |
| Facebook | **Đang lỗi** ở yt-dlp 2026.08.19, xem mục dưới |

Chưa kiểm tra: tải cả danh sách phát, cookies từ trình duyệt.

### Douyin

Douyin chỉ trả dữ liệu cho trình duyệt thật, nên yt-dlp cần cookies của trình duyệt (không cần đăng nhập):

1. Mở Firefox, vào https://www.douyin.com và để trang tải xong một lần.
2. Trong app: Cài đặt → Cookies → "Lấy từ trình duyệt: Firefox" → Lưu.
3. Dán link Douyin (nhận cả link rút gọn `v.douyin.com/…` và link chia sẻ `iesdouyin.com/share/video/…`).

Nếu vẫn báo cần cookies, đóng Firefox rồi thử lại (cookies chỉ đọc được khi trình duyệt không khoá file).

### Video mở lên chỉ có tiếng, không có hình

Douyin và TikTok hay phát video mã hoá H.265 (HEVC). Windows mặc định không có bộ giải mã H.265 nên
Windows Media Player, Phim & TV chỉ phát tiếng. App tự nhận ra và chuyển sang H.264 ngay sau khi tải
(bật sẵn, tắt được trong Cài đặt). Ở phần xem trước, mức nào là H.265 sẽ có nhãn "H.265".
Nếu muốn giữ nguyên H.265, tắt tuỳ chọn này và xem bằng VLC hoặc cài "HEVC Video Extensions" từ Microsoft Store.

## Hạn chế cần biết

- **Facebook**: yt-dlp bản 2026.08.19 đang lỗi "Cannot parse data" với Facebook
  ([yt-dlp #17720](https://github.com/yt-dlp/yt-dlp/issues/17720)). Khi yt-dlp ra bản sửa, bấm
  **Cài đặt → Cập nhật yt-dlp** rồi mở lại app.
- **Video cần đăng nhập** (Instagram/Facebook riêng tư, YouTube giới hạn tuổi hoặc bị hỏi "xác minh không phải bot"):
  vào Cài đặt, chọn lấy cookies từ trình duyệt đã đăng nhập (Firefox chạy ổn nhất) hoặc chỉ file `cookies.txt`.
- **Không có ffmpeg**: YouTube chỉ tải tối đa 720p (bản đã ghép sẵn), không xuất được MP3.
  Tải ffmpeg tại https://www.gyan.dev/ffmpeg/builds/ (Windows) và thêm vào PATH, hoặc chép `ffmpeg.exe` vào thư mục `ffmpeg\bin\` cạnh `app.py`.
- Các trang đổi cấu trúc thường xuyên. Gặp lỗi lạ, việc đầu tiên là **Cập nhật yt-dlp** trong Cài đặt.
- Cần Node.js hoặc Deno trên máy để YouTube mở đủ định dạng cao (app tự nhận nếu có).

## Cấu trúc

```
app.py              máy chủ FastAPI + yt-dlp (xem trước, hàng đợi tải, huỷ, cookies, cập nhật)
static/index.html   giao diện (HTML/CSS/JS thuần, không build)
requirements.txt    yt-dlp[default,curl-cffi], fastapi, uvicorn
run.bat             chạy trên Windows
settings.json       tự tạo khi lưu cài đặt
```

API nội bộ: `POST /api/preview {url}`, `POST /api/download {url, preset}`, `GET /api/jobs`,
`POST /api/jobs/{id}/cancel`, `DELETE /api/jobs/{id}`, `GET/POST /api/settings`, `POST /api/update`.

## Lưu ý pháp lý

Dùng cho mục đích cá nhân. Video thuộc bản quyền của người đăng; đăng lại nội dung của người khác
cần được họ cho phép. Việc tải có thể trái điều khoản sử dụng của một số nền tảng.
