"""Kiểm tra nhanh: tải một video qua API của app đang chạy rồi soi file bằng ffprobe.

    .venv/Scripts/python.exe tools/smoke_test.py <link> [best|1080|720|480|audio]
"""
import json, time, urllib.request, subprocess, os, sys
BASE = 'http://127.0.0.1:8765'
def call(path, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method or ('POST' if data is not None else 'GET'), headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)
url = sys.argv[1]; preset = sys.argv[2] if len(sys.argv) > 2 else 'best'
job = call('/api/download', {'url': url, 'preset': preset, 'title': '', 'thumbnail': '', 'kind': 'video'})
print('job', job['id'], job['status'])
t = time.time()
while time.time() - t < 570:
    j = next(x for x in call('/api/jobs') if x['id'] == job['id'])
    if j['status'] in ('done', 'error', 'cancelled'):
        break
    time.sleep(1.5)
print('status:', j['status'], '| progress', j['progress'], '| error', j['error'])
print('title:', j['title'][:70]); print('file:', j['filepath'])
if j['log']: print('log tail:', j['log'][-3:])
if j['filepath'] and os.path.exists(j['filepath']):
    print('size KB:', os.path.getsize(j['filepath']) // 1024)
    print(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,codec_name,width,height', '-of', 'csv=p=0', j['filepath']], capture_output=True, text=True).stdout.strip())
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'last_frame.png')
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-ss', '3', '-i', j['filepath'], '-frames:v', '1', '-vf', 'scale=540:-1', out])
    print('frame ->', out, os.path.exists(out))
