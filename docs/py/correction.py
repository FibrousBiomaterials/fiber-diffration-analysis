"""ローレンツ補正・偏光補正マップを計算するモジュール。強度抽出パイプラインの最初のステップ。"""
from __future__ import annotations

import numpy as np


def polar_corr(theta: np.ndarray, rho: np.ndarray, A: float) -> np.ndarray:
    """偏光因子 P の逆数 1/P を返す(これを強度に掛けると偏光補正済みになる)。

    水平直線偏光成分 P_h = 1 - sin^2(2theta) * cos^2(rho)
    垂直直線偏光成分 P_v = 1 - sin^2(2theta) * sin^2(rho)
    P = A * P_h + (1 - A) * P_v

    theta : ブラッグ角 [rad] (2theta ではない)
    rho   : 検出器面上の方位角 [rad]。水平(赤道)方向で0。
    A     : 水平偏光成分の割合。1.0で完全水平偏光、0.5で無偏光と等価。
    """
    two_theta = 2.0 * theta
    sin2_2t = np.sin(two_theta) ** 2
    P_h = 1.0 - sin2_2t * np.cos(rho) ** 2
    P_v = 1.0 - sin2_2t * np.sin(rho) ** 2
    P = A * P_h + (1.0 - A) * P_v
    return 1.0 / P


def build_lorentz_map(
    shape: tuple[int, int],
    cx: float,
    cy: float,
    D: float,
    wavelength: float,
    pixel_size: float,
    A: float,
    beta: float = 0.0,
    fiber_axis: str = "vertical",
    blind_margin: float = 0.02,
) -> tuple[np.ndarray, np.ndarray]:
    """円筒対称ローレンツ因子 1/L と偏光因子 1/P を全ピクセルで計算し、その積を返す。

      1/L = xi * sqrt(1 - (lambda*(xi^2+zeta^2)/(2*xi))^2)
      1/P = polar_corr(theta, rho, A)

    xi: 逆空間の赤道面内成分, zeta: 子午線方向成分。
    xi->0(中心)と sin_rho->1(層線端)で L が発散するため blind_margin で除外する。
    幾何ヤコビアン J は含まない。

    戻り値: (inv_LP, mask)。mask=True の画素のみ inv_LP が有効。
    """
    # ブラウザ内実行(Pyodide/WASM)はプロセスに使えるメモリが限られており、
    # 4000x4000のfloat64配列(1枚あたり約128MB)を十数枚同時に保持すると
    # メモリ確保に失敗することがある。演算の順序・内容は変更せず、
    # 使い終わった配列を都度 del して解放する。
    ny, nx = shape
    yy, xx = np.mgrid[0:ny, 0:nx].astype(float)
    dx = (xx - cx) * pixel_size
    dy = (yy - cy) * pixel_size
    del yy, xx

    if fiber_axis == "vertical":
        m, e = dy, dx
    elif fiber_axis == "horizontal":
        m, e = dx, dy
    else:
        raise ValueError("fiber_axis must be 'vertical' or 'horizontal'")

    R = np.sqrt(dx ** 2 + dy ** 2)
    two_theta = np.arctan2(R, D)
    del R
    theta = 0.5 * two_theta
    s = 2.0 * np.sin(theta) / wavelength
    phi = np.arctan2(m, e)

    q_para = (np.cos(two_theta) - 1.0) / wavelength
    q_perp = np.sin(two_theta) / wavelength
    del two_theta
    q_m = q_perp * np.sin(phi)
    del q_perp, phi
    zeta = q_para * np.sin(beta) + q_m * np.cos(beta)
    del q_para, q_m
    xi = np.sqrt(np.clip(s ** 2 - zeta ** 2, 0.0, None))
    del s

    with np.errstate(divide="ignore", invalid="ignore"):
        sin_rho = wavelength * (xi ** 2 + zeta ** 2) / (2.0 * xi)
    del zeta
    cos2_rho = 1.0 - sin_rho ** 2
    mask = (xi > 1e-9) & (cos2_rho > blind_margin) & np.isfinite(sin_rho)
    del sin_rho

    rho = np.arctan2(e, m)
    del m, e
    inv_P = polar_corr(theta, rho, A)
    del rho

    inv_LP = np.zeros_like(xi)
    inv_LP[mask] = (
        (xi[mask] * np.sqrt(cos2_rho[mask])) * inv_P[mask] / np.cos(2 * theta[mask]) ** 3
    )

    return inv_LP, mask
