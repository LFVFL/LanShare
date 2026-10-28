"""Bounded binary image uploads. Original bytes never pass through JSON/base64."""
import os
import re
import shutil
import threading
import time
import uuid
import warnings
from pathlib import Path

from .config import (
    CHAT_IMAGE_DIR, CHAT_UPLOAD_DIR, CHAT_IMAGE_MAX_BYTES,
    CHAT_MEDIA_CHUNK_BYTES, CHAT_MEDIA_TEMP_BYTES, CHAT_MEDIA_MIN_FREE_BYTES,
    CHAT_MEDIA_MAX_UPLOADS, CHAT_MEDIA_EXPIRE_SECONDS, CHAT_MEDIA_MAX_PIXELS,
)

try:
    from PIL import Image, ImageOps
except ImportError:
    Image = ImageOps = None

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except ImportError:
    pass

try:
    import pillow_avif  # noqa: F401 - registers an optional Pillow decoder
except ImportError:
    pass


class MediaError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


_uploads = {}
_lock = threading.Lock()
_decode_slots = threading.BoundedSemaphore(2)
_FORMATS = {
    'PNG': 'png', 'JPEG': 'jpg', 'GIF': 'gif', 'WEBP': 'webp',
    'BMP': 'bmp', 'TIFF': 'tiff', 'ICO': 'ico', 'AVIF': 'avif',
    'HEIF': 'heic', 'HEIC': 'heic',
}


def _remove_files(session):
    session['path'].unlink(missing_ok=True)
    if session.get('media') and not session.get('used'):
        targets = set()
        for key in ('image', 'thumb', 'preview'):
            url = session['media'].get(key)
            if url:
                targets.add(CHAT_IMAGE_DIR / Path(url).name)
        stem = Path(session['media']['image']).stem
        targets.update(CHAT_IMAGE_DIR.glob(stem + '_t.*'))
        targets.update(CHAT_IMAGE_DIR.glob(stem + '_p.*'))
        for target in targets:
            target.unlink(missing_ok=True)


def pending_media_names():
    with _lock:
        stems = {Path(s['media']['image']).stem for s in _uploads.values()
                 if s.get('media') and not s.get('used') and not s.get('cancelled')}
    names = set()
    for stem in stems:
        names.update(path.name for path in CHAT_IMAGE_DIR.glob(stem + '.*'))
        names.update(path.name for path in CHAT_IMAGE_DIR.glob(stem + '_t.*'))
        names.update(path.name for path in CHAT_IMAGE_DIR.glob(stem + '_p.*'))
    return names


def cleanup_uploads():
    now = time.time()
    with _lock:
        stale = [(key, s) for key, s in _uploads.items()
                 if now - s['touched'] > CHAT_MEDIA_EXPIRE_SECONDS]
        for key, session in stale:
            if session['lock'].acquire(blocking=False):
                try:
                    session['cancelled'] = True
                    _remove_files(session)
                    _uploads.pop(key, None)
                finally:
                    session['lock'].release()
    if CHAT_UPLOAD_DIR.is_dir():
        with _lock:
            active = {s['path'].name for s in _uploads.values()}
        for path in CHAT_UPLOAD_DIR.glob('*.part'):
            try:
                if path.name not in active and now - path.stat().st_mtime > CHAT_MEDIA_EXPIRE_SECONDS:
                    path.unlink(missing_ok=True)
            except OSError:
                pass


def capabilities():
    formats = []
    if Image is not None:
        Image.init()
        formats = [name for name in _FORMATS if name in Image.OPEN]
    return {
        'success': True, 'chunk_bytes': CHAT_MEDIA_CHUNK_BYTES,
        'max_bytes': CHAT_IMAGE_MAX_BYTES, 'temp_bytes': CHAT_MEDIA_TEMP_BYTES,
        'formats': formats, 'available': Image is not None,
        'max_pixels': CHAT_MEDIA_MAX_PIXELS,
    }


