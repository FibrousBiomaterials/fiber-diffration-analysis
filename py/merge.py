"""ブロックフィットで得た反射強度を、対称等価な反射(同一hkl・フリーデル対・
数学的に完全一致する面間隔を持つ反射)についてマージ(重み付き平均)するモジュール。

instensity_resrict_polar.ipynb セル48・49・51〜54の移植:
  セル48: 有効な反射の抽出 (count>0, intensity>0, sigma_I>0 かつ有限)
  セル49: 同一hklの反射を重み付き平均でマージ (merge_by_hkl)
  セル51: 逆格子計量テンソルからのd値計算(本実装では既存の逆格子ベクトルの内積で代替)
  セル53/54: 同一l内でd値が近い反射(フリーデル対を含む対称等価反射)を重み付き平均でマージ
             (merge_by_dspacing)

ノートブックと同様、この2つは別々の(独立して呼び出せる)ステップとして分けてある。

面間隔の一致判定は、たまたま数値的に近い(が対称性による厳密な一致ではない)別反射を
誤って結合しないよう、浮動小数点誤差程度の厳しい許容誤差を既定値とする
(結晶の対称操作で本当に等価な反射は d が数学的に完全一致するため)。
"""
from __future__ import annotations

import numpy as np


def _canonical_hkl(h: int, k: int, l: int) -> tuple[int, int, int]:
    """フリーデル対 (h,k,l) と (-h,-k,-l) を同一グループにまとめるため、
    l, k, h の優先順位で最初の非ゼロ成分が正になるよう符号をそろえる。
    """
    h, k, l = int(h), int(k), int(l)
    if (l, k, h) < (0, 0, 0):
        return (-h, -k, -l)
    return (h, k, l)


def _weighted_merge(intensities: np.ndarray, sigma_I: np.ndarray) -> tuple[float, float] | None:
    """1/sigma^2 を重みとした加重平均と、その標準誤差を返す。"""
    valid = np.isfinite(intensities) & np.isfinite(sigma_I) & (sigma_I > 0) & (intensities > 0)
    if not np.any(valid):
        return None
    A = intensities[valid]
    s = sigma_I[valid]
    w = 1.0 / s ** 2
    A_merge = float(np.sum(w * A) / np.sum(w))
    sigma_merge = float(np.sqrt(1.0 / np.sum(w)))
    return A_merge, sigma_merge


def merge_by_hkl(
    reflections: list[dict],
    intensities: np.ndarray,
    sigma_I: np.ndarray,
    counts: np.ndarray,
    a_s_v: np.ndarray,
    b_s_v: np.ndarray,
    c_s_v: np.ndarray,
) -> list[dict]:
    """ブロックフィットの反射リスト(検出器上の対称等価な位置ごとに別エントリ、
    各要素は ref["hkl"], ref["R0"], ref["phi0"] を持つdict)を、符号正規化した
    同一hkl(フリーデル対を含む)でグループ化し、重み付き平均でマージする
    (ノートブック セル48・49相当)。

    戻り値: [{"h","k","l","d_hkl","dstar","intensity","sigma_I","multiplicity",
              "r0_px","phi0_list","merged_hkl"}, ...]
    """
    intensities = np.asarray(intensities, dtype=float)
    sigma_I = np.asarray(sigma_I, dtype=float)
    counts = np.asarray(counts)

    valid = (
        np.isfinite(intensities) & (intensities > 0)
        & np.isfinite(sigma_I) & (sigma_I > 0)
        & (counts > 0)
    )

    hkl_groups: dict[tuple[int, int, int], list[int]] = {}
    for i, ref in enumerate(reflections):
        if not valid[i]:
            continue
        key = _canonical_hkl(*ref["hkl"])
        hkl_groups.setdefault(key, []).append(i)

    merged_out: list[dict] = []
    for hkl_key, idxs in hkl_groups.items():
        idxs = np.array(idxs, dtype=int)
        merged = _weighted_merge(intensities[idxs], sigma_I[idxs])
        if merged is None:
            continue
        A_merge, sigma_merge = merged
        g_vec = hkl_key[0] * a_s_v + hkl_key[1] * b_s_v + hkl_key[2] * c_s_v
        dstar = float(np.linalg.norm(g_vec))
        if not np.isfinite(dstar) or dstar <= 0:
            continue
        merged_out.append({
            "h": hkl_key[0], "k": hkl_key[1], "l": hkl_key[2],
            "d_hkl": 1.0 / dstar,
            "dstar": dstar,
            "intensity": A_merge,
            "sigma_I": sigma_merge,
            "multiplicity": int(len(idxs)),
            "r0_px": float(np.mean([reflections[i]["R0"] for i in idxs])),
            "phi0_list": [float(reflections[i]["phi0"]) for i in idxs],
            "merged_hkl": [(hkl_key[0], hkl_key[1], hkl_key[2])],
        })

    return merged_out


