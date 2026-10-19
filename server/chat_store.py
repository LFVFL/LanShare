"""聊天持久化：rooms.json 原子写 + 备份、图片落盘、旧数据迁移。"""
import base64
import json
import os
import re
import shutil
import threading
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path

from .config import (
    APP_BASE_DIR, CHAT_DATA_DIR, CHAT_IMAGE_DIR, CHAT_ROOMS_FILE_PATH,
    MAX_CHAT_IMAGE_BASE64_LEN, MAX_CHAT_THUMB_BASE64_LEN, ALLOWED_CHAT_IMAGE_EXTS,
)
from .paths import is_path_within
from .storage import chat_lock

CHAT_ROOMS = {}
CHAT_ROOMS_FILE = ""

# 新消息事件：SSE 连接平时在这里等，写入方 notify 一下就立刻醒。
# 和 chat_lock 分开，避免把「发消息」和「推消息」两件事的锁缠在一起。
chat_event = threading.Condition()


def notify_chat_event():
    """有新消息/房间变化时叫醒所有等待中的 SSE 连接。"""
    with chat_event:
        chat_event.notify_all()


def detect_image_ext(image_bytes):
    """
    根据文件头判断图片真实格式。
    防止伪造 data:image/png 但实际不是图片。
    """
    if not image_bytes:
        return None

    # PNG
    if image_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        return "png"

    # JPEG
    if image_bytes.startswith(b'\xff\xd8\xff'):
        return "jpg"

    # GIF
    if image_bytes.startswith(b'GIF87a') or image_bytes.startswith(b'GIF89a'):
        return "gif"

    # WEBP
    if image_bytes.startswith(b'RIFF') and len(image_bytes) >= 12 and image_bytes[8:12] == b'WEBP':
        return "webp"

    return None


def save_chat_base64_image(data_url, thumb=False, stem=None):
    """
    把聊天中的 Base64 图片保存为真实文件。
    JSON 里只保存返回的图片访问 URL，例如 /chat/image/xxx.png。

    thumb=True 时存成缩略图：文件名加 _t 后缀，体积上限按缩略图算。
    stem 给定时用它当文件名主体（缩略图会在此基础上再加 _t），
    这样缩略图能跟着原图一起被删掉。

    返回：
        成功："/chat/image/xxx.png"（缩略图是 "/chat/image/xxx_t.png"）
        失败：None
    """
    if not data_url or not isinstance(data_url, str):
        return None

    limit = MAX_CHAT_THUMB_BASE64_LEN if thumb else MAX_CHAT_IMAGE_BASE64_LEN
    if len(data_url) > limit:
        return None

    if not data_url.startswith("data:image/"):
        return None

    match = re.match(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.+)$", data_url, re.S)
    if not match:
        return None

    declared_ext = match.group(1).lower()
    base64_data = match.group(2)

    if declared_ext in ("jpeg", "pjpeg"):
        declared_ext = "jpg"

    try:
        image_bytes = base64.b64decode(base64_data, validate=True)
    except Exception:
        return None

    if not image_bytes:
        return None

    # 再次限制解码后的真实大小，避免 Base64 绕过
    # 这里大致按 Base64 限制反推，实际可按需调整
    max_real_image_bytes = int(limit * 0.75)
    if len(image_bytes) > max_real_image_bytes:
        return None

    real_ext = detect_image_ext(image_bytes)
    if not real_ext:
        return None

    if real_ext not in ALLOWED_CHAT_IMAGE_EXTS:
        return None

    # 以真实文件头判断出来的格式为准，不信任前端声明
    ext = real_ext

    stem = stem or f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    if thumb:
        stem += "_t"
    filename = f"{stem}.{ext}"
    file_path = CHAT_IMAGE_DIR / filename

    try:
        CHAT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(image_bytes)
    except OSError:
        return None

    return f"/chat/image/{filename}"


def delete_chat_image_file(image_url):
    """
    删除聊天消息对应的本地图片文件（含它的 _t 缩略图）。
    只处理 /chat/image/xxx 这种本程序保存的图片。
    """
    if not image_url or not image_url.startswith("/chat/image/"):
        return

    filename = image_url.replace("/chat/image/", "", 1)
    filename = urllib.parse.unquote(filename)
    filename = Path(filename).name

    if not filename:
        return

    image_path = (CHAT_IMAGE_DIR / filename).resolve()

    if not is_path_within(image_path, CHAT_IMAGE_DIR):
        return

    # 同名的 _t 缩略图一起删（扩展名可能不同：缩略图统一存 jpg），免得留孤儿文件
    targets = [image_path]
    if not image_path.stem.endswith("_t"):
        targets.extend(CHAT_IMAGE_DIR.glob(image_path.stem + "_t.*"))

    for target in targets:
        try:
            target.unlink(missing_ok=True)
        except OSError as e:
            print(f"[Chat] 删除图片失败: {target} - {e}")


