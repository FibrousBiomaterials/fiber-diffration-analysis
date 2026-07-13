"""極座標(背景差し引き済み)画像から反射強度を抽出するブロックフィット(NNLS)モジュール。

以下の処理を行う:
  反射位置の4回対称展開(±p1, phi, 180-phi, phi+180, 360-phi)と重複除去
          (配向幅Bの算出は peakwidth.py で行う)
  ピーク形状関数(動径ローレンツ×方位角ガウス)とw_function
  全対称等価反射のリスト作成(R0, phi0, gamma_R, sigma_phi)
  設計行列の構築(ピーク列 + 平板背景列)
  ブロックごとのNNLSフィット
  経験的な強度誤差の見積もり
  計算パターンの再構成
"""
from __future__ import annotations

import math
from typing import Callable

import numpy as np
from scipy.optimize import nnls
from scipy.stats import chi2

from peakwidth import calc_sigma_from_hkl

ProgressCallback = Callable[[int, int], None]


# =============================================================================
# ピーク形状関数
# =============================================================================

def wrap_pixel(dphi: np.ndarray, n_phi: int) -> np.ndarray:
    """ピクセル単位の角度差を周期境界を考慮して [-n_phi/2, n_phi/2) に巻きつける。"""
    return ((dphi + n_phi / 2) % n_phi) - n_phi / 2


def lorentz_1d(x: np.ndarray, gamma: float) -> np.ndarray:
    """ローレンツ関数(面積1正規化)。gamma はHWHM。"""
    return (gamma / np.pi) / (x ** 2 + gamma ** 2)


def gauss_1d(x: np.ndarray, sigma: float) -> np.ndarray:
    """ガウス関数(面積1正規化)。"""
    return np.exp(-0.5 * (x / sigma) ** 2) / (sigma * np.sqrt(2.0 * np.pi))


def peak_profile(R, phi, R0, phi0, gamma_R, sigma_chi, n_phi):
    """1つの反射の2Dプロファイル P(R, phi) = Lorentz(R-R0) x Gauss(wrap(phi-phi0))。"""
    p_radial = lorentz_1d(R - R0, gamma_R)
    p_azim = gauss_1d(wrap_pixel(phi - phi0, n_phi), sigma_chi)
    return p_radial * p_azim


def radial_width_component(sigma: float, dstar: float, wm: float, weq: float, c_para: float, lamda: float) -> float:
    """動径方向ローレンツ半幅への寄与(w0を含まない)。w_function。"""
    theta = math.asin(min(max(lamda * dstar / 2.0, -1.0), 1.0))
    denom = (math.cos(2.0 * theta) ** 2) * math.cos(theta)
    wc = wm + weq * math.sin(sigma) + c_para * dstar ** 2
    return wc / denom


# =============================================================================
# 反射位置の4回対称展開 + 重複除去
# =============================================================================

def expand_symmetric_points(projection: list[tuple[float, float, tuple[int, int, int]]]):
    """project_reflections の (p1, p3, hkl) から、検出器全面に展開した
    (r_nm, phi_deg_raw, hkl) のリストを返す(重複除去済み)。
    """
    polar_proj = []
    for p1, p3, hkl in projection:
        phi = math.degrees(math.atan2(p3, p1)) % 360.0
        r = math.hypot(p1, p3)
        polar_proj.append((r, phi, hkl))

    all_polar = []
    for r, phi, hkl in polar_proj:
        all_polar.append((r, phi, hkl))
        all_polar.append((r, -phi + 180, hkl))
        all_polar.append((r, phi + 180, hkl))
        all_polar.append((r, -phi + 360, hkl))

    seen = set()
    deduped = []
    for r, phi, hkl in all_polar:
        key = (round(r, 8), round(phi, 8), tuple(hkl))
        if key not in seen:
            seen.add(key)
            deduped.append((r, phi, hkl))
    return deduped


# =============================================================================
# 全対称等価反射のリスト作成
# =============================================================================

