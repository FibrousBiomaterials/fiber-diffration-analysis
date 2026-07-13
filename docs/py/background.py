"""極座標リビニング + ベイズ背景推定 + 平滑化を行うモジュール。

instensity_resrict_polar.ipynb セル17 [3][4][5] の移植:
  rebin_to_polar      : 傾き補正 + 極座標(phi, r)へのリビニング
  sonneveld_init      : 移動最小値フィルタによる背景初期推定
  bayesian_background : 1トレースごとのベイズ重み付きスプライン背景推定
  smooth_background   : 背景の補間 + 2Dガウシアン平滑化
"""
from __future__ import annotations

from typing import Callable

import numpy as np
from scipy.interpolate import LSQUnivariateSpline
from scipy.ndimage import gaussian_filter, minimum_filter1d, uniform_filter1d
from scipy.stats import binned_statistic_2d

from tilt import rotate_around_center

ProgressCallback = Callable[[int, int], None]


def rebin_to_polar(
    img_corr: np.ndarray,
    raw_img: np.ndarray,
    cx: float,
    cy: float,
    angle_deg: float,
    blind_mask: np.ndarray | None,
    n_phi: int,
    n_r: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """補正済み画像を傾き補正してから極座標(phi,r)にリビニングする。

    不感領域マスクは生画像から作る(補正後の小さい実シグナルや cubic 補間の
    負リンギングを巻き込まないため)。画像は cubic、マスクは最近傍で別々に回す。
    """
    # ブラウザ内実行(Pyodide/WASM)のメモリ制約に配慮し、使い終わった大きな配列は
    # 都度 del で解放する(演算内容・順序は元のまま、結果は完全に同一)。
    ny, nx = img_corr.shape
    phi_bins = np.linspace(0, 360, n_phi + 1)
    r_bins = np.linspace(0, n_r, n_r + 1)

    bad_mask = raw_img <= 0
    if blind_mask is not None:
        bad_mask = bad_mask | blind_mask

    img_rot = rotate_around_center(img_corr, angle_deg, cx, cy, order=3, cval=0.0)
    bad_rot = rotate_around_center(bad_mask.astype(float), angle_deg, cx, cy, order=0, cval=1.0) > 0.5
    del bad_mask

    yy, xx = np.mgrid[0:ny, 0:nx]
    dx = xx - cx
    dy = yy - cy
    del yy, xx
    r_px = np.sqrt(dx ** 2 + dy ** 2)
    phi = np.rad2deg(np.arctan2(dy, dx)) % 360.0
    del dx, dy

    val = img_rot.ravel()
    del img_rot
    good = np.isfinite(val) & (~bad_rot.ravel())
    del bad_rot
    phi_g = phi.ravel()[good]
    del phi
    r_g = r_px.ravel()[good]
    del r_px
    val_g = val[good]
    del val, good

    polar_mean, _, _, _ = binned_statistic_2d(phi_g, r_g, val_g, statistic="mean", bins=[phi_bins, r_bins])
    polar_std, _, _, _ = binned_statistic_2d(phi_g, r_g, val_g, statistic="std", bins=[phi_bins, r_bins])
    polar_count, _, _, _ = binned_statistic_2d(phi_g, r_g, val_g, statistic="count", bins=[phi_bins, r_bins])

    return polar_mean, polar_std, polar_count


def sonneveld_init(intensity: np.ndarray, window: int = 150) -> np.ndarray:
    """移動最小値フィルタ + 平滑化による背景初期推定。"""
    bg = minimum_filter1d(intensity.astype(float), size=window, mode="nearest")
    bg = uniform_filter1d(bg, size=window // 2, mode="nearest")
    return bg


def bayesian_background(
    intensity: np.ndarray,
    std: np.ndarray | None = None,
    grid_spacing: int = 150,
    n_iter: int = 50,
    sigma_scale: float = 0.1,
    window: int = 150,
    r_min: int = 125,
) -> np.ndarray:
    """1本のアジマストレースのベイズ重み付きスプライン背景推定。

    diff = I - bg が正(ピーク側)の点の重みを exp で抑制し、背景がピークに
    引っ張られないようにする。
    """
    n = len(intensity)
    r = np.arange(n, dtype=float)
    if std is None:
        std = np.sqrt(np.clip(intensity, 1, None))

    valid = np.isfinite(intensity) & np.isfinite(std) & (std > 0) & (r >= r_min)
    if valid.sum() < grid_spacing * 2:
        return np.zeros(n)

    r_v, I_v, std_v = r[valid], intensity[valid], std[valid]
    bg = sonneveld_init(I_v, window=window)
    bg_best = bg.copy()

    knots = np.arange(r_v[0] + grid_spacing, r_v[-1] - grid_spacing, grid_spacing)
    if len(knots) < 2:
        bg_full = np.zeros(n)
        bg_full[valid] = bg_best
        return bg_full

    weights = 1.0 / np.clip(std_v, 1e-10, None)

    for _ in range(n_iter):
        diff = I_v - bg
        bg_scale = np.clip(bg, 1.0, None)
        suppress = np.where(diff > 0, np.exp(-0.5 * (diff / (sigma_scale * bg_scale)) ** 2), 1.0)
        suppress = np.clip(suppress, 1e-6, 1.0)
        w = np.clip(weights * suppress, 1e-10, None)

        try:
            spl = LSQUnivariateSpline(r_v, I_v, t=knots, w=w, k=3, ext=3)
            bg_new = spl(r_v)
        except Exception:
            continue

        bg_new = np.clip(np.minimum(bg_new, I_v), 0, None)
        if np.nanmax(bg_new) > np.nanmax(I_v):
            continue
        bg_best = bg_new.copy()

        if np.max(np.abs(bg_new - bg)) < 1e-3:
            bg = bg_new
            break
        bg = bg_new

    bg_full = np.zeros(n)
    bg_full[valid] = bg_best
    return bg_full


def smooth_background(
    polar_bg: np.ndarray,
    polar_mean_disp: np.ndarray,
    n_phi: int,
    n_r: int,
    sigma: tuple[float, float] = (6, 10),
) -> tuple[np.ndarray, np.ndarray]:
    """背景マップの無効点(0/nan)を phi方向(周期的)→r方向の順に線形補間で埋め、
    2Dガウシアンで平滑化する。最後に nan領域・実測値超えをクリップする。
    """
    polar_bg_filled = polar_bg.copy()
    phi_idx = np.arange(n_phi)
    for j in range(n_r):
        col = polar_bg[:, j]
        valid = (col > 0) & np.isfinite(col)
        if valid.sum() < 2:
            continue
        valid_ext = np.concatenate([phi_idx[valid] - n_phi, phi_idx[valid], phi_idx[valid] + n_phi])
        col_ext = np.concatenate([col[valid], col[valid], col[valid]])
        col_filled = np.interp(phi_idx, valid_ext, col_ext)
        polar_bg_filled[:, j] = np.where(valid, col, col_filled)

    polar_bg_filled2 = polar_bg_filled.copy()
    r_idx = np.arange(n_r)
    for i in range(n_phi):
        row = polar_bg_filled[i]
        valid = (row > 0) & np.isfinite(row)
        if valid.sum() < 2:
            continue
        polar_bg_filled2[i] = np.where(valid, row, np.interp(r_idx, r_idx[valid], row[valid]))

    polar_bg_smooth = gaussian_filter(polar_bg_filled2, sigma=sigma, mode=("wrap", "nearest"))

    nan_mask = np.isnan(polar_mean_disp)
    polar_bg_smooth[nan_mask] = np.nan
    polar_bg_smooth = np.minimum(polar_bg_smooth, np.where(nan_mask, np.inf, polar_mean_disp))
    polar_bg_smooth = np.maximum(polar_bg_smooth, 0)
    polar_sub_smooth = np.clip(polar_mean_disp - polar_bg_smooth, 0, None)
    return polar_bg_smooth, polar_sub_smooth


def run_background_removal(
    img_corr: np.ndarray,
    raw_img: np.ndarray,
    cx: float,
    cy: float,
    angle_deg: float,
    blind_mask: np.ndarray,
    n_phi: int = 360,
    n_r: int = 2000,
    r_min: int = 125,
    grid_spacing: int = 150,
    n_iter: int = 50,
    sigma_scale: float = 0.1,
    window: int = 150,
    smooth_sigma: tuple[float, float] = (6, 10),
    on_progress: ProgressCallback | None = None,
) -> dict:
    """[3]極座標リビニング→[4]ベイズ背景推定(全トレース)→[5]補間・平滑化 をまとめて実行する。"""
    polar_mean, polar_std, polar_count = rebin_to_polar(
        img_corr, raw_img, cx, cy, angle_deg, blind_mask, n_phi, n_r
    )

    polar_bg = np.zeros_like(polar_mean)
    for i in range(n_phi):
        bg = bayesian_background(
            polar_mean[i], std=polar_std[i], grid_spacing=grid_spacing,
            n_iter=n_iter, sigma_scale=sigma_scale, window=window, r_min=r_min,
        )
        polar_bg[i] = bg
        if on_progress:
            on_progress(i + 1, n_phi)

    polar_mean_disp = polar_mean.copy()
    polar_mean_disp[:, :r_min] = np.nan

    polar_bg_smooth, polar_sub_smooth = smooth_background(
        polar_bg, polar_mean_disp, n_phi, n_r, sigma=smooth_sigma
    )

    return {
        "polar_mean": polar_mean_disp,
        "polar_count": polar_count,
        "polar_bg_smooth": polar_bg_smooth,
        "polar_sub_smooth": polar_sub_smooth,
    }
