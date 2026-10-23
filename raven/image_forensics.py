# -*- coding: utf-8 -*-
"""图像篡改痕迹取证（轻量版）。

定位：不是再造一个 FUSED，而是做**上线前的那一层粗筛**——
在把图片送进深度取证模型之前，用纯 CPU、毫秒级的统计特征把"明显二次编辑过"的图挑出来，
并且给出块级定位图供人工复核。这也是我复现 FUSED 之后一直想补的另一半：
论文给的是精度上限，工程要的是成本和可解释性。

三类痕迹（都是压缩域/残差域的经典手法，不含学习型检测器）：
    1. ELA（Error Level Analysis）：以固定质量重压缩后求差，误差分布不均 = 局部历史不同；
    2. 噪声残差一致性：高通滤波后的块间方差，拼接区域噪声模型常与背景不一致；
    3. JPEG Ghost：多质量重压缩取最小误差，二次压缩区域在特定质量下误差骤降。
"""
import io
import os

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

BLOCK = 32
QUALITY_PROBE = (70, 80, 90)


# ---------------------------------------------------------------- 基础算子
def _to_arr(img: Image.Image):
    return np.asarray(img.convert("RGB"), dtype=np.float32)


def _jpeg_roundtrip(arr: np.ndarray, quality: int) -> np.ndarray:
    buf = io.BytesIO()
    Image.fromarray(arr.astype(np.uint8)).save(buf, format="JPEG", quality=quality)
    buf.seek(0)
    return _to_arr(Image.open(buf))


def ela_map(arr: np.ndarray, quality: int = 90) -> np.ndarray:
    re = _jpeg_roundtrip(arr, quality)
    diff = np.abs(arr - re).mean(axis=2)
    return diff


def ghost_map(arr: np.ndarray, quals=QUALITY_PROBE) -> np.ndarray:
    errs = [np.abs(arr - _jpeg_roundtrip(arr, q)).mean(axis=2) for q in quals]
    return np.minimum.reduce(errs)


def noise_residual(arr: np.ndarray) -> np.ndarray:
    """高通残差：灰度图减去 3x3 中值。中值滤波对噪声不敏感，减完剩下的主要是噪声与细节。"""
    gray = arr.mean(axis=2)
    pad = np.pad(gray, 1, mode="edge")
    stack = np.stack([
        pad[0:-2, 0:-2], pad[0:-2, 1:-1], pad[0:-2, 2:],
        pad[1:-1, 0:-2], pad[1:-1, 1:-1], pad[1:-1, 2:],
        pad[2:, 0:-2], pad[2:, 1:-1], pad[2:, 2:],
    ])
    med = np.median(stack, axis=0)
    return gray - med


def gradient_map(arr: np.ndarray) -> np.ndarray:
    gray = arr.mean(axis=2)
    gx = np.zeros_like(gray)
    gy = np.zeros_like(gray)
    gx[:, 1:-1] = gray[:, 2:] - gray[:, :-2]
    gy[1:-1, :] = gray[2:, :] - gray[:-2, :]
    return np.abs(gx) + np.abs(gy)