def build_reflections_list(
    deduped_points,
    a_s_v: np.ndarray,
    b_s_v: np.ndarray,
    c_s_v: np.ndarray,
    fiber_axis_v: np.ndarray,
    D: float,
    lamda: float,
    p: float,
    w0: float,
    wm: float,
    weq: float,
    c_crystal: float,
    B: float,
    n_r: int,
    n_phi: int,
) -> tuple[list[dict], dict]:
    """(r_nm, phi_deg_raw, hkl) のリストから、gamma_R・sigma_phi を含む反射リストを作る。

    B(配向幅)は deg 単位で受け取り、Gaussianの計算に使うsigma_phiは
    phi0 と同じ bin 単位に変換してから格納する。
    """
    reflections: list[dict] = []
    seen_keys = set()
    bin_per_deg = n_phi / 360.0
    stats = {"n_skipped_range": 0, "n_skipped_dup": 0, "n_skipped_meridian": 0}

    for r_nm, phi_raw, hkl in deduped_points:
        R0 = r_nm / p
        phi0 = phi_raw % 360.0
        if math.isclose(phi0, 360.0, abs_tol=1e-8) or math.isclose(phi0, 0.0, abs_tol=1e-8):
            phi0 = 0.0

        if not (0 <= R0 <= n_r):
            stats["n_skipped_range"] += 1
            continue

        key = (round(R0, 4), round(phi0, 4))
        if key in seen_keys:
            stats["n_skipped_dup"] += 1
            continue
        seen_keys.add(key)

        sigma_hkl = float(calc_sigma_from_hkl(np.array([hkl]), a_s_v, b_s_v, c_s_v, fiber_axis_v)[0])
        dstar = 2 * math.sin(0.5 * math.atan(r_nm / D)) / lamda
        gamma_R = w0 + radial_width_component(sigma_hkl, dstar, wm, weq, c_crystal, lamda)

        sin_s = math.sin(sigma_hkl)
        if sin_s < 0.05:
            stats["n_skipped_meridian"] += 1
            continue
        sigma_phi_deg = B / sin_s
        sigma_phi = sigma_phi_deg * bin_per_deg

        reflections.append({
            "R0": R0,
            "phi0": phi0 * bin_per_deg,
            "gamma_R": gamma_R,
            "sigma_phi": sigma_phi,
            "hkl": hkl,
            "sigma_hkl": sigma_hkl,
            "dstar": dstar,
        })

    return reflections, stats


# =============================================================================
# 設計行列の構築
# =============================================================================

def get_support_mask(R_flat, phi_flat, R0, phi0, gamma_R, sigma_phi, n_phi, n_lorentz=20, n_gauss=10):
    dR = np.abs(R_flat - R0)
    dphi = np.abs(wrap_pixel(phi_flat - phi0, n_phi))
    return (dR < n_lorentz * gamma_R) & (dphi < n_gauss * sigma_phi)


def build_design_matrix(R_grid, phi_grid, reflections, n_phi, n_lorentz=15, n_gauss=4, add_flat_bg=True):
    n_pixels = R_grid.size
    R_flat = R_grid.ravel()
    phi_flat = phi_grid.ravel()

    active_idx = []
    column_names = []
    masked_values: list[tuple[np.ndarray, np.ndarray]] = []

    for idx, ref in enumerate(reflections):
        mask = get_support_mask(
            R_flat, phi_flat, ref["R0"], ref["phi0"], ref["gamma_R"], ref["sigma_phi"],
            n_phi, n_lorentz=n_lorentz, n_gauss=n_gauss,
        )
        if not np.any(mask):
            continue

        values = peak_profile(
            R_flat[mask], phi_flat[mask], ref["R0"], ref["phi0"], ref["gamma_R"], ref["sigma_phi"], n_phi,
        )
        # n_pixels長の真偽値配列をそのまま反射の数だけ溜めるとメモリを圧迫するため、
        # Trueの位置(局所範囲、通常はごく一部)だけを整数添字で保持する。
        # ブールインデックスと同じ要素を同じ順序で選ぶため結果は完全に同一。
        idx_array = np.flatnonzero(mask)
        del mask
        masked_values.append((idx_array, values))
        active_idx.append(idx)
        column_names.append(f"peak_{idx}")

    n_peak_cols = len(active_idx)
    n_cols = n_peak_cols + (1 if add_flat_bg else 0)
    if n_cols == 0:
        return np.zeros((n_pixels, 0)), [], []

    # 反射ごとにn_pixels長の密な列をいったんリストに溜めてからcolumn_stackすると、
    # 溜めたリストとスタック後の行列が一時的に二重にメモリ上へ存在してしまう
    # (反射が密集するブロックでは数百MB級になりうる)。最終行列を先に確保して
    # そこへ直接書き込むことで、計算結果は完全に同一のまま、これを避ける。
    X = np.zeros((n_pixels, n_cols), dtype=np.float64)
    for col_i, (idx_array, values) in enumerate(masked_values):
        X[idx_array, col_i] = values
    del masked_values

    if add_flat_bg:
        X[:, n_peak_cols] = 1.0
        column_names.append("bg_flat")

    return X, active_idx, column_names


