"""ピーク幅(結晶子サイズ・配向乱れ・格子歪み由来の広がり)モデルを最小二乗フィットするモジュール。

instensity_resrict_polar.ipynb セル27・28・29〜33 の移植:
  fit_radial_width_fixed_center     : セル28相当。ピーク位置(r0)を固定し、
                                        動径方向ローレンツ幅のみをフィット
  fit_azimuthal_width_fixed_center  : セル27相当。ピーク位置(phi0)を固定し、
                                        方位角方向ガウス幅のみをフィット(l=0反射用)
  fit_peak_widths                    : 上記2つを選択済み反射点全体に適用するドライバ
  w0            : ビームサイズ由来の幅(固定値、beam_size/p)
  calc_sigma_from_hkl : 各hklの逆格子ベクトルと繊維軸とのなす角 sigma
  fit_peak_width_model : w0 を固定して wm, weq, c をフィットする w_function (セル33)
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import curve_fit
from scipy.stats import chi2


def fit_radial_width_fixed_center(
    polar_sub_smooth: np.ndarray,
    r0_px: float,
    phi0_deg: float,
    n_phi: int,
    r_window: float,
) -> float | None:
    """r0を固定し、phi0に最も近い方位角トレースの動径プロファイルにローレンツ+定数を
    フィットして動径方向の幅(HWHM)を返す(ノートブック cell28 の移植)。

    ノートブックは背景(定数項)をフィット範囲内のmin(y)に固定しているが、
    フィット範囲が幅に対して狭いとmin(y)が真の背景より大幅に高く見積もられ、
    幅が過小評価される。そのため背景も自由パラメータとしてフィットする。
    """
    n_r = polar_sub_smooth.shape[1]
    phi_bin = int(round(phi0_deg * n_phi / 360.0)) % n_phi
    profile = polar_sub_smooth[phi_bin]

    r_axis = np.arange(n_r, dtype=float)
    mask = (r_axis >= r0_px - r_window) & (r_axis <= r0_px + r_window)
    x = r_axis[mask]
    y = np.nan_to_num(profile[mask], nan=0.0)
    if len(y) == 0 or np.max(y) <= 0:
        return None

    def lorentz_const(x, amplitude, gamma, bg):
        return amplitude * (gamma / np.pi) / ((x - r0_px) ** 2 + gamma ** 2) + bg

    bg0 = float(np.min(y))
    gamma0 = max(r_window / 4, 0.5)
    amp0 = max(float(np.max(y) - bg0) * math.pi * gamma0, 1.0)
    p0 = [amp0, gamma0, bg0]
    lower = [0.0, 0.1, 0.0]
    upper = [np.inf, max(r_window, 0.2), max(float(np.max(y)), 1.0)]

    try:
        popt, _ = curve_fit(lorentz_const, x, y, p0=p0, bounds=(lower, upper), maxfev=5000)
    except RuntimeError:
        return None

    return float(popt[1])


def fit_azimuthal_width_fixed_center(
    polar_sub_smooth: np.ndarray,
    r0_px: float,
    phi0_deg: float,
    n_phi: int,
    phi_window_deg: float,
    r_avg_width: int = 5,
) -> float | None:
    """phi0(固定)を中心に、r0付近で平均した方位角プロファイルへ
    ガウス+定数(背景も固定)をフィットして配向幅(sigma, deg単位)を返す
    (ノートブック cell27 の移植)。l=0反射にのみ使う。

    フィット自体はビン単位の格子(polar_sub_smoothのインデックス)で行うが、
    返り値は検出器のビン分解能(n_phi)に依存しない deg 単位に変換する。
    phi0が0°/360°付近にある場合でも周期境界をまたいで正しくフィットできるよう、
    中心からの角度差は [-n_phi/2, n_phi/2) に巻きつけてから使う。

    ノートブックは背景(定数項)をフィット範囲内のmin(y)に固定しているが、
    フィット範囲(phi_window_deg)が配向幅に対して狭いと、範囲内のどの点も
    まだピークの裾野にあるため min(y) が真の背景より大幅に高く見積もられ、
    配向幅が過小評価される(実測で真値の半分程度まで縮む場合があった)。
    そのため背景も自由パラメータとしてフィットする。
    """
    n_r = polar_sub_smooth.shape[1]
    r_lo = max(0, int(r0_px) - r_avg_width)
    r_hi = min(n_r, int(r0_px) + r_avg_width)
    if r_hi <= r_lo:
        return None

    profile = np.nanmean(polar_sub_smooth[:, r_lo:r_hi], axis=1)
    phi_axis = np.arange(n_phi, dtype=float)
    bin_per_deg = n_phi / 360.0
    phi0_bin = phi0_deg * bin_per_deg
    phi_window_bins = phi_window_deg * bin_per_deg

    # 周期境界(0°と360°の接続)を考慮した中心からの角度差
    dphi_bin = ((phi_axis - phi0_bin + n_phi / 2) % n_phi) - n_phi / 2
    mask = np.abs(dphi_bin) <= phi_window_bins
    x = dphi_bin[mask]
    y = np.nan_to_num(profile[mask], nan=0.0)
    if len(y) == 0 or np.max(y) <= 0:
        return None

    def gauss_const(x, amplitude, sigma, bg):
        return amplitude * np.exp(-0.5 * (x / sigma) ** 2) / (sigma * np.sqrt(2 * np.pi)) + bg

    bg0 = float(np.min(y))
    sigma0 = max(phi_window_bins / 4, 0.5)
    amp0 = max(float(np.max(y) - bg0) * sigma0 * math.sqrt(2 * math.pi), 1.0)
    p0 = [amp0, sigma0, bg0]
    lower = [0.0, 0.1, 0.0]
    upper = [np.inf, max(phi_window_bins, 0.2), max(float(np.max(y)), 1.0)]

    try:
        popt, _ = curve_fit(gauss_const, x, y, p0=p0, bounds=(lower, upper), maxfev=5000)
    except RuntimeError:
        return None

    sigma_bin = float(popt[1])
    return sigma_bin / bin_per_deg


def fit_peak_widths(
    points: list[dict],
    polar_sub_smooth: np.ndarray,
    n_phi: int,
    r_window: float,
    phi_window_deg: float,
    r_avg_width: int = 5,
) -> tuple[list[dict], list[dict]]:
    """選択済み反射点(各要素: h,k,l,r0_px,phi0_deg,d_obs)について、
    動径方向の幅は全点、方位角方向の幅はl=0の点のみフィットする。

    戻り値: (radial_results, l0_results)。どちらも入力の点情報に
    "gamma_r_px"(radial)または"sigma_phi_deg"(l0)を加えたdictのリスト。
    """
    radial_results: list[dict] = []
    l0_results: list[dict] = []

    for pt in points:
        gamma_r = fit_radial_width_fixed_center(
            polar_sub_smooth, pt["r0_px"], pt["phi0_deg"], n_phi, r_window,
        )
        if gamma_r is not None:
            radial_results.append({**pt, "gamma_r_px": gamma_r})

        if pt["l"] == 0:
            sigma_phi = fit_azimuthal_width_fixed_center(
                polar_sub_smooth, pt["r0_px"], pt["phi0_deg"], n_phi, phi_window_deg, r_avg_width,
            )
            if sigma_phi is not None:
                l0_results.append({**pt, "sigma_phi_deg": sigma_phi})

    return radial_results, l0_results


def calc_sigma_from_hkl(
    hkl_observed: np.ndarray, a_s_v: np.ndarray, b_s_v: np.ndarray, c_s_v: np.ndarray,
    fiber_axis_v: np.ndarray,
) -> np.ndarray:
    """各hklの逆格子ベクトルと繊維軸方向とのなす角 sigma [rad] を返す。"""
    hkl_observed = np.asarray(hkl_observed, dtype=float)
    h, k, l = hkl_observed[:, 0], hkl_observed[:, 1], hkl_observed[:, 2]
    g_vec = h[:, None] * a_s_v[None, :] + k[:, None] * b_s_v[None, :] + l[:, None] * c_s_v[None, :]
    g_norm = np.linalg.norm(g_vec, axis=1)
    g_unit = g_vec / g_norm[:, None]

    fiber_axis = fiber_axis_v / np.linalg.norm(fiber_axis_v)
    cos_sigma = np.clip(np.abs(g_unit @ fiber_axis), -1.0, 1.0)
    return np.arccos(cos_sigma)


def fit_peak_width_model(
    observations: list[dict],
    a_s_v: np.ndarray,
    b_s_v: np.ndarray,
    c_s_v: np.ndarray,
    fiber_axis_v: np.ndarray,
    w0: float,
    lamda: float,
) -> dict:
    """observations: [{'hkl': (h,k,l), 'dstar': float, 'gamma_r': float}, ...] から
    wm, weq, c をフィットする(w0は固定)。ノートブック cell 33 r_sigma_fitting の移植。
    """
    hkl_arr = np.array([o["hkl"] for o in observations], dtype=float)
    dstar_arr = np.array([o["dstar"] for o in observations], dtype=float)
    w_arr = np.array([o["gamma_r"] for o in observations], dtype=float)

    sigma_arr = calc_sigma_from_hkl(hkl_arr, a_s_v, b_s_v, c_s_v, fiber_axis_v)

    def w_function(X, wm, weq, c_para):
        sigma, dstar = X
        theta = np.arcsin(np.clip(lamda * dstar / 2.0, -1.0, 1.0))
        denom = (np.cos(2.0 * theta) ** 2) * np.cos(theta)
        wc = wm + weq * np.sin(sigma) + c_para * dstar ** 2
        return w0 + wc / denom

    p0 = [1.0, 1.0, 1.0]
    bounds = ([0.0, 0.0, 0.0], [np.inf, np.inf, np.inf])

    popt, pcov = curve_fit(
        w_function, (sigma_arr, dstar_arr), w_arr, p0=p0, bounds=bounds, maxfev=10000
    )
    perr = np.sqrt(np.diag(pcov))

    w_calc = w_function((sigma_arr, dstar_arr), *popt)
    residual = w_arr - w_calc
    chi = float(np.sum((residual ** 2) / w_calc))
    dof = len(w_arr) - len(popt)
    # ノートブック cell 33 に合わせて sf (生存関数) を使う (cell21 の unit_cell_fitting は cdf で、
    # 元のノートブック自体がセルごとに異なる慣習を使っているため、それぞれ忠実に踏襲する)
    pvalue = float(chi2.sf(chi, df=dof)) if dof > 0 else float("nan")

    wm_fit, weq_fit, c_fit = (float(v) for v in popt)
    wm_err, weq_err, c_err = (float(v) for v in perr)

    return {
        "w0": float(w0),
        "wm": wm_fit,
        "weq": weq_fit,
        "c": c_fit,
        "wm_err": wm_err,
        "weq_err": weq_err,
        "c_err": c_err,
        "chi2": chi,
        "dof": dof,
        "pvalue": pvalue,
        "w_calc": [float(v) for v in w_calc],
        "sigma_deg": [math.degrees(float(v)) for v in sigma_arr],
    }