def thumb_url_for(image_url):
    """/chat/image/a.png → /chat/image/a_t.png；不是本程序的图片返回空串。"""
    if not image_url or not image_url.startswith("/chat/image/"):
        return ""
    stem, dot, ext = image_url.rpartition(".")
    if not dot or "/" in ext:
        return ""
    return stem + "_t." + ext


# 缩略图补生成（给老消息用）：Pillow 是可选依赖，没装就什么都不做
try:
    from PIL import Image
except ImportError:
    Image = None


def ensure_thumb(image_url, max_side=320, quality=72, max_bytes=20 * 1024 * 1024):
    """老消息只有原图时按需补一张缩略图；返回缩略图 URL，补不了返回空串。

    没装 Pillow、原图过大、图片损坏都只是返回空串（前端会自动退回用原图）。
    """
    if Image is None or not image_url or not image_url.startswith("/chat/image/"):
        return ""

    filename = urllib.parse.unquote(image_url.replace("/chat/image/", "", 1))
    filename = Path(filename).name
    if not filename:
        return ""

    src = (CHAT_IMAGE_DIR / filename).resolve()
    if not is_path_within(src, CHAT_IMAGE_DIR) or not src.is_file():
        return ""

    thumb_path = src.with_name(src.stem + "_t.jpg")
    if thumb_path.is_file() and thumb_path.stat().st_size > 0:
        return f"/chat/image/{thumb_path.name}"

    try:
        if src.stat().st_size > max_bytes:
            return ""
        with Image.open(src) as im:
            im = im.convert("RGB")          # PNG 的透明通道铺白，否则存 JPEG 会报错
            im.thumbnail((max_side, max_side), Image.LANCZOS)
            CHAT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
            im.save(thumb_path, "JPEG", quality=quality, optimize=True)
    except Exception as e:
        print(f"[Chat] 补缩略图失败 {src.name}: {e}")
        return ""

    return f"/chat/image/{thumb_path.name}"


def cleanup_chat_images(grace_seconds=600, dry_run=False):
    """清理聊天图片目录里的孤儿文件。

    规则都很保守，宁可少删：
      1. `xxx_t.jpg` 找不到对应原图 `xxx.*` → 删（原图没了，缩略图残留）；
      2. 原图没被任何房间的任何消息引用 → 连它的 `_t` 一起删；
      3. 只处理 mtime 早于 grace_seconds 的文件，避免删掉刚上传/正在发的。

    返回 {'removed': 个数, 'bytes': 释放字节, 'kept': 保留个数}。
    """
    if not CHAT_IMAGE_DIR.is_dir():
        return {'removed': 0, 'bytes': 0, 'kept': 0}

    with chat_lock:
        referenced = set()
        for room in CHAT_ROOMS.values():
            for msg in room.get('messages', []):
                for key in ('image', 'thumb'):
                    url = msg.get(key) or ''
                    if url.startswith('/chat/image/'):
                        referenced.add(Path(url).name)

    referenced_stems = {Path(n).stem for n in referenced}
    deadline = time.time() - grace_seconds
    removed = freed = kept = 0

    try:
        files = [p for p in CHAT_IMAGE_DIR.iterdir() if p.is_file()]
    except OSError:
        return {'removed': 0, 'bytes': 0, 'kept': 0}
    existing_stems = {p.stem for p in files}

    for path in files:
        try:
            if path.stat().st_mtime > deadline:      # 太新：可能正在用
                kept += 1
                continue
        except OSError:
            continue

        is_thumb = path.stem.endswith('_t')
        if is_thumb:
            if path.stem[:-2] in existing_stems:
                kept += 1
                continue
            reason = 'orphan thumb'
        else:
            if path.stem in referenced_stems:
                kept += 1
                continue
            reason = 'unreferenced'

        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        if not dry_run:
            try:
                path.unlink()
            except OSError as e:
                print(f"[Chat] 清理失败 {path.name}: {e}")
                continue
            removed += 1
            freed += size
            print(f"[Chat] 清理孤儿图片（{reason}）：{path.name}")
            if not is_thumb:
                for t in CHAT_IMAGE_DIR.glob(path.stem + '_t.*'):
                    try:
                        freed += t.stat().st_size
                    except OSError:
                        pass
                    try:
                        t.unlink()
                        removed += 1
                    except OSError:
                        pass
        else:
            removed += 1
            freed += size

    if removed:
        print("[Chat] 图片清理完成：删除 %d 个，释放 %.1f KB，保留 %d 个"
              % (removed, freed / 1024, kept))
    return {'removed': removed, 'bytes': freed, 'kept': kept}