# =============================================================================
# ブロックごとのNNLSフィット
# =============================================================================

def block_fit(
    I_obs: np.ndarray,
    R_axis: np.ndarray,
    phi_axis: np.ndarray,
    reflections: list[dict],
    block_R: int = 150,
    stride_R: int | None = None,
    R_min_px: int = 125,
    on_progress: ProgressCallback | None = None,
):
    n_R, n_phi = I_obs.shape

    if stride_R is None:
        stride_R = block_R // 2

    start_center = R_min_px + block_R // 2
    end_center = n_R - block_R // 2

    centers_R_idx = list(range(start_center, end_center + 1, stride_R))
    if len(centers_R_idx) == 0:
        centers_R_idx = [(start_center + end_center) // 2]
    if centers_R_idx[-1] < n_R - block_R // 2:
        centers_R_idx.append(n_R - block_R // 2)

    block_centers_R = np.array([R_axis[i] for i in centers_R_idx])
    n_blocks_total = len(centers_R_idx)
    inner_half_R = block_R // 4

    assignment: dict[int, list[int]] = {i: [] for i in range(n_blocks_total)}

    for ref_idx, ref in enumerate(reflections):
        R0 = ref["R0"]
        assigned = False

        for block_i, cR_idx in enumerate(centers_R_idx):
            cR_value = R_axis[cR_idx]

            if block_i == 0:
                inner_min = R_axis[max(0, cR_idx - block_R // 2)]
            else:
                inner_min = cR_value - inner_half_R

            if block_i == n_blocks_total - 1:
                inner_max = R_axis[min(n_R - 1, cR_idx + block_R // 2)]
            else:
                inner_max = cR_value + inner_half_R

            if inner_min <= R0 < inner_max:
                assignment[block_i].append(ref_idx)
                assigned = True
                break

        if not assigned:
            dR = np.abs(block_centers_R - R0)
            i_best = int(np.argmin(dR))
            assignment[i_best].append(ref_idx)

    intensities = np.zeros(len(reflections))
    counts = np.zeros(len(reflections), dtype=int)
    bg_values = np.full(n_blocks_total, np.nan)

    for block_count, cR in enumerate(centers_R_idx, 1):
        block_i = block_count - 1
        assigned_refs = set(assignment[block_i])

        sR = max(0, cR - block_R // 2)
        eR = min(n_R, cR + block_R // 2)

        R_block = R_axis[sR:eR]
        phi_block = phi_axis

        R_g, phi_g = np.meshgrid(R_block, phi_block, indexing="ij")
        y = I_obs[sR:eR, :].ravel().astype(np.float64)

        X, active_idx, _column_names = build_design_matrix(R_g, phi_g, reflections, n_phi, add_flat_bg=True)
        del R_g, phi_g

        if on_progress:
            on_progress(block_count, n_blocks_total)

        if len(active_idx) == 0:
            del X
            continue

        X_fit = np.asarray(X, dtype=np.float64)
        y_fit = np.asarray(y, dtype=np.float64)
        del y

        valid = np.isfinite(y_fit) & np.all(np.isfinite(X_fit), axis=1)
        X_fit = X_fit[valid]
        y_fit = y_fit[valid]
        del valid

        if X_fit.shape[0] == 0 or X_fit.shape[1] == 0:
            del X, X_fit
            continue

        col_norm = np.linalg.norm(X_fit, axis=0)
        active_cols = col_norm > 0
        del col_norm

        if not np.any(active_cols):
            del X, X_fit
            continue

        X_active = X_fit[:, active_cols]
        del X_fit

        try:
            A_active, _residual = nnls(X_active, y_fit, maxiter=10 * X_active.shape[1])
        except RuntimeError:
            del X, X_active
            continue

        A = np.zeros(X.shape[1], dtype=np.float64)
        A[active_cols] = A_active
        del X, X_active, A_active

        n_peak_cols = len(active_idx)
        A_peaks = A[:n_peak_cols]
        bg_flat = A[n_peak_cols] if len(A) > n_peak_cols else np.nan
        bg_values[block_i] = bg_flat

        for k, idx in enumerate(active_idx):
            if idx in assigned_refs:
                intensities[idx] = A_peaks[k]
                counts[idx] = 1

    return intensities, counts, bg_values


# =============================================================================
# 経験的な強度誤差の見積もり
# =============================================================================

def estimate_errors(
    I_obs,
    R_axis,
    phi_axis,
    reflections,
    intensities,
    bg_flat=0.0,
    sigma_const=0.0,
    sigma_const_fraction=0.0,
    sigma_coeff=None,
    relative_error=0.03,
    sigma_scale=1.0,
    delta_mode="unity",
    delta_value=0.01,
    min_delta_mode="median",
    min_delta_fraction=1e-3,
):
    I_obs = np.asarray(I_obs, dtype=float)
    R_axis = np.asarray(R_axis, dtype=float)
    phi_axis = np.asarray(phi_axis, dtype=float)
    intensities = np.asarray(intensities, dtype=float)

    n_R, n_phi = I_obs.shape
    n_ref = len(reflections)

    R_g, phi_g = np.meshgrid(R_axis, phi_axis, indexing="ij")
    R_flat = R_g.ravel()
    phi_flat = phi_g.ravel()
    y = I_obs.ravel()
    valid = np.isfinite(y)

    sigma_I = np.full(n_ref, np.inf, dtype=float)
    delta_chi2 = np.zeros(n_ref, dtype=float)
    dA_used = np.zeros(n_ref, dtype=float)
    sigma_overlap_arr = np.full(n_ref, np.inf, dtype=float)
    sigma_relative_arr = np.full(n_ref, np.inf, dtype=float)

    positive_A = intensities[np.isfinite(intensities) & (intensities > 0)]
    A_scale = np.nanmedian(positive_A) if len(positive_A) else 1.0

    if sigma_const is None:
        sigma_const = sigma_const_fraction * A_scale
    sigma_const = float(sigma_const)

    if min_delta_mode == "unity":
        dA_min = 1.0
    elif min_delta_mode == "median":
        dA_min = min_delta_fraction * A_scale
    else:
        raise ValueError("min_delta_mode must be 'unity' or 'median'")

    if bg_flat is None or not np.isfinite(bg_flat):
        bg_flat = 0.0

    I_calc_base = np.full_like(y, float(bg_flat), dtype=float)
    profiles = []

    # mask(画像全体サイズの真偽値配列, 約700KB)を反射の数だけ profiles に
    # そのまま溜めると、反射数が1000を超えるあたりでGB単位のメモリを食う
    # (実際にこれが原因でブラウザ内実行時のメモリ不足を引き起こした)。
    # mask が True の位置(反射の局所範囲、通常は数百点程度)だけを整数添字で
    # 保持するように変更する。ブールインデックスと整数添字インデックスは
    # 同じ要素を同じ順序で選び出すため、計算結果は完全に同一になる。
    for idx, ref in enumerate(reflections):
        mask = get_support_mask(R_flat, phi_flat, ref["R0"], ref["phi0"], ref["gamma_R"], ref["sigma_phi"], n_phi)
        if not np.any(mask):
            profiles.append(None)
            continue

        prof_values = peak_profile(
            R_flat[mask], phi_flat[mask], ref["R0"], ref["phi0"], ref["gamma_R"], ref["sigma_phi"], n_phi,
        )
        prof_values = np.asarray(prof_values, dtype=float)
        idx_array = np.flatnonzero(mask)
        del mask
        profiles.append((idx_array, prof_values))

        A = intensities[idx]
        if np.isfinite(A):
            I_calc_base[idx_array] += A * prof_values

    SPP_all = np.array([np.sum(p[1] ** 2) if p is not None else np.nan for p in profiles], dtype=float)
    med_SPP = np.nanmedian(SPP_all[np.isfinite(SPP_all) & (SPP_all > 0)])

    if sigma_coeff is None:
        if not np.isfinite(med_SPP) or med_SPP <= 0:
            med_SPP = 1.0
        sigma_coeff = relative_error * A_scale * med_SPP
    sigma_coeff = float(sigma_coeff)

    for idx in range(n_ref):
        if profiles[idx] is None:
            continue

        idx_array, prof_values = profiles[idx]
        valid_in_mask = valid[idx_array] & np.isfinite(prof_values)
        if not np.any(valid_in_mask):
            continue

        P = prof_values[valid_in_mask]
        A = intensities[idx]
        if not np.isfinite(A):
            continue

        if delta_mode == "unity":
            dA = 1.0
        elif delta_mode == "absolute":
            dA = float(delta_value)
        elif delta_mode == "relative":
            dA = max(delta_value * abs(A), dA_min)
        else:
            raise ValueError("delta_mode must be 'unity', 'absolute', or 'relative'")
        dA_used[idx] = dA

        SPP = np.sum(P ** 2)
        dchi2 = (dA ** 2) * SPP
        delta_chi2[idx] = dchi2
        if not np.isfinite(dchi2) or dchi2 <= 0:
            continue

        sigma_overlap = sigma_coeff / dchi2
        sigma_relative = relative_error * abs(A)
        sigma_overlap_arr[idx] = sigma_overlap
        sigma_relative_arr[idx] = sigma_relative

        sigma_I[idx] = sigma_scale * math.sqrt(sigma_const ** 2 + sigma_overlap ** 2 + sigma_relative ** 2)

    sigma_components = {
        "sigma_const": sigma_const,
        "sigma_overlap": sigma_overlap_arr,
        "sigma_relative": sigma_relative_arr,
        "sigma_coeff": sigma_coeff,
        "sigma_scale": sigma_scale,
    }

    return sigma_I, delta_chi2, dA_used, sigma_components


# =============================================================================
# 計算パターンの再構成
# =============================================================================

def reconstruct_pattern(R_axis, phi_axis, reflections, intensities, n_phi, bg_flat=0.0):
    R_g, phi_g = np.meshgrid(R_axis, phi_axis, indexing="ij")
    R_flat = R_g.ravel()
    phi_flat = phi_g.ravel()

    if bg_flat is None or not np.isfinite(bg_flat):
        bg_flat = 0.0

    I_calc_flat = np.full(R_flat.size, float(bg_flat), dtype=np.float64)

    for ref, A in zip(reflections, intensities):
        if not np.isfinite(A):
            continue

        mask = get_support_mask(R_flat, phi_flat, ref["R0"], ref["phi0"], ref["gamma_R"], ref["sigma_phi"], n_phi)
        if not np.any(mask):
            continue

        I_calc_flat[mask] += A * peak_profile(
            R_flat[mask], phi_flat[mask], ref["R0"], ref["phi0"], ref["gamma_R"], ref["sigma_phi"], n_phi,
        )

    return I_calc_flat.reshape(R_g.shape)
