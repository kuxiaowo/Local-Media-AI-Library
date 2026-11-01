from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageCms, ImageOps

IMAGES = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.gif', '.heic', '.heif', '.arw', '.nef', '.sr2', '.dng', '.cr2', '.cr3', '.jxr'}
VIDEOS = {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.mts', '.m2ts', '.3gp', '.mod', '.mpg', '.mpeg', '.wmv'}
VIDEO_PREVIEW_SECOND = 10.0
VIDEO_DECODER_MODES = {'cpu', 'intel', 'nvidia', 'intel_nvidia', 'cpu_intel_nvidia'}
QSV_DECODERS = {
    'av1': 'av1_qsv',
    'h264': 'h264_qsv',
    'hevc': 'hevc_qsv',
    'mjpeg': 'mjpeg_qsv',
    'mpeg2video': 'mpeg2_qsv',
    'vc1': 'vc1_qsv',
    'vp9': 'vp9_qsv',
}


def video_decode_options(mode, codec=None):
    """Return FFmpeg input options for the requested video decoder."""
    if mode not in VIDEO_DECODER_MODES:
        raise ValueError('未知视频解码方式')
    if mode == 'nvidia':
        # CUDA chooses the matching NVDEC decoder and downloads frames when a
        # following software filter (such as scene detection) needs them.
        return ['-hwaccel', 'cuda']
    if mode == 'intel':
        # Explicit QSV decoders return software frames to this filter graph.
        # Generic -hwaccel qsv leaves hardware surfaces behind and the CPU
        # scene filter cannot consume them without a format-specific download.
        decoder = QSV_DECODERS.get(codec or '')
        return ['-c:v', decoder] if decoder else ['-hwaccel', 'qsv']
    return []


def video_decoder_candidates(mode):
    """Return physical decoders participating in a configured mode."""
    if mode not in VIDEO_DECODER_MODES:
        raise ValueError('未知视频解码方式')
    if mode == 'intel_nvidia':
        return ['nvidia', 'intel']
    if mode == 'cpu_intel_nvidia':
        return ['nvidia', 'intel', 'cpu']
    return [mode]


def normalized(path):
    return os.path.normcase(str(Path(path).absolute()))


def file_hash(path, cancelled=None):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            if cancelled and cancelled():
                raise InterruptedError('hash cancelled')
            digest.update(block)
    return digest.hexdigest()


def ffmpeg(name='ffmpeg'):
    value = shutil.which(name)
    if not value:
        raise RuntimeError(f'未找到 {name}，请安装并加入 PATH')
    return value


def open_image(path):
    suffix = Path(path).suffix.lower()
    if suffix in {'.heic', '.heif'}:
        import pillow_heif
        pillow_heif.register_heif_opener()
    if suffix in {'.arw', '.nef', '.sr2', '.dng', '.cr2', '.cr3'}:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            try:
                thumb = raw.extract_thumb()
                image = Image.open(io.BytesIO(thumb.data)) if thumb.format == rawpy.ThumbFormat.JPEG else Image.fromarray(thumb.data)
                image.load()
                return image
            except (rawpy.LibRawNoThumbnailError, rawpy.LibRawUnsupportedThumbnailError):
                image = Image.fromarray(raw.postprocess(use_camera_wb=True, output_bps=8))
                image.info['raw_decoded'] = True
                return image
    image = Image.open(path)
    image.load()
    return image


def photo_metadata(image):
    exif = image.getexif()
    try:
        exif = {**dict(exif), **dict(exif.get_ifd(34665))}
    except (KeyError, TypeError):
        exif = dict(exif)
    result = {}
    for name, tag in [('camera', 272), ('make', 271), ('lens', 42036), ('aperture', 33437),
                      ('shutter', 33434), ('iso', 34855), ('captured_at', 36867)]:
        value = exif.get(tag)
        if value is not None:
            result[name] = str(Fraction(float(value)).limit_denominator(100000)) if name=='shutter' else str(value)
    try:
        gps = image.getexif().get_ifd(34853)
        def degrees(value):
            return float(value[0]) + float(value[1]) / 60 + float(value[2]) / 3600
        result['latitude'] = degrees(gps[2]) * (-1 if gps[1] == 'S' else 1)
        result['longitude'] = degrees(gps[4]) * (-1 if gps[3] == 'W' else 1)
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        pass
    return result


def make_preview(path, destination, kind, video_decoder='cpu'):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if kind == 'video':
        probe = subprocess.run([ffmpeg('ffprobe'), '-v', 'error', '-show_format', '-show_streams',
                                '-of', 'json', str(path)], capture_output=True, timeout=30, check=True)
        data = json.loads(probe.stdout)
        stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
        if stream is None:
            raise RuntimeError('文件中没有可解码的视频轨道')
        duration = float(data.get('format', {}).get('duration', 0))
        codec = stream.get('codec_name')
        meta = {'width': stream['width'], 'height': stream['height'],
                'duration': duration,
                'codec': codec,
                'captured_at': stream.get('tags', {}).get('creation_time') or data.get('format', {}).get('tags', {}).get('creation_time')}
        # Prefer the tenth second so title cards and opening fades are less
        # likely to become the gallery cover. Short videos use their midpoint.
        seek = VIDEO_PREVIEW_SECOND if duration <= 0 or duration > VIDEO_PREVIEW_SECOND else max(duration / 2, 0)
        def command_at(position, mode):
            return [ffmpeg(), '-v', 'error', '-ss', f'{position:g}',
                    *video_decode_options(mode, codec), '-i', str(path), '-frames:v', '1',
                    '-vf', 'scale=1600:1600:force_original_aspect_ratio=decrease', '-y', str(destination)]
        def decode(position):
            candidates = video_decoder_candidates(video_decoder)
            # CPU remains the final compatibility fallback for hardware-only
            # selections, but is also a normal participant in the all-device
            # mode.
            if 'cpu' not in candidates:
                candidates.append('cpu')
            last_error = None
            for mode in candidates:
                try:
                    subprocess.run(command_at(position, mode), capture_output=True, timeout=60, check=True)
                    return
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                    last_error = error
            raise last_error
        try:
            decode(seek)
        except subprocess.CalledProcessError:
            if seek == 0:
                raise
            decode(0)
        return meta
    with open_image(path) as original:
        meta = photo_metadata(original)
        raw_dimensions = None
        if Path(path).suffix.lower() in {'.arw', '.nef', '.sr2', '.dng', '.cr2', '.cr3'}:
            import exifread
            with open(path, 'rb') as raw_file:
                tags = exifread.process_file(raw_file, details=False)
            for name, tag in [('camera','Image Model'),('make','Image Make'),('lens','EXIF LensModel'),
                              ('aperture','EXIF FNumber'),('shutter','EXIF ExposureTime'),('iso','EXIF ISOSpeedRatings'),
                              ('captured_at','EXIF DateTimeOriginal')]:
                if tag in tags:
                    meta[name] = str(tags[tag])
            orientation = tags.get('Image Orientation')
            if orientation and not original.info.get('raw_decoded'):
                original.getexif()[274] = int(orientation.values[0])
            width, height = tags.get('EXIF ExifImageWidth'), tags.get('EXIF ExifImageLength')
            if width and height:
                raw_dimensions = (int(width.values[0]),int(height.values[0]))
                if orientation and int(orientation.values[0]) in {5,6,7,8}:
                    raw_dimensions = raw_dimensions[::-1]
            try:
                def coordinate(tag):
                    values = tags[tag].values
                    return float(values[0])+float(values[1])/60+float(values[2])/3600
                meta['latitude'] = coordinate('GPS GPSLatitude') * (-1 if str(tags['GPS GPSLatitudeRef'])=='S' else 1)
                meta['longitude'] = coordinate('GPS GPSLongitude') * (-1 if str(tags['GPS GPSLongitudeRef'])=='W' else 1)
            except (KeyError,TypeError,ValueError,ZeroDivisionError):
                pass
        image = ImageOps.exif_transpose(original)
        meta.update(width=image.width, height=image.height)
        if raw_dimensions:
            meta.update(width=raw_dimensions[0],height=raw_dimensions[1])
        icc = image.info.get('icc_profile')
        if icc:
            try:
                image = ImageCms.profileToProfile(image, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                                                 ImageCms.createProfile('sRGB'), outputMode='RGB')
            except (OSError, ImageCms.PyCMSError):
                image = image.convert('RGB')
        else:
            image = image.convert('RGB')
        image.thumbnail((2000, 2000))
        image.save(destination, 'JPEG', quality=90)
        return meta


def sort_timestamp(meta, fallback):
    value = meta.get('captured_at')
    if value:
        for parser in [lambda s: datetime.strptime(s, '%Y:%m:%d %H:%M:%S'),
                       lambda s: datetime.fromisoformat(s.replace('Z', '+00:00'))]:
            try:
                return parser(value).timestamp()
            except (ValueError, TypeError):
                pass
    return fallback