def get_chat_rooms_file():
    """
    获取聊天室数据文件路径。
    优先使用程序目录下 chat_data/rooms.json。
    如果创建失败，则回退到用户目录。
    """
    try:
        CHAT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        CHAT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        return str(CHAT_ROOMS_FILE_PATH)
    except Exception:
        app_data = Path.home() / ".lan_cloud_share"
        app_data.mkdir(parents=True, exist_ok=True)
        return str(app_data / "chat_rooms.json")


def load_chat_rooms():
    global CHAT_ROOMS, CHAT_ROOMS_FILE
    CHAT_ROOMS_FILE = get_chat_rooms_file()

    def _read(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    try:
        if os.path.exists(CHAT_ROOMS_FILE):
            try:
                data = _read(CHAT_ROOMS_FILE)
            except (json.JSONDecodeError, IOError, OSError) as e:
                print(f"[Chat] 主文件损坏，尝试备份: {e}")
                bak = CHAT_ROOMS_FILE + '.bak'
                data = _read(bak) if os.path.exists(bak) else None
                if isinstance(data, dict):
                    print(f"[Chat] 已从备份恢复 {len(data)} 个聊天室")
            if isinstance(data, dict):
                CHAT_ROOMS = data
                print(f"[Chat] 已加载 {len(CHAT_ROOMS)} 个聊天室")
                return
    except (json.JSONDecodeError, IOError, OSError) as e:
        print(f"[Chat] 加载聊天室失败: {e}")
    CHAT_ROOMS = {}


def save_chat_rooms():
    """
    原子写：先写 .tmp 再 os.replace。
    直接 'w' 覆盖的话，写到一半断电/崩溃就把 rooms.json 变成半截 JSON，
    下次启动 json.load 失败 → 所有聊天记录凭空消失。
    """
    if not CHAT_ROOMS_FILE:
        return
    target = Path(CHAT_ROOMS_FILE)
    tmp = target.with_suffix(target.suffix + '.tmp')
    try:
        with chat_lock:
            payload = json.dumps(CHAT_ROOMS, ensure_ascii=False, indent=2)
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, 'w', encoding='utf-8') as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        # 留一份上一版，json 损坏时还能回退
        if target.exists():
            try:
                shutil.copyfile(target, target.with_suffix(target.suffix + '.bak'))
            except OSError:
                pass
        os.replace(tmp, target)
    except (IOError, OSError) as e:
        print(f"[Chat] 保存聊天室失败: {e}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


class ChatRoomSaver:
    def __init__(self, delay=5.0):
        self.delay = delay
        self._timer = None
        self._lock = threading.Lock()

    def mark_dirty(self):
        with self._lock:
            if self._timer is None:
                self._timer = threading.Timer(self.delay, self._do_save)
                self._timer.daemon = True
                self._timer.start()

    def _do_save(self):
        with self._lock:
            self._timer = None
        save_chat_rooms()

    def flush(self):
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
        save_chat_rooms()


def migrate_old_chat():
    """
    迁移旧版单聊天室聊天记录。
    修复点：
    1. 即使旧文件为空列表，也会正确创建 default 房间；
    2. 无论迁移是否成功，都保证 default 房间存在；
    3. 不会出现打印已创建但实际没有创建的情况。
    """
    global CHAT_ROOMS

    if 'default' in CHAT_ROOMS:
        return

    old_chat_file = Path.home() / ".lan_cloud_share" / "chat_history.json"

    if not old_chat_file.exists():
        alt = APP_BASE_DIR / "chat_history.json"
        old_chat_file = alt if alt.exists() else None

    migrated = False

    if old_chat_file:
        try:
            with open(old_chat_file, 'r', encoding='utf-8') as f:
                messages = json.load(f)

            if isinstance(messages, list):
                CHAT_ROOMS['default'] = {
                    'id': 'default',
                    'name': '默认群聊',
                    'messages': messages,
                    'created': time.time()
                }
                migrated = True

                try:
                    os.remove(old_chat_file)
                except OSError:
                    pass

                print(f"[Chat] 已迁移 {len(messages)} 条历史消息到默认房间")

        except (json.JSONDecodeError, IOError, OSError) as e:
            print(f"[Chat] 迁移旧数据失败: {e}")

    if not migrated:
        CHAT_ROOMS['default'] = {
            'id': 'default',
            'name': '默认群聊',
            'messages': [],
            'created': time.time()
        }
        print("[Chat] 已创建默认聊天室")

    save_chat_rooms()

chat_room_saver = ChatRoomSaver(delay=5.0)
load_chat_rooms()
migrate_old_chat()

# 启动时顺手清一次图片目录（只删"确定没人用"的孤儿文件，见函数注释）
try:
    cleanup_chat_images()
except Exception as e:
    print(f"[Chat] 启动清理图片失败（忽略）: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════
