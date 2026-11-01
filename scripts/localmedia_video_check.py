"""Read-only real-video extraction and local model smoke test in an isolated library."""
import asyncio
import json
import sys
from pathlib import Path

from localmedia.db import Database
from localmedia.media import normalized
from localmedia.scanner import Scanner
from localmedia.tasks import Tasks
from localmedia.worker import Worker


async def main():
    path=Path(sys.argv[1]).resolve()
    db=Database(Path('work/video-acceptance'))
    if not db.one('SELECT id FROM roots'):
        db.execute('INSERT INTO roots(id,path) VALUES (?,?)',('r',normalized(path.parent)))
    scanner=Scanner(db);scanner.visit('r',path)
    media=db.one('SELECT * FROM media WHERE path=?',(normalized(path),));scanner.preview(media)
    db.set_setting('model','qwen3.5:4b');db.set_setting('summary_model','qwen3.5:4b')
    db.set_setting('embedding_model','bge-m3:567m');db.set_setting('request_timeout',180)
    tasks=Tasks(db);worker=Worker(db,tasks)
    task=db.one("SELECT * FROM tasks WHERE media_id=? AND state='running'",(media['id'],))
    if not task:
        tasks.create([media['id']],True)
        task=tasks.claim()
    try:
        await worker.analyze(task)
        result=db.one('SELECT description,analysis_state,metadata FROM media WHERE id=?',(media['id'],))
        result['checkpoint']=json.loads(db.one('SELECT checkpoint FROM tasks WHERE id=?',(task['id'],))['checkpoint'])
        (db.directory/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(result['analysis_state'],result['description'])
    finally:
        await worker.close()


asyncio.run(main())