def blocks(m: np.ndarray, size: int = BLOCK) -> np.ndarray:
    """裁到整块网格后做块均值池化"""
    h, w = m.shape
    h2, w2 = h // size * size, w // size * size
    m = m[:h2, :w2]
    return m.reshape(h2 // size, size, w2 // size, size).mean(axis=(1, 3))


def _p99_over_med(b: np.ndarray) -> float:
    med = float(np.median(b)) + 1e-6
    return float(np.percentile(b, 99) / med)


def _cv(b: np.ndarray) -> float:
    mu = float(b.mean()) + 1e-6
    return float(b.std() / mu)


def _tail_ratio(b: np.ndarray, k=5.0) -> float:
    med = float(np.median(b)) + 1e-6
    return float((b > k * med).mean())


def _local_resid(b: np.ndarray) -> np.ndarray:
    p = np.pad(b, 1, mode="edge")
    stack = np.stack([p[i:i + b.shape[0], j:j + b.shape[1]]
                      for i in range(3) for j in range(3)])
    return b - np.median(stack, axis=0)


def _local_contrast(b: np.ndarray) -> float:
    """局部异常对比度：块图减去 3x3 邻域中值后的最大值 / 全局标准差。

    这是本模块最有用的一个特征之一。篡改区域通常只占画面的百分之几，
    用 p99、均值这类**全局统计**会被背景稀释掉；先做邻域平滑再取残差峰值，
    等于只问"有没有一块地方跟它周围不像"，对小区域敏感得多。
    """
    return float(_local_resid(b).max() / (float(b.std()) + 1e-6))


def _local_dip(b: np.ndarray) -> float:
    """反向对比：邻域中值减去自身的最小值（"某块比周围更干净"）。
    重采样/模糊/低质量素材拼进来的区域，噪声能量会**下降**，属于这一类异常。"""
    return float((- _local_resid(b)).max() / (float(b.std()) + 1e-6))


def _dip_area(b: np.ndarray) -> float:
    med = float(np.median(b)) + 1e-6
    dip = np.clip(med - b, 0, None)
    return float(dip.mean() / med)


FEATURE_NAMES = [
    "ela_p99_med", "ela_local_contrast", "ela_local_dip", "ela_tail", "ela_cv",
    "noise_cv", "noise_p99_med", "noise_local_drop", "noise_dip_area",
    "ghost_p99_med", "ghost_local_contrast",
    "edge_err_corr", "green_ratio",
]


def extract(img: Image.Image):
    """返回 (特征向量, 特征字典, 定位图)"""
    arr = _to_arr(img)
    ela = ela_map(arr)
    gho = ghost_map(arr)
    noi = np.abs(noise_residual(arr))
    grad = gradient_map(arr)

    eb, gb, nb, rb = blocks(ela), blocks(gho), blocks(noi), blocks(grad)
    nmed = float(np.median(nb)) + 1e-6
    feats = {
        "ela_p99_med": _p99_over_med(eb),
        "ela_local_contrast": _local_contrast(eb),
        "ela_local_dip": _local_dip(eb),
        "ela_tail": _tail_ratio(eb, 4.0),
        "ela_cv": _cv(eb),
        "noise_cv": _cv(nb),
        "noise_p99_med": _p99_over_med(nb),
        # 重采样/贴图会让局部噪声能量**下降**：最小块/中位块 越小越可疑
        "noise_local_drop": float(np.min(nb) / nmed),
        "noise_dip_area": _dip_area(nb),
        "ghost_p99_med": _p99_over_med(gb),
        "ghost_local_contrast": _local_contrast(gb),
        "edge_err_corr": _pearson(rb.ravel(), eb.ravel()),
        # 强水平跳变比例（贴图边缘的粗线索）
        "green_ratio": float(np.mean(np.abs(np.diff(arr.mean(axis=2), axis=1)) > 24)),
    }
    vec = np.array([feats[k] for k in FEATURE_NAMES], dtype=np.float32)

    # 定位图：噪声"凹陷"（重采样/低质素材） + ELA 局部残差（压缩历史不一致）
    def _norm(m):
        return (m - m.min()) / (m.max() - m.min() + 1e-6)
    dip_n = _norm(np.clip(float(np.median(nb)) - nb, 0, None))
    ela_r = _norm(np.clip(_local_resid(eb), 0, None))
    heat = 0.65 * dip_n + 0.35 * ela_r
    return vec, feats, heat


def _pearson(a, b):
    a = np.asarray(a, float)
    b = np.asarray(b, float)
    a = a - a.mean()
    b = b - b.mean()
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9
    return float(np.dot(a, b) / denom)


def localize(heat: np.ndarray, topk_ratio=0.1):
    """取热度最高的 k% 块作为"疑似篡改区域"，返回块坐标列表"""
    flat = heat.ravel()
    k = max(1, int(len(flat) * topk_ratio))
    idx = np.argsort(flat)[-k:]
    h, w = heat.shape
    return [(int(i // w), int(i % w)) for i in idx]


def iou_with_bbox(heat: np.ndarray, bbox):
    """定位命中度：预测块集合与真实篡改框的交集 / 预测块面积。
    简化指标，比 IoU 宽松，但对"能不能把人工视线引到正确区域"够用。"""
    x0, y0, x1, y1 = bbox
    h, w = heat.shape
    bw, bh = 480 / w, 480 / h
    pred = set(localize(heat, 0.1))
    hit = sum(1 for (r, c) in pred if x0 <= c * bw + bw / 2 <= x1 and y0 <= r * bh + bh / 2 <= y1)
    return hit / max(1, len(pred))


# ---------------------------------------------------------------- 造图
_FONT_CACHE = {}


def _font(size=22):
    if size in _FONT_CACHE:
        return _FONT_CACHE[size]
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc",
              r"C:\Windows\Fonts\simhei.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size)
                _FONT_CACHE[size] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default()
    _FONT_CACHE[size] = f
    return f


def make_poster(seed: int, size=480) -> Image.Image:
    """程序化生成一张"社区海报"：渐变底 + 几何色块 + 标题 + 文字 + 颗粒。
    目的是让图像里有足够的纹理/边缘/平坦区，三类痕迹才有可测的落差。"""
    rng = np.random.default_rng(seed)
    y = np.linspace(0, 1, size)[:, None]
    x = np.linspace(0, 1, size)[None, :]
    c1 = rng.integers(30, 220, 3).astype(np.float32)
    c2 = rng.integers(30, 220, 3).astype(np.float32)
    bg = c1[None, None, :] * (1 - y[..., None]) + c2[None, None, :] * y[..., None]
    bg = np.repeat(bg, size, axis=1)
    img = Image.fromarray(np.clip(bg, 0, 255).astype(np.uint8))

    d = ImageDraw.Draw(img)
    for _ in range(rng.integers(3, 7)):
        w = int(rng.integers(40, 200))
        h = int(rng.integers(40, 200))
        px = int(rng.integers(0, size - w))
        py = int(rng.integers(0, size - h))
        col = tuple(int(v) for v in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            d.rectangle([px, py, px + w, py + h], fill=col)
        else:
            d.ellipse([px, py, px + w, py + h], fill=col)

    title = rng.choice(["活动公告", "版本更新", "招募队友", "限定福利", "赛事速报", "同人企划"])
    d.text((24, 24), title, font=_font(34), fill=(255, 255, 255))
    for i in range(int(rng.integers(2, 5))):
        d.text((26, 90 + i * 34), rng.choice([
            "全新内容已上线，欢迎体验",
            "组队开黑，长期稳定在线",
            "本次活动限时至本周日",
            "欢迎留言交流，理性讨论",
        ]), font=_font(20), fill=(245, 245, 245))

    # 颗粒噪声（模拟手机拍摄/导出）
    arr = np.asarray(img, dtype=np.float32)
    arr += rng.normal(0, 3.0, arr.shape)
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(
        ImageFilter.GaussianBlur(0.4))


def tamper(img: Image.Image, seed: int, mode: str, donor: Image.Image = None):
    """返回 (篡改图, bbox)。

    刻意还原真实编辑流程里**会留下痕迹**的那些动作，否则这个任务就是假的：
    - copy-move：拷贝后缩放再放回（模拟"复制一块放大/缩小"），重采样会压低局部高频；
    - splice：素材来自一张**低质量来源图**（先按 Q=55 存一遍再裁），压缩历史与底图不同；
    - 两者都做轻微亮度偏移 + 羽化，再整体另存为 Q=92，形成"局部历史不一致 + 全局二次压缩"。
    """
    rng = np.random.default_rng(seed)
    size = img.size[0]
    w = int(rng.integers(70, 130))
    h = int(rng.integers(70, 130))
    sx = int(rng.integers(0, size - w - 1))
    sy = int(rng.integers(0, size - h - 1))

    if mode == "copy_move":
        patch = img.crop((sx, sy, sx + w, sy + h))
        # 缩放往返 -> 重采样，模拟尺寸调整
        s = float(rng.uniform(0.78, 0.92))
        patch = patch.resize((max(8, int(w * s)), max(8, int(h * s))), Image.BILINEAR)
        patch = patch.resize((w, h), Image.BICUBIC)
        patch = patch.filter(ImageFilter.GaussianBlur(0.7))
    else:
        donor = donor or make_poster(seed + 999, size)
        buf = io.BytesIO()
        donor.save(buf, format="JPEG", quality=55)   # 低质量来源 -> 残留强压缩块效应
        buf.seek(0)
        donor_q = Image.open(buf).convert("RGB")
        patch = donor_q.crop((sx, sy, sx + w, sy + h))

    # 亮度/色偏微调，避免"贴上去就一模一样"这种不真实情形
    pa = np.asarray(patch, dtype=np.float32)
    pa = pa * float(rng.uniform(0.92, 1.08)) + float(rng.uniform(-8, 8))
    patch = Image.fromarray(np.clip(pa, 0, 255).astype(np.uint8))

    tx = int(rng.integers(0, size - w - 1))
    ty = int(rng.integers(0, size - h - 1))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rectangle([6, 6, w - 6, h - 6], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(3))
    out = img.copy()
    out.paste(patch, (tx, ty), mask)
    return out, (tx, ty, tx + w, ty + h)


def save_jpeg(img: Image.Image, path: str, quality=88):
    img.save(path, format="JPEG", quality=quality)


def load(path: str) -> Image.Image:
    return Image.open(path).convert("RGB")