def merge_by_dspacing(hkl_merged: list[dict], d_tol_nm: float = 1e-6) -> list[dict]:
    """merge_by_hkl の出力を、同一l内でd値(面間隔, nm)がほぼ完全一致する反射
    同士でさらに重み付き平均する(対称等価だがhklが異なる反射をまとめる。
    ノートブック セル51〜54相当)。

    d_tol_nm は既定でごく小さい値(浮動小数点誤差程度)にしてあり、対称操作で
    数学的に厳密に等しくなるd値のみを同一視する。数値的にたまたま近いだけの
    別反射(対称等価ではない)を誤って結合しないようにするため。
    """
    if not hkl_merged:
        return []

    intens1 = np.array([r["intensity"] for r in hkl_merged], dtype=float)
    sigma1 = np.array([r["sigma_I"] for r in hkl_merged], dtype=float)
    d1 = np.array([r["d_hkl"] for r in hkl_merged], dtype=float)
    l1 = np.array([r["l"] for r in hkl_merged], dtype=int)

    merged_out: list[dict] = []
    for l_val in sorted(set(l1.tolist())):
        layer_idx = np.where(l1 == l_val)[0]
        order = layer_idx[np.argsort(d1[layer_idx])]

        current_group = [order[0]]
        for idx in order[1:]:
            group_mean_d = float(np.mean(d1[current_group]))
            if abs(d1[idx] - group_mean_d) <= d_tol_nm:
                current_group.append(idx)
            else:
                merged_out.append(_finalize_group(current_group, hkl_merged, intens1, sigma1))
                current_group = [idx]
        merged_out.append(_finalize_group(current_group, hkl_merged, intens1, sigma1))

    return merged_out


def _finalize_group(group_idxs: list[int], stage1: list[dict], intens1: np.ndarray, sigma1: np.ndarray) -> dict:
    idxs = np.array(group_idxs, dtype=int)
    if len(idxs) == 1:
        return dict(stage1[idxs[0]])

    merged = _weighted_merge(intens1[idxs], sigma1[idxs])
    A_merge, sigma_merge = merged if merged is not None else (float(np.mean(intens1[idxs])), float(np.nan))
    rep = stage1[idxs[0]]
    total_mult = int(sum(stage1[i]["multiplicity"] for i in idxs))
    phi0_list: list[float] = []
    for i in idxs:
        phi0_list.extend(stage1[i]["phi0_list"])
    merged_hkl: list[tuple[int, int, int]] = []
    for i in idxs:
        merged_hkl.extend(stage1[i]["merged_hkl"])
    return {
        "h": rep["h"], "k": rep["k"], "l": rep["l"],
        "d_hkl": float(np.mean([stage1[i]["d_hkl"] for i in idxs])),
        "dstar": float(np.mean([stage1[i]["dstar"] for i in idxs])),
        "intensity": A_merge,
        "sigma_I": sigma_merge,
        "multiplicity": total_mult,
        "r0_px": float(np.mean([stage1[i]["r0_px"] for i in idxs])),
        "phi0_list": phi0_list,
        "merged_hkl": merged_hkl,
    }
