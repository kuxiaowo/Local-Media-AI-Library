"""Read-only real-media preview verification and isolated 100k-record benchmark."""
import argparse
import json
import time
from pathlib import Path

import numpy as np

from localmedia.db import Database, profile
from localmedia.index import VectorIndex, normalize
from localmedia.media import make_preview


def benchmark(destination):
    db = Database(destination)
    count, dim = 100000, 1024
    rng = np.random.default_rng(7)
    space = profile(db.settings())
    if not db.one('SELECT id FROM roots'):
        with db.connect() as conn:
            conn.execute("INSERT INTO roots(id,path) VALUES ('r','benchmark')")
            for start in range(0,count,2000):
                conn.executemany('INSERT INTO media(id,root_id,path,parent,name,kind,sort_time,updated) VALUES (?,?,?,?,?,?,?,?)',
                    [(str(i),'r',f'benchmark/{i}.jpg','benchmark',f'{i}.jpg','image',i,0) for i in range(start,start+2000)])
                matrix = rng.normal(size=(2000,dim)).astype('float32')
                matrix /= np.linalg.norm(matrix,axis=1)[:,None]
                conn.executemany('INSERT INTO vectors VALUES (?,?,?,?,?)',
                    [(str(start+i),space,0,dim,row.tobytes()) for i,row in enumerate(matrix)])
    index = VectorIndex(db)
    query = normalize(rng.normal(size=dim))
    index.search(query,profile(db.settings()))
    times=[]
    for _ in range(5):
        start=time.perf_counter()
        index.search(query,profile(db.settings()))
        times.append(time.perf_counter()-start)
    lists=[]
    for _ in range(5):
        start=time.perf_counter()
        db.rows("SELECT id,name FROM media WHERE root_id='r' AND availability='online' ORDER BY sort_time DESC LIMIT 100 OFFSET 90000")
        db.one("SELECT COUNT(*) FROM media WHERE availability='online'")
        lists.append(time.perf_counter()-start)
    return {'records':count,'dimensions':dim,'warm_vector_seconds':times,'warm_list_seconds':lists,
            'note':'Synthetic vectors; model embedding request excluded. Not a semantic quality measurement.'}


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--benchmark',action='store_true')
    parser.add_argument('--samples')
    parser.add_argument('--output',default='work/acceptance')
    args=parser.parse_args()
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    result={}
    if args.benchmark:
        result['benchmark']=benchmark(output/'benchmark')
    if args.samples:
        result['samples']=[]
        for i,sample in enumerate(json.loads(Path(args.samples).read_text(encoding='utf-8-sig'))):
            path=Path(sample['FullName'])
            try:
                meta=make_preview(path,output/f'preview-{i}.jpg','video' if path.suffix.lower() in {'.mp4','.mov'} else 'image')
                result['samples'].append({'name':path.name,'metadata':meta,'status':'ready'})
            except Exception as exc:
                result['samples'].append({'name':path.name,'status':'failed','reason':type(exc).__name__})
    (output/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))
