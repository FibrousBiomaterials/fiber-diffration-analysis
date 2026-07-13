"""検出器画像の左右対称性から傾き補正角度を自動探索するモジュール。"""
from __future__ import annotations

from typing import Callable

import numpy as np
from scipy.ndimage import affine_transform

ProgressCallback = Callable[[int, int], None]


def rotate_around_center(
    image: np.ndarray, angle_deg: float, cx: float, cy: float, order: int = 3, cval: float = 0.0
) -> np.ndarray:
    """点(cx,cy)を中心に画像を面内回転する。"""
    a = np.deg2rad(angle_deg)
    matrix = np.array([[np.cos(a), np.sin(a)], [-np.sin(a), np.cos(a)]])
    center = np.array([cy, cx])
    offset = center - matrix @ center
    return affine_transform(image, matrix, offset=offset, order=order, mode="constant", cval=cval)


def symmetry_score(image: np.ndarray, cx: float, cy: float, y_max: int) -> float:
    """y = cy ~ y_max の範囲で左右対称度（相関係数）を返す。"""
    cy_int = int(round(cy))
    cx_int = int(round(cx))
    roi = image[cy_int:y_max, :]
    left = roi[:, :cx_int]
    right = roi[:, cx_int:][:, :cx_int][:, ::-1]
    w = min(left.shape[1], right.shape[1])
    if w <= 0:
        return -1.0
    l, r = left[:, -w:], right[:, -w:]
    mask = (l > 0) & (r > 0)
    if mask.sum() < 100:
        return -1.0
    lv, rv = l[mask].astype(float), r[mask].astype(float)
    lv -= lv.mean()
    rv -= rv.mean()
    denom = np.sqrt((lv ** 2).sum() * (rv ** 2).sum())
    return float(np.dot(lv, rv) / denom) if denom > 0 else -1.0


def search_tilt_angle(
    img: np.ndarray,
    cx: float,
    cy: float,
    downsample: int = 4,
    coarse_range: float = 5.0,
    coarse_step: float = 0.1,
    fine_span: float = 1.0,
    fine_step: float = 0.01,
    on_progress: ProgressCallback | None = None,
) -> dict:
    """粗探索→精密探索で対称化角度を求める。

    フル解像度だと1回の探索に数分かかるため、探索自体はダウンサンプリング画像で
    行い、角度(deg)のみを返す。角度はスケールに依存しないため、この結果を
    フル解像度の画像にそのまま適用できる。

    on_progress が渡された場合、各角度を評価するたびに (完了数, 総数) で呼ばれる。
    """
    img_log = np.log1p(np.clip(img.astype(float), 0, None))
    small = img_log[::downsample, ::downsample]
    cx_s, cy_s = cx / downsample, cy / downsample
    y_max_s = small.shape[0]

    coarse_angles = np.arange(-coarse_range, coarse_range + coarse_step / 2, coarse_step)
    fine_angles_len = len(np.arange(-fine_span, fine_span + fine_step / 2, fine_step))
    total_steps = len(coarse_angles) + fine_angles_len
    done = 0

    coarse_scores = []
    for ang in coarse_angles:
        coarse_scores.append(
            symmetry_score(rotate_around_center(small, ang, cx_s, cy_s, order=1), cx_s, cy_s, y_max_s)
        )
        done += 1
        if on_progress:
            on_progress(done, total_steps)
    best_coarse = float(coarse_angles[int(np.argmax(coarse_scores))])

    fine_angles = np.arange(best_coarse - fine_span, best_coarse + fine_span + fine_step / 2, fine_step)
    fine_scores = []
    for ang in fine_angles:
        fine_scores.append(
            symmetry_score(rotate_around_center(small, ang, cx_s, cy_s, order=3), cx_s, cy_s, y_max_s)
        )
        done += 1
        if on_progress:
            on_progress(min(done, total_steps), total_steps)
    angle_opt = float(fine_angles[int(np.argmax(fine_scores))])
    best_score = float(max(fine_scores))

    return {
        "angle_opt_deg": angle_opt,
        "score": best_score,
        "coarse_angle_deg": best_coarse,
        "coarse_score": float(max(coarse_scores)),
    }
