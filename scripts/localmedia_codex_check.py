import asyncio
import json
from pathlib import Path
from localmedia.providers import Codex


async def main():
    directory=Path('work/codex-check').resolve();directory.mkdir(exist_ok=True)
    codex=Codex(cwd=str(directory))
    try:
        account=await codex.rpc('account/read',{'refreshToken':False})
        models=await codex.rpc('model/list',{})
        limits=await codex.rpc('account/rateLimits/read')
        safe={'auth_type':(account.get('account') or {}).get('type'),
              'models':[{'model':m['model'],'inputModalities':m.get('inputModalities')} for m in models['data']],
              'limits':limits}
        (directory/'status.json').write_text(json.dumps(safe,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(safe,ensure_ascii=False,indent=2))
    finally:
        await codex.close()


asyncio.run(main())
