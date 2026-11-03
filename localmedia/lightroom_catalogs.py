"""Read-only catalog inspection and normal/full membership validation.

Catalog contents are changed only by Lightroom's own import/export commands.
This module never writes to an LR SQLite database or changes original files.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ntpath
from pathlib import Path
import sqlite3


def windows_path(value):
    return ntpath.normcase(ntpath.normpath(str(value)))


def hidden_path(path, roots):
    path = windows_path(path)
    return any(path == windows_path(root) or path.startswith(windows_path(root).rstrip('\\') + '\\')
               for root in roots)


@dataclass
class CatalogInventory:
    path: Path
    photos: dict[str, str]
    history_count: int
    snapshot_count: int
    digest: str


def inspect_catalog(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('目录文件不存在。')
    connection = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise ValueError('LR 目录完整性检查未通过。')
        rows = connection.execute('''SELECT i.id_global,r.absolutePath,d.pathFromRoot,f.idx_filename
            FROM Adobe_images i JOIN AgLibraryFile f ON f.id_local=i.rootFile
            JOIN AgLibraryFolder d ON d.id_local=f.folder
            JOIN AgLibraryRootFolder r ON r.id_local=d.rootFolder''').fetchall()
        photos = {str(ident).lower(): windows_path(ntpath.join(root, folder, filename))
                  for ident, root, folder, filename in rows}
        if len(photos) != connection.execute('SELECT count(*) FROM Adobe_images').fetchone()[0]:
            raise ValueError('部分照片无法对应到原文件记录，停止目录切换。')
        digest = hashlib.sha256()
        tables = ['Adobe_images', 'Adobe_libraryImageDevelopHistoryStep',
                  'Adobe_libraryImageDevelopSnapshot', 'Adobe_imageDevelopSettings',
                  'AgLibraryCollection', 'AgLibraryCollectionImage', 'AgLibraryKeyword',
                  'AgLibraryKeywordImage', 'AgPhotoProperty', 'AgSearchablePhotoProperty']
        available = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table not in available:
                continue
            digest.update(table.encode())
            for row in sorted(repr(tuple(r)) for r in connection.execute('SELECT * FROM "' + table + '"')):
                digest.update(row.encode()); digest.update(b'\n')
        return CatalogInventory(path, photos,
            connection.execute('SELECT count(*) FROM Adobe_libraryImageDevelopHistoryStep').fetchone()[0],
            connection.execute('SELECT count(*) FROM Adobe_libraryImageDevelopSnapshot').fetchone()[0],
            digest.hexdigest())
    finally:
        connection.close()


def verify_normal_full(normal, full, hidden_roots):
    if not hidden_roots:
        raise ValueError('尚未配置需要隐藏的目录。')
    private = {key for key, path in full.photos.items() if hidden_path(path, hidden_roots)}
    public = set(full.photos) - private
    leaked = {key for key, path in normal.photos.items() if hidden_path(path, hidden_roots)}
    if leaked:
        raise ValueError(f'普通目录仍包含 {len(leaked)} 个需要隐藏的条目，拒绝报告隐藏成功。')
    if set(normal.photos) != public:
        raise ValueError('普通目录与完整目录的普通照片范围不一致，需要先用 LR 导出/合并目录。')
    if any(normal.photos[key] != full.photos[key] for key in public):
        raise ValueError('两个目录中同一照片指向不同原文件，停止自动同步。')
    return {'normal_count': len(public), 'hidden_count': len(private), 'full_count': len(full.photos)}


def edit_records(path, selected=None):
    """Read stable photo metadata/history fields, ignoring catalog-local row IDs."""
    connection = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        records = {}
        columns = {r[1] for r in connection.execute('PRAGMA table_info(Adobe_images)')}
        fields = [f for f in ('rating', 'pick', 'colorLabels', 'orientation') if f in columns]
        images = connection.execute('SELECT id_local,id_global' + ''.join(',"' + f + '"' for f in fields) + ' FROM Adobe_images').fetchall()
        for row in images:
            ident, key = row[0], str(row[1]).lower()
            if selected is not None and key not in selected:
                continue
            def normalized(values):
                return tuple(float(v) if isinstance(v, (int, float)) else v for v in values)
            record = [normalized(row[2:])]
            for table in ('Adobe_libraryImageDevelopHistoryStep', 'Adobe_libraryImageDevelopSnapshot'):
                names = {r[1] for r in connection.execute('PRAGMA table_info("' + table + '")')}
                names = sorted(names - {'id_local', 'id_global', 'image', 'digest', 'snapshotID'})
                values = connection.execute('SELECT ' + ','.join('"'+n+'"' for n in names) + ' FROM "'+table+'" WHERE image=?', (ident,)).fetchall()
                record.append(sorted(repr(normalized(value)) for value in values))
            records[key] = hashlib.sha256(repr(record).encode()).hexdigest()
        return records
    finally:
        connection.close()
