"""クリックした回折点のピーク位置を精密化し、最小二乗法で単位格子を精密化するモジュール。

ピーク検出: 理論位置(x0,y0)周辺を極座標(r, phi)でサンプリングし、動径方向ローレンツ関数×
方位角方向ガウス関数の2Dモデル(instensity_resrict_polar.ipynb セル20 の
_peak_2d_lorentz_x_gauss と同じ関数形)を直接生画像に対してフィットする。
単位格子精密化: instensity_resrict_polar.ipynb セル21 unit_cell_fitting の一般化版。
元は alpha=beta=90度固定の単斜晶系専用だったが、一般三斜晶系の d 値の式をベースに、
結晶系ごとの独立パラメータ・連動・固定角をCRYSTAL_SYSTEMSで表現して7結晶系に対応する。
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import curve_fit
from scipy.stats import chi2

# 各結晶系の制約。free: curve_fitで実際にフィットするパラメータ(a,b,c,alpha,beta,gammaのうち)。
# fixed: 角度を定数に固定。linked_length/linked_angle: 従属パラメータが従う独立パラメータ名。
CRYSTAL_SYSTEMS: dict[str, dict] = {
    "triclinic": {
        "free": ["a", "b", "c", "alpha", "beta", "gamma"],
        "fixed": {},
        "linked_length": {},
        "linked_angle": {},
    },
    "monoclinic": {
        "free": ["a", "b", "c", "gamma"],
        "fixed": {"alpha": 90.0, "beta": 90.0},
        "linked_length": {},
        "linked_angle": {},
    },
    "orthorhombic": {
        "free": ["a", "b", "c"],
        "fixed": {"alpha": 90.0, "beta": 90.0, "gamma": 90.0},
        "linked_length": {},
        "linked_angle": {},
    },
    "tetragonal": {
        "free": ["a", "c"],
        "fixed": {"alpha": 90.0, "beta": 90.0, "gamma": 90.0},
        "linked_length": {"b": "a"},
        "linked_angle": {},
    },
    "hexagonal": {
        "free": ["a", "c"],
        "fixed": {"alpha": 90.0, "beta": 90.0, "gamma": 120.0},
        "linked_length": {"b": "a"},
        "linked_angle": {},
    },
    "trigonal": {
        "free": ["a", "alpha"],
        "fixed": {},
        "linked_length": {"b": "a", "c": "a"},
        "linked_angle": {"beta": "alpha", "gamma": "alpha"},
    },
    "cubic": {
        "free": ["a"],
        "fixed": {"alpha": 90.0, "beta": 90.0, "gamma": 90.0},
        "linked_length": {"b": "a", "c": "a"},
        "linked_angle": {},
    },
}


def _peak_2d_lorentz_x_gauss(rphi, amplitude, r0, phi0, gamma_r, sigma_phi, bg):
    """r方向ローレンツ×phi方向ガウスの2Dピークプロファイル(ノートブック セル20と同じ関数形)。

    phi はここでは理論位置周辺の非周期的な連続値として扱う(360度境界の折り返しは
    fit_peak_polar 側でサンプリング時に回避しているため、周期処理は不要)。
    """
    r, phi = rphi
    L = (gamma_r / np.pi) / ((r - r0) ** 2 + gamma_r ** 2)
    G = np.exp(-0.5 * ((phi - phi0) / sigma_phi) ** 2) / (sigma_phi * np.sqrt(2 * np.pi))
    return amplitude * L * G + bg


def fit_peak_polar(
    img: np.ndarray,
    x0: float,
    y0: float,
    cx: float,
    cy: float,
    r_window: float = 15.0,
    phi_window: float = 8.0,
    n_r: int = 31,
    n_phi: int = 33,
) -> dict | None:
    """理論位置(x0,y0)周辺を極座標(r,phi)でサンプリングし、動径方向ローレンツ×方位角方向
    ガウスの2Dモデルをフィットして、ピーク中心 (r0, phi0) などを返す。

    生画像(Cartesian)を r,phi のグリッド点ごとに最近傍サンプリングして直接フィットするため、
    極座標リビニング済みの画像がなくても使える。
    """
    r0_theory = math.hypot(x0 - cx, y0 - cy)
    phi0_theory = math.degrees(math.atan2(y0 - cy, x0 - cx))

    r_axis = np.linspace(r0_theory - r_window, r0_theory + r_window, n_r)
    phi_axis = np.linspace(phi0_theory - phi_window, phi0_theory + phi_window, n_phi)
    r_grid, phi_grid = np.meshgrid(r_axis, phi_axis, indexing="ij")

    phi_rad = np.radians(phi_grid)
    px = cx + r_grid * np.cos(phi_rad)
    py = cy + r_grid * np.sin(phi_rad)

    h, w = img.shape
    ix = np.round(px).astype(int)
    iy = np.round(py).astype(int)
    valid = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
    if valid.sum() < 20:
        return None

    intensity = np.full(r_grid.shape, np.nan)
    intensity[valid] = img[iy[valid], ix[valid]].astype(float)

    ok = ~np.isnan(intensity)
    r_flat = r_grid[ok]
    phi_flat = phi_grid[ok]
    val_flat = intensity[ok]

    bg0 = float(np.percentile(val_flat, 10))
    peak_val = float(val_flat.max())
    if peak_val <= bg0:
        return None

    gamma_r0 = max(r_window / 4, 0.5)
    sigma_phi0 = max(phi_window / 4, 0.5)
    amp0 = (peak_val - bg0) * math.pi * gamma_r0 * sigma_phi0 * math.sqrt(2 * math.pi)

    p0 = [amp0, r0_theory, phi0_theory, gamma_r0, sigma_phi0, bg0]
    lower = [0, r0_theory - r_window, phi0_theory - phi_window, 0.2, 0.2, 0]
    upper = [np.inf, r0_theory + r_window, phi0_theory + phi_window, r_window, phi_window, peak_val]

    try:
        popt, _ = curve_fit(
            _peak_2d_lorentz_x_gauss, (r_flat, phi_flat), val_flat,
            p0=p0, bounds=(lower, upper), maxfev=8000,
        )
    except RuntimeError:
        return None

    amplitude, r0, phi0, gamma_r, sigma_phi, bg = (float(v) for v in popt)

    return {
        "r0": r0,
        "phi0": phi0 % 360.0,
        "gamma_r": gamma_r,
        "sigma_phi_deg": sigma_phi,
        "amplitude": amplitude,
        "bg": bg,
        "peak_intensity": peak_val,
    }


def radius_nm_to_d(R: float, D: float, lamda: float) -> float | None:
    """検出器面上の動径距離 R [nm] から d 値 [nm] を求める
    (reflections.project_reflections の厳密な逆変換)。
    """
    rho = math.sqrt(D ** 2 + R ** 2)
    inner = 2 * (rho - D)
    if inner <= 0:
        return None
    s = math.sqrt(inner) / (lamda * math.sqrt(rho))
    if s <= 0:
        return None
    return 1.0 / s


def _d_hkl_triclinic(h, k, l, a, b, c, alpha_rad, beta_rad, gamma_rad):
    """一般三斜晶系の d 値。ノートブック cell 21 の d_function を alpha,beta 固定なしに一般化したもの。"""
    cos_a, cos_b, cos_g = np.cos(alpha_rad), np.cos(beta_rad), np.cos(gamma_rad)
    sin_a, sin_b, sin_g = np.sin(alpha_rad), np.sin(beta_rad), np.sin(gamma_rad)

    numerator = 1 - cos_a ** 2 - cos_b ** 2 - cos_g ** 2 + 2 * cos_a * cos_b * cos_g
    denominator = (
        (h * b * c * sin_a) ** 2
        + (k * c * a * sin_b) ** 2
        + (l * a * b * sin_g) ** 2
        + 2 * k * l * (a ** 2) * b * c * (cos_b * cos_g - cos_a)
        + 2 * l * h * (b ** 2) * c * a * (cos_g * cos_a - cos_b)
        + 2 * h * k * (c ** 2) * a * b * (cos_a * cos_b - cos_g)
    )
    return a * b * c * np.sqrt(numerator / denominator)


def _expand(spec: dict, values: dict) -> dict:
    """独立パラメータの値から、固定角・連動先を埋めて a,b,c,alpha,beta,gamma を全て揃える。"""
    full = dict(values)
    for name, value in spec["fixed"].items():
        full[name] = value
    for dep, src in spec["linked_length"].items():
        full[dep] = full[src]
    for dep, src in spec["linked_angle"].items():
        full[dep] = full[src]
    return full


def _expand_err(spec: dict, err_values: dict) -> dict:
    """誤差の展開版。固定パラメータは値ではなく誤差0として埋める。"""
    full = dict(err_values)
    for name in spec["fixed"]:
        full[name] = 0.0
    for dep, src in spec["linked_length"].items():
        full[dep] = full[src]
    for dep, src in spec["linked_angle"].items():
        full[dep] = full[src]
    return full


def _make_d_function(system: str):
    spec = CRYSTAL_SYSTEMS[system]
    free_names = spec["free"]

    def d_function(X, *params):
        h, k, l = X
        full = _expand(spec, dict(zip(free_names, params)))
        return _d_hkl_triclinic(
            h, k, l,
            full["a"], full["b"], full["c"],
            np.radians(full["alpha"]), np.radians(full["beta"]), np.radians(full["gamma"]),
        )

    return d_function


def refine_unit_cell(observations: list[dict], crystal_system: str, initial: dict) -> dict:
    """observations: [{'hkl': (h,k,l), 'd_obs': float}, ...] から結晶系に応じて単位格子を最小二乗フィットする。
    initial: {'a','b','c','alpha_deg','beta_deg','gamma_deg'} の現在値(初期値・従属パラメータの参照元)。
    """
    if crystal_system not in CRYSTAL_SYSTEMS:
        raise ValueError(f"未知の結晶系です: {crystal_system}")

    spec = CRYSTAL_SYSTEMS[crystal_system]
    free_names = spec["free"]

    initial_full = {
        "a": initial["a"], "b": initial["b"], "c": initial["c"],
        "alpha": initial["alpha_deg"], "beta": initial["beta_deg"], "gamma": initial["gamma_deg"],
    }
    p0 = [initial_full[name] for name in free_names]

    h_arr = np.array([o["hkl"][0] for o in observations], dtype=float)
    k_arr = np.array([o["hkl"][1] for o in observations], dtype=float)
    l_arr = np.array([o["hkl"][2] for o in observations], dtype=float)
    d_obs = np.array([o["d_obs"] for o in observations], dtype=float)

    d_function = _make_d_function(crystal_system)

    popt, pcov = curve_fit(d_function, (h_arr, k_arr, l_arr), d_obs, p0=p0)
    perr = np.sqrt(np.diag(pcov))

    d_calc = d_function((h_arr, k_arr, l_arr), *popt)
    chi = float(np.sum((d_obs - d_calc) ** 2 / d_calc))
    dof = len(d_obs) - len(popt)
    pvalue = float(chi2.cdf(chi, df=dof)) if dof > 0 else float("nan")

    free_values = dict(zip(free_names, (float(v) for v in popt)))
    err_values = dict(zip(free_names, (float(v) for v in perr)))

    full = _expand(spec, free_values)
    full_err = _expand_err(spec, err_values)

    return {
        "a": full["a"], "b": full["b"], "c": full["c"],
        "alpha_deg": full["alpha"], "beta_deg": full["beta"], "gamma_deg": full["gamma"],
        "a_err": full_err["a"], "b_err": full_err["b"], "c_err": full_err["c"],
        "alpha_err_deg": full_err["alpha"], "beta_err_deg": full_err["beta"], "gamma_err_deg": full_err["gamma"],
        "free_params": free_names,
        "chi2": chi,
        "dof": dof,
        "pvalue": pvalue,
        "d_calc": [float(v) for v in d_calc],
    }
