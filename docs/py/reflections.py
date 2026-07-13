"""単位格子パラメータから回折点(反射)の検出器上の位置を計算するモジュール。

単位格子ベクトル→逆格子ベクトル→hklの投影、という順で計算する。
"""
from __future__ import annotations

import itertools
import math

import numpy as np


def compute_unit_cell_vectors(
    a: float, b: float, c: float, alpha: float, beta: float, gamma: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    """alpha, beta, gamma はラジアン。(a_v, b_v, c_v, V) を返す。"""
    a_v = np.array([a, 0.0, 0.0])
    b_v = np.array([b * np.cos(gamma), b * np.sin(gamma), 0.0])
    c_v = np.array([
        c * np.cos(beta),
        c * (np.cos(alpha) - np.cos(beta) * np.cos(gamma)) / np.sin(gamma),
        np.sqrt(
            c ** 2
            - (c * np.cos(beta)) ** 2
            - (c * (np.cos(alpha) - np.cos(beta) * np.cos(gamma)) / np.sin(gamma)) ** 2
        ),
    ])
    V = a * b * c * (
        (1 - np.cos(alpha) ** 2 - np.cos(beta) ** 2 - np.cos(gamma) ** 2
         + 2 * np.cos(alpha) * np.cos(beta) * np.cos(gamma)) ** 0.5
    )
    return a_v, b_v, c_v, float(V)


def compute_reciprocal_vectors(
    a_v: np.ndarray, b_v: np.ndarray, c_v: np.ndarray, V: float, phi: float
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Y軸を基準に phi(ラジアン)回転させてから逆格子ベクトルを計算する。

    (a_s_v, b_s_v, c_s_v, c_v_r) を返す。c_v_r は回転後の実空間c軸ベクトルで、
    繊維軸方向の近似として peakwidth.calc_sigma_from_hkl などで使う。
    """
    R = np.array([
        [np.cos(phi), 0, np.sin(phi)],
        [0, 1, 0],
        [-np.sin(phi), 0, np.cos(phi)],
    ])
    a_v_r = R @ a_v
    b_v_r = R @ b_v
    c_v_r = R @ c_v

    a_s_v = (1 / V) * np.cross(b_v_r, c_v_r)
    b_s_v = (1 / V) * np.cross(c_v_r, a_v_r)
    c_s_v = (1 / V) * np.cross(a_v_r, b_v_r)
    return a_s_v, b_s_v, c_s_v, c_v_r


def project_reflections(
    hkl_max: int,
    a_s_v: np.ndarray,
    b_s_v: np.ndarray,
    c_s_v: np.ndarray,
    D: float,
    lamda: float,
    theta_max: float = math.pi / 7,
) -> list[tuple[float, float, tuple[int, int, int]]]:
    """(p1, p3, (h,k,l)) のリストを返す。"""
    projection: list[tuple[float, float, tuple[int, int, int]]] = []

    for h, k, l in itertools.product(range(-hkl_max, hkl_max), repeat=3):
        q = h * a_s_v + k * b_s_v + l * c_s_v
        s = float(np.linalg.norm(q))
        if s == 0:
            continue
        s3 = q[2]

        denom = 2 - lamda ** 2 * s ** 2
        if denom == 0:
            continue

        inside = 1 - (lamda ** 2 * s ** 2) / 4 - (s3 / s) ** 2
        if inside < 0:
            continue

        p1 = (2 * lamda * D * s / denom) * math.sqrt(inside)
        p3 = (2 * lamda * D * s / denom) * (s3 / s)

        sin_theta = s * lamda / 2
        if not -1 <= sin_theta <= 1:
            continue
        theta = math.asin(sin_theta)

        if 0 < theta < theta_max:
            projection.append((p1, p3, (h, k, l)))

    return projection
