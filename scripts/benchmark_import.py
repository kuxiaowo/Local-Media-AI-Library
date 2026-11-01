"""Isolated import/preview benchmark; never reads an existing media library."""
import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
import threading
import time

parser = argparse.ArgumentParser()
parser.add_argument('--source-root', default=str(Path(__file__).resolve().parents[1]))
parser.add_argument('--output', required=True, help='A new, empty benchmark directory')
parser.add_argument('--files', type=int, default=1000)
parser.add_argument('--existing', type=int, default=10000)
parser.add_argument('--photos', type=int, default=24)
args = parser.parse_args()
sys.path.insert(0, str(Path(args.source_root).resolve()))

import numpy as np
from PIL import Image
from localmedia.db import Database
from localmedia.media import normalized
from localmedia.scanner import Scanner

output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=False)
source = output / 'files'
source.mkdir()
sample = source / '0.jpg'
Image.new('RGB', (16, 12), 'red').save(sample)
content = sample.read_bytes()
for i in range(args.files):
    path = source / f'{i}.jpg'
    path.write_bytes(content)
    os.utime(path, (time.time() - 20,) * 2)
db = Database(output / 'scan-catalog')
db.execute('INSERT INTO roots(id,path) VALUES (?,?)', ('r', normalized(source)))
db.execute("INSERT INTO roots(id,path,enabled) VALUES ('existing','synthetic-existing',0)")
with db.connect() as conn:
    conn.executemany("INSERT INTO media(id,root_id,path,parent,name,kind,size,hash,updated) VALUES (?,'existing',?,'synthetic-existing',?,'image',?,'synthetic',0)",
                    ((f'old-{i}', f'synthetic-existing/{i}.jpg',f'{i}.jpg',1000000+i) for i in range(args.existing)))
connections = [0]
original_connect = db.connect
@contextlib.contextmanager
def counted_connect():
    connections[0] += 1
    with original_connect() as conn:
        yield conn
db.connect = counted_connect
scanner = Scanner(db)
start = time.perf_counter()
scanner.scan()
import_seconds = time.perf_counter() - start
import_connections = connections[0]
connections[0] = 0
start = time.perf_counter()
scanner.scan()
rescan_seconds = time.perf_counter() - start
rescan_connections = connections[0]

photos = output / 'photos'
photos.mkdir()
rng = np.random.default_rng(7)
pixels = rng.integers(0, 256, (3200, 4800, 3), dtype=np.uint8)
first = photos / '0.jpg'
Image.fromarray(pixels).save(first, quality=90)
del pixels
os.utime(first, (time.time() - 20,) * 2)
for i in range(1, args.photos):
    os.link(first, photos / f'{i}.jpg')
preview_db = Database(output / 'preview-catalog')
preview_db.execute('INSERT INTO roots(id,path) VALUES (?,?)', ('r', normalized(photos)))
preview_scanner = Scanner(preview_db)
preview_scanner.scan()
columns = {r['name'] for r in preview_db.rows('PRAGMA table_info(media)')}
has_tiers = 'thumbnail_state' in columns
preview_scanner.preview_thread = threading.Thread(target=preview_scanner.preview_loop)
start = time.perf_counter()
preview_scanner.preview_thread.start()
first_visible = all_visible = all_standard = None
try:
    while time.perf_counter() - start < 180:
        rows = preview_db.rows('SELECT * FROM media')
        now = time.perf_counter() - start
        ready = sum(r['preview_state'] == 'ready' for r in rows)
        visible = sum(r['preview_state'] == 'ready' or (has_tiers and r['thumbnail_state'] == 'ready') for r in rows)
        if visible and first_visible is None:
            first_visible = now
        if visible == args.photos and all_visible is None:
            all_visible = now
        if ready == args.photos:
            all_standard = now
            break
        if any(r['preview_state'] == 'failed' for r in rows):
            raise RuntimeError('Synthetic preview failed')
        threading.Event().wait(.025)
    assert all_standard is not None, 'preview benchmark timed out'
finally:
    preview_scanner.close()
result = {
    'new_files':args.files, 'existing_records':args.existing,
    'scan_seconds':round(import_seconds,3), 'scan_connections':import_connections,
    'unchanged_rescan_seconds':round(rescan_seconds,3), 'rescan_connections':rescan_connections,
    'synthetic_jpegs':args.photos, 'resolution':[4800,3200],
    'first_visible_seconds':round(first_visible,3), 'all_visible_seconds':round(all_visible,3),
    'all_standard_seconds':round(all_standard,3), 'tiered':has_tiers,
    'scope':'Synthetic files; warm filesystem caches; no RAW/video, AI, or real-library throughput claim.',
}
(output / 'report.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(result,indent=2))
