"""与 OBot-ACM 保持一致的 ID 编码，以及缩略图生成。

`md5_to_base62` / `base62_to_md5` 必须与 `src/core/util/tools.py` 完全一致，
否则 Web 端展示的 ID 和 Bot 回复里的 ID 对不上。
"""

from __future__ import annotations

import hashlib
import io
import string
import warnings

from PIL import Image

from . import config

_BASE62_CHARSET = string.ascii_letters + string.digits
_CHAR_TO_INDEX = {char: index for index, char in enumerate(_BASE62_CHARSET)}

MD5_LENGTH = 32
GIF_SUFFIX = ".gif"


def md5_to_base62(md5_hash: str) -> str:
    """将 MD5 转换为 Base62 格式, 字符集[a-zA-Z0-9]。"""
    if len(md5_hash) != 32:
        raise ValueError("md5 must be a 32-char hex string")
    num = int(md5_hash, 16)
    if num == 0:
        return _BASE62_CHARSET[0]

    out = ""
    while num > 0:
        num, remainder = divmod(num, 62)
        out = _BASE62_CHARSET[remainder] + out
    return out


def base62_to_md5(base62_str: str) -> str:
    """将 Base62 转换为 MD5, 字符集[a-zA-Z0-9]。"""
    if not base62_str or len(base62_str) > 22:
        raise ValueError("base62 length must be within 1..22")
    invalid = [char for char in base62_str if char not in _CHAR_TO_INDEX]
    if invalid:
        raise ValueError(f"invalid base62 characters: {''.join(invalid)}")

    num = 0
    for char in base62_str:
        num = num * 62 + _CHAR_TO_INDEX[char]
    if num > (1 << 128) - 1:
        raise ValueError("base62 value exceeds the 128-bit md5 range")
    return format(num, "032x")


def is_md5(name: str) -> bool:
    """判断是否为 32 位小写十六进制（图片文件名即其 MD5）。"""
    if len(name) != MD5_LENGTH:
        return False
    return all(char in "0123456789abcdef" for char in name)


def md5_of_bytes(raw: bytes) -> str:
    return hashlib.md5(raw).hexdigest()


def hash_id_of(stem: str) -> str:
    """图片文件名（不含扩展名）-> 展示用 Base62 ID。"""
    return md5_to_base62(stem)


def new_legacy_entry(ocr_text: str, add_time: float) -> dict:
    """按 Bot 的旧版结构构造一条记录，保证写回后 Bot 完全兼容。"""
    return {
        "ocr_text": ocr_text,
        "add_time": add_time,
        "likes": 0,
        "comments": [],
        "pickup_times": 0,
    }


class ThumbnailError(Exception):
    """缩略图无法生成（图太大、格式坏了等），属于可预期的输入问题。"""


def make_thumbnail(raw: bytes, max_side: int = config.THUMB_MAX_SIDE) -> bytes:
    """把动图/静图压成一张 WebP 缩略图。

    像素数超出 config.THUMB_MAX_PIXELS 时抛 ThumbnailError 而不是 MemoryError：
    调用方据此返回 415，整个页面不会被一张坏图拖垮。
    """
    try:
        with warnings.catch_warnings():
            # Pillow 对「接近上限」的图只发警告，这里升级成错误一起拦住
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as img:
                # Check before pixel decoding, without altering Pillow globals used by other clients.
                if img.width * img.height > config.THUMB_MAX_PIXELS:
                    raise ThumbnailError(f"图片像素数超过上限（{config.THUMB_MAX_PIXELS}）")
                return _render_thumbnail(img, max_side)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ThumbnailError(f"图片像素数超过上限（{config.THUMB_MAX_PIXELS}）") from error


def _render_thumbnail(img: Image.Image, max_side: int) -> bytes:
    """把已经打开的图片渲染成 WebP 字节。

    帧是「处理一帧、缩放一帧、丢掉一帧」，而不是先把所有帧都以 RGBA 留在内存里：
    像素上限给的是解码预算，逐帧处理才不会在预算内把峰值内存翻好几倍。
    """
    size = (max_side, max_side)
    first = None
    canvas = None
    frame_count = 0

    try:
        total = min(getattr(img, "n_frames", 1), config.THUMB_MAX_FRAMES)
    except (EOFError, ValueError):
        total = 1

    for index in range(max(1, total)):
        try:
            img.seek(index)
            frame = img.convert("RGBA")
        except (EOFError, ValueError):
            break

        if index == 0:
            first = frame
            first.thumbnail(size, Image.LANCZOS)
        elif first is not None and frame.size != first.size:
            # 帧尺寸不一致（少见的 GIF 子矩形帧）：合并不了就只留首帧
            del frame
            break
        else:
            # 动图：把后续帧叠到一起，做成一张静态图，省掉 WebP 动画的兼容麻烦
            try:
                if canvas is None:
                    canvas = Image.new("RGBA", first.size, (0, 0, 0, 0))
                canvas = Image.alpha_composite(canvas, frame)
            except ValueError:
                del frame
                break
        frame_count = index + 1
        del frame

    if first is None:  # pragma: no cover - 头一帧都读不出来时按坏图处理
        raise ThumbnailError("无法解码图片的第一帧")

    if canvas is not None and frame_count > 1:
        # canvas 也是首帧的尺寸，缩到同一尺寸后按透明度混合
        canvas.thumbnail(size, Image.LANCZOS)
        first = Image.blend(first, canvas, 0.35)

    buffer = io.BytesIO()
    first.convert("RGBA").save(buffer, format="WEBP", quality=82, method=4)
    return buffer.getvalue()


def thumbnail_cache_key(path_text: str, mtime_ns: int, size: int) -> str:
    """缩略图缓存文件名，源文件变化时自动失效。"""
    raw = f"{path_text}|{mtime_ns}|{size}".encode()
    return hashlib.sha1(raw).hexdigest()