def start_upload(owner, room, name, size):
    if Image is None:
        raise MediaError('图片服务缺少 Pillow，请安装 requirements.txt 中的依赖。', 503)
    if not isinstance(name, str) or not name or len(name) > 255:
        raise MediaError('图片文件名无效')
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise MediaError('图片大小无效')
    if Path(name).suffix.lower() in ('.svg', '.svgz'):
        raise MediaError('不支持 SVG 聊天图片，请先导出为 PNG/JPEG；SVG 不会被内联执行。', 415)
    if CHAT_IMAGE_MAX_BYTES and size > CHAT_IMAGE_MAX_BYTES:
        raise MediaError('图片超过服务器配置的单图资源上限', 413)
    cleanup_uploads()
    CHAT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        reserved = sum(s['size'] for s in _uploads.values() if not s.get('used'))
        if len(_uploads) >= CHAT_MEDIA_MAX_UPLOADS:
            raise MediaError('正在处理的图片较多，请稍后重试', 503)
        if CHAT_MEDIA_TEMP_BYTES and reserved + size > CHAT_MEDIA_TEMP_BYTES:
            raise MediaError('图片临时空间配额不足，请取消其他上传或调整服务器配置', 507)
        if shutil.disk_usage(CHAT_UPLOAD_DIR).free - size - reserved < CHAT_MEDIA_MIN_FREE_BYTES:
            raise MediaError('服务器磁盘剩余空间不足', 507)
        token = uuid.uuid4().hex
        path = CHAT_UPLOAD_DIR / (token + '.part')
        path.touch(exist_ok=False)
        _uploads[token] = {
            'owner': owner, 'room': room, 'name': name, 'size': size,
            'offset': 0, 'path': path, 'touched': time.time(),
            'lock': threading.Lock(), 'media': None, 'used': False,
            'cancelled': False,
        }
    return {'success': True, 'upload': token, 'offset': 0, 'chunk_bytes': CHAT_MEDIA_CHUNK_BYTES}


def _session(token, owner):
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{32}', token):
        raise MediaError('图片上传凭据无效', 404)
    with _lock:
        session = _uploads.get(token)
    if session is None or session['owner'] != owner:
        raise MediaError('图片上传不存在或不属于当前会话', 404)
    if time.time() - session['touched'] > CHAT_MEDIA_EXPIRE_SECONDS:
        raise MediaError('图片上传已过期，请重新选择图片', 410)
    return session


def _check_active(session):
    if session.get('cancelled'):
        raise MediaError('图片上传已取消', 404)
    if time.time() - session['touched'] > CHAT_MEDIA_EXPIRE_SECONDS:
        raise MediaError('图片上传已过期，请重新选择图片', 410)


def append_chunk(token, owner, offset, stream, length):
    session = _session(token, owner)
    with session['lock']:
        _check_active(session)
        if session['media'] or session['used']:
            raise MediaError('图片已经处理完成', 409)
        if offset != session['offset']:
            raise MediaError('图片上传位置不一致，请重试', 409)
        if not 0 < length <= CHAT_MEDIA_CHUNK_BYTES or offset + length > session['size']:
            raise MediaError('图片分片大小无效', 413)
        if shutil.disk_usage(CHAT_UPLOAD_DIR).free - length < CHAT_MEDIA_MIN_FREE_BYTES:
            raise MediaError('服务器磁盘剩余空间不足', 507)
        written = 0
        try:
            with session['path'].open('r+b') as target:
                target.seek(offset)
                while written < length:
                    block = stream.read(min(65536, length - written))
                    if not block:
                        raise MediaError('图片分片传输中断，请重试', 400)
                    target.write(block)
                    written += len(block)
        except (OSError, MediaError):
            with session['path'].open('r+b') as target:
                target.truncate(offset)
            raise
        session['offset'] += written
        session['touched'] = time.time()
        return {'success': True, 'offset': session['offset']}


