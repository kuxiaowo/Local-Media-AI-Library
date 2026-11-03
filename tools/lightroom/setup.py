"""Prepare local config, plugin copy and shortcuts; never registers/edits LR prefs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from localmedia.lightroom import CatalogPair
from localmedia.lightroom_bridge import catalog_id


def lua(value):
    # Lua 5.1 supports decimal byte escapes, not JSON's Unicode escapes.
    return '"' + ''.join(chr(b) if 32 <= b < 127 and b not in (34, 92) else '\\%03d' % b
                         for b in str(value).encode('utf-8')) + '"'


def prepare(path, port=18766):
    path = path.resolve()
    pair = CatalogPair.load(path)
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    if pair.hidden_roots:
        from localmedia.lightroom_catalogs import inspect_catalog, verify_normal_full, edit_records
        workspace = Path(config['catalog_workspace']).resolve()
        if not pair.normal.parent.is_relative_to(workspace) or pair.normal.parent == workspace:
            raise ValueError('普通目录必须放在 catalog_workspace 的专用子文件夹中。')
        marker = workspace / '.localmedia-catalog-workspace'
        if not marker.exists():
            full, normal = inspect_catalog(pair.alternate), inspect_catalog(pair.normal)
            verify_normal_full(normal, full, pair.hidden_roots)
            if edit_records(pair.normal) != edit_records(pair.alternate, set(normal.photos)):
                raise ValueError('初始化前请用 LR 原生导出普通目录，两个目录的编辑不一致。')
            marker.write_text('LocalMedia normal/full catalog workspace', encoding='utf-8')
            (workspace/'state.json').write_text(json.dumps(dict(last_mode='full', full_digest=full.digest,
                                                              normal_digest=normal.digest)), encoding='utf-8')
    config.setdefault('ai', {})
    for number, mode in enumerate(('normal', 'alternate')):
        base = path.parent / ('ai-' + mode)
        identity = catalog_id(getattr(pair, mode))
        if mode in config['ai'] and config['ai'][mode]['catalog_id'] != identity:
            # A newly bound catalog must not expose the preceding catalog's AI data.
            base = base / identity[:12]
            del config['ai'][mode]
        config['ai'].setdefault(mode, dict(catalog_id=identity, token=secrets.token_hex(32),
                                         port=port + number, data=str(base/'database'), inbox=str(base/'renders')))
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    plugin = path.parent / 'LocalMedia.lrplugin'
    shutil.copytree(ROOT/'plugins/LocalMedia.lrplugin', plugin, dirs_exist_ok=True)
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    launcher = ROOT/'tools/lightroom/launcher.py'
    entries = []
    for mode in ('normal', 'alternate'):
        item = config['ai'][mode]
        entries.append('['+lua(str(getattr(pair, mode)).replace('\\','/').lower())+']={'+
                       ','.join(k+'='+lua(v) for k,v in dict(mode=mode, catalog_id=item['catalog_id'],
                           token=item['token'], url='http://127.0.0.1:'+str(item['port'])).items())+'}')
    (plugin/'Config.lua').write_text('return {python='+lua(pythonw)+',launcher='+lua(launcher)+
        ',config='+lua(path)+',catalogMode='+lua(config.get('catalog_mode','separate'))+
        ',hiddenRoots={'+','.join(lua(str(p)) for p in pair.hidden_roots)+'},catalogs={'+','.join(entries)+'}}', encoding='utf-8')
    for label, args in [('图库切换', []), ('隐藏指定照片', ['--switch','normal']), ('显示全部照片', ['--switch','alternate'])]:
        command = subprocess.list2cmdline([str(pythonw), str(launcher), '--config', str(path), *args])
        (path.parent/(label+'.vbs')).write_text('CreateObject("WScript.Shell").Run "'+command.replace('"','""')+'", 0, False\r\n', encoding='utf-16')
    return plugin


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',required=True,type=Path)
    parser.add_argument('--port',default=18766,type=int)
    args=parser.parse_args()
    print(prepare(args.config,args.port))
