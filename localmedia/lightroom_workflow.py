"""Normal/full catalog transitions through LR-native operations only."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import time
import uuid

from .lightroom_catalogs import hidden_path, inspect_catalog, verify_normal_full, edit_records
from .lightroom_native import NativeCatalogs


def bundle(catalog):
    return [catalog, Path(str(catalog) + '-data'), Path(str(catalog) + '-wal'), Path(str(catalog) + '-shm'),
            catalog.with_name(catalog.stem + ' Previews.lrdata'),
            catalog.with_name(catalog.stem + ' Smart Previews.lrdata'),
            catalog.with_name(catalog.stem + ' Helper.lrdata')]


def copy_item(source, destination):
    if source.is_dir():
        shutil.copytree(source, destination)
    elif source.exists():
        shutil.copy2(source, destination)


def restore_normal(exported, normal, workspace):
    """Publish a validated export, retaining the whole previous normal bundle."""
    exported, normal, workspace = exported.resolve(), normal.resolve(), workspace.resolve()
    if not normal.parent.is_relative_to(workspace) or normal.parent == workspace:
        raise ValueError('普通目录必须放在专用管理目录的子文件夹中。')
    if not exported.parent.is_relative_to(workspace) or exported.parent == normal.parent:
        raise ValueError('导出目录不在管理区域中，或与普通目录重叠。')
    if any(not path.resolve().is_relative_to(workspace) for path in bundle(normal) + bundle(exported)):
        raise ValueError('目录附属文件指向管理区域外，停止发布。')
    if exported.stem != normal.stem or Path(str(normal) + '.lock').exists():
        raise ValueError('普通目录名称不匹配或仍被 LR 打开。')
    backup = workspace / 'backups' / uuid.uuid4().hex
    backup.mkdir(parents=True)
    moved, installed = [], []
    try:
        for old in bundle(normal):
            if old.exists():
                old.rename(backup / old.name)
                moved.append(old)
        for source, destination in zip(bundle(exported), bundle(normal)):
            if source.exists():
                # Move an owned, completed export; no recursive deletion.
                source.rename(destination)
                installed.append(destination)
    except Exception:
        recovery = workspace / 'recovery' / uuid.uuid4().hex
        recovery.mkdir(parents=True)
        for path in installed:
            path.rename(recovery / path.name)
        for old in moved:
            (backup / old.name).rename(old)
        raise
    return str(backup)


def switch_normal_full(session, target, config, native=None):
    pair = session.pair
    if not pair.hidden_roots:
        raise ValueError('未启用普通/完整目录模式。')
    workspace = Path(config['catalog_workspace']).resolve()
    if not (workspace / '.localmedia-catalog-workspace').is_file():
        raise ValueError('目录管理区域尚未初始化。')
    if target not in (pair.normal, pair.alternate):
        raise ValueError('目标目录未配置。')
    current = session.current()
    state_file = workspace / 'state.json'
    state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
    started = time.monotonic()
    native = native or NativeCatalogs(session)
    before_full = inspect_catalog(pair.alternate)
    before_normal = inspect_catalog(pair.normal)
    if any(hidden_path(path, pair.hidden_roots) for path in before_normal.photos.values()):
        raise ValueError('普通目录中发现需要隐藏的照片，必须先重新创建普通目录。')
    if current == target:
        if target == pair.alternate and state.get('last_mode') == 'normal':
            raise RuntimeError('完整目录已打开，但上次同步尚未确认完成；请先核对编辑和备份。')
        return {'ok': True, 'already_open': True, 'seconds': 0.0}
    result = {'ok': False, 'already_open': False}
    if target == pair.normal:
        if current != pair.alternate:
            raise RuntimeError('请先打开完整目录，再通过 LR 菜单隐藏指定照片。')
        # The SDK menu selected all public photos immediately before this call.
        destination = workspace / 'exports' / uuid.uuid4().hex / pair.normal.stem
        destination.parent.mkdir(parents=True)
        native.export_selected(destination, allow_all=not any(hidden_path(path, pair.hidden_roots) for path in before_full.photos.values()))
        exported = destination / (pair.normal.stem + '.lrcat')
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            try:
                counts = verify_normal_full(inspect_catalog(exported), before_full, pair.hidden_roots)
                break
            except Exception:
                time.sleep(.5)
        else:
            raise RuntimeError('LR 普通目录导出未完成或范围不匹配，保留完整目录。')
        session.close(pair.alternate, timeout=300, on_wait=native.backup_if_prompted)
        # Recheck after LR has flushed all export/catalog sidecars and exited.
        counts = verify_normal_full(inspect_catalog(exported), inspect_catalog(pair.alternate), pair.hidden_roots)
        result['backup'] = restore_normal(exported, pair.normal, workspace)
        state = {'last_mode': 'normal', 'full_digest': inspect_catalog(pair.alternate).digest,
                 'normal_digest': inspect_catalog(pair.normal).digest}
        state_file.write_text(json.dumps(state), encoding='utf-8')
        session.open(pair.normal)
        result.update(counts)
    else:
        if current not in (pair.normal, None):
            raise RuntimeError('当前不是配置的普通目录。')
        if state.get('full_digest') != before_full.digest:
            raise RuntimeError('完整目录在普通模式期间发生变化，需先核对冲突；没有覆盖任何编辑。')
        if current:
            session.close(pair.normal, timeout=300, on_wait=native.backup_if_prompted)
        changed = inspect_catalog(pair.normal).digest != state.get('normal_digest')
        if changed:
            backup = workspace / 'backups' / ('full-' + uuid.uuid4().hex)
            backup.mkdir(parents=True)
            for source in bundle(pair.alternate)[:4]:
                copy_item(source, backup / source.name)
            result['backup'] = str(backup)
        session.open(pair.alternate)
        if changed:
            # The process changed during open; construct controls for its new PID.
            expected = edit_records(pair.normal)
            private_ids = {key for key,path in before_full.photos.items() if hidden_path(path, pair.hidden_roots)}
            private_before = edit_records(pair.alternate, private_ids)
            # The window can appear before LR finishes startup. Wait for the
            # application to respond before issuing a single import command.
            time.sleep(3)
            NativeCatalogs(session).import_catalog(pair.normal)
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                try:
                    counts = verify_normal_full(inspect_catalog(pair.normal), inspect_catalog(pair.alternate), pair.hidden_roots)
                    # Membership can match before the merge finishes. Keep the
                    # native dialog closed and let LR finish its own transaction.
                    if (edit_records(pair.alternate, set(expected)) == expected
                            and edit_records(pair.alternate, private_ids) == private_before
                            and session.user.IsWindowEnabled(NativeCatalogs(session).main())):
                        break
                except Exception:
                    pass
                time.sleep(.5)
            else:
                raise RuntimeError('LR 合并尚未确认完成，已保留两个目录和完整目录备份。')
        counts = verify_normal_full(inspect_catalog(pair.normal), inspect_catalog(pair.alternate), pair.hidden_roots)
        state['last_mode'] = 'full'
        state_file.write_text(json.dumps(state), encoding='utf-8')
        result.update(counts)
    result.update(ok=True, seconds=time.monotonic() - started)
    return result