def _render_image(source, stem, name, size):
    paths = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(source) as image:
                ext = _FORMATS.get(image.format)
                if not ext:
                    raise MediaError('不支持该图片格式，请转为 PNG/JPEG/WebP 后发送', 415)
                width, height = image.size
                if CHAT_MEDIA_MAX_PIXELS and width * height > CHAT_MEDIA_MAX_PIXELS:
                    raise MediaError('图片像素超过服务器解码保护上限，请降低分辨率后发送', 413)
                # Decode before publication; magic bytes alone are not an image validator.
                image.seek(0)
                # Resize before copying/orienting; JPEG draft decoding can avoid a
                # full-resolution allocation and PNG needs only one large buffer.
                image.thumbnail((1920, 1920), Image.Resampling.LANCZOS)
                image.load()
                rendered = ImageOps.exif_transpose(image)
                if rendered.mode in ('RGBA', 'LA') or 'transparency' in rendered.info:
                    rgba = rendered.convert('RGBA')
                    canvas = Image.new('RGB', rgba.size, 'white')
                    canvas.paste(rgba, mask=rgba.getchannel('A'))
                    rendered = canvas
                else:
                    rendered = rendered.convert('RGB')
                CHAT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                original = CHAT_IMAGE_DIR / (stem + '.' + ext)
                thumb = CHAT_IMAGE_DIR / (stem + '_t.jpg')
                preview = CHAT_IMAGE_DIR / (stem + '_p.jpg')
                paths.extend((original, thumb, preview))
                rendered.save(preview, 'JPEG', quality=88, optimize=True)
                rendered.thumbnail((320, 320), Image.Resampling.LANCZOS)
                rendered.save(thumb, 'JPEG', quality=75, optimize=True)
            # Multi-frame decoders release their source only after leaving the context.
            os.replace(source, original)
            return {
                'image': '/chat/image/' + original.name,
                'thumb': '/chat/image/' + thumb.name,
                # Native browser formats keep animation/original-resolution lightboxes.
                'preview': '/chat/image/' + (original.name if ext in ('png', 'jpg', 'gif', 'webp') else preview.name),
                'name': Path(name).name, 'size': size,
                'width': width, 'height': height, 'format': ext,
            }
    except Exception as error:
        for path in paths:
            path.unlink(missing_ok=True)
        if isinstance(error, MediaError):
            raise
        raise MediaError('图片无法安全解码。HEIC/AVIF 需对应 Pillow 插件；SVG、损坏图片不支持。', 415) from error


def complete_upload(token, owner):
    session = _session(token, owner)
    with session['lock']:
        _check_active(session)
        if session['used']:
            raise MediaError('图片已经发送', 409)
        if session['media']:
            return {'success': True, 'media': token, **session['media']}
        if session['offset'] != session['size']:
            raise MediaError('图片尚未上传完成', 409)
        if not _decode_slots.acquire(blocking=False):
            raise MediaError('服务器正在处理其他图片，请稍后重试', 503)
        try:
            session['media'] = _render_image(session['path'], token, session['name'], session['size'])
        finally:
            _decode_slots.release()
        session['touched'] = time.time()
        return {'success': True, 'media': token, **session['media']}


def take_media(token, owner, room):
    session = _session(token, owner)
    with session['lock']:
        _check_active(session)
        if session['room'] != room or not session['media'] or session['used']:
            raise MediaError('图片不可用，请重新上传', 409)
        session['used'] = True
        session['touched'] = time.time()
        return dict(session['media'])


def cancel_upload(token, owner):
    session = _session(token, owner)
    with session['lock']:
        session['cancelled'] = True
        _remove_files(session)
        with _lock:
            _uploads.pop(token, None)
    return {'success': True}


def finish_media(token, owner):
    """Once attached to a persisted message only the message owns these files."""
    with _lock:
        session = _uploads.get(token)
        if session and session['owner'] == owner and session['used']:
            _uploads.pop(token, None)
