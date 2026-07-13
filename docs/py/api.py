"""ブラウザ内実行(Pyodide)用の薄いディスパッチ層。

各ハンドラはHTTP/Pydantic/ジョブポーリングに依存しないプレーン関数で、
`handler(payload: dict, progress_cb, file_bytes) -> dict` の形を持ち、
pyWorker.js から `dispatch(route, payload, progress_cb, file_bytes)` 経由で
呼び出される。

progress_cb は進捗のない処理でも常に渡されるが、使わないハンドラは無視してよい
(None チェック不要 — pyWorker.js 側で必ず呼び出し可能な関数を渡す)。
file_bytes は画像バイト列を必要としないルートでは None になる。

同期エンドポイントは毎回ファイルを再読込・再デコードする
(呼び出し側 = app.js が毎回 File API から読み直したバイト列を渡してくる)。
"""
from __future__ import annotations

import base64
import io
import math
import uuid

import numpy as np
from PIL import Image

from background import run_background_removal
from blockfit import (
    block_fit,
    build_reflections_list,
    estimate_errors,
    expand_symmetric_points,
    reconstruct_pattern,
)
from detector import load_detector_image
from correction import build_lorentz_map
from merge import merge_by_dspacing, merge_by_hkl
from peakwidth import fit_peak_width_model, fit_peak_widths
from reflections import compute_reciprocal_vectors, compute_unit_cell_vectors, project_reflections
from refine import CRYSTAL_SYSTEMS, fit_peak_polar, radius_nm_to_d, refine_unit_cell
from tilt import rotate_around_center, search_tilt_angle

PREVIEW_MAX_SIZE = 900

# バックグラウンド除去の極座標配列(1Dプロファイル取得・後続ステージで再利用)。
# JSON化しないので専用のストアに保持する。
background_results: dict[str, dict] = {}
# ブロックフィットの観測・計算パターン配列(1Dプロファイル取得・面間隔マージで再利用)。
blockfit_results: dict[str, dict] = {}


def _decode_image(payload: dict, file_bytes) -> tuple[np.ndarray, int]:
    if file_bytes is None:
        raise ValueError("画像ファイルが指定されていません")
    raw = bytes(file_bytes)
    img_size = int(payload.get("img_size", 4000))
    img_bit = int(payload.get("img_bit", 16))
    return load_detector_image(raw, img_size, img_bit)


def _to_preview_png(img: np.ndarray) -> tuple[str, float, int, int]:
    """グレースケールのプレビューPNGを生成する。(base64, scale, width, height) を返す。

    scale は「フル解像度の1px」を「プレビューの何pxに対応させたか」を表す倍率。
    """
    log_img = np.log1p(np.clip(img.astype(np.float64), 0, None))
    lo, hi = log_img.min(), log_img.max()
    scaled = (log_img - lo) / (hi - lo) if hi > lo else np.zeros_like(log_img)
    # cmap="Greys" 相当、強度が高いほど黒くなるよう反転
    scaled_u8 = ((1.0 - scaled) * 255).astype(np.uint8)

    pil_img = Image.fromarray(scaled_u8, mode="L")

    orig_h, orig_w = img.shape
    scale = min(PREVIEW_MAX_SIZE / orig_w, PREVIEW_MAX_SIZE / orig_h, 1.0)
    new_w, new_h = max(1, round(orig_w * scale)), max(1, round(orig_h * scale))
    pil_img = pil_img.resize((new_w, new_h), Image.LANCZOS)

    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return b64, scale, new_w, new_h


def _load_image(payload: dict, progress_cb, file_bytes) -> dict:
    img, header = _decode_image(payload, file_bytes)

    stats = {
        "filename": payload.get("filename"),
        "shape": list(img.shape),
        "header_bytes": header,
        "min": float(img.min()),
        "max": float(img.max()),
        "mean": float(img.mean()),
    }

    preview_b64, _scale, _w, _h = _to_preview_png(img)

    return {"stats": stats, "preview_png_base64": preview_b64}


def _tilt_search(payload: dict, progress_cb, file_bytes) -> dict:
    img, _header = _decode_image(payload, file_bytes)

    cx = float(payload.get("cx", 2011.3))
    cy = float(payload.get("cy", 2012.3))
    downsample = int(payload.get("downsample", 4))

    def on_progress(done: int, total: int) -> None:
        progress_cb(done, total)

    result = search_tilt_angle(img, cx, cy, downsample=downsample, on_progress=on_progress)

    img_corrected = rotate_around_center(img.astype(np.float64), result["angle_opt_deg"], cx, cy, order=3)
    preview_b64, _scale, _w, _h = _to_preview_png(img_corrected)

    return {"stats": result, "preview_png_base64": preview_b64}


def _reflections(payload: dict, progress_cb, file_bytes) -> dict:
    img, _header = _decode_image(payload, file_bytes)

    tilt_angle_deg = float(payload.get("tilt_angle_deg", 0))
    cx = float(payload.get("cx", 2011.3))
    cy = float(payload.get("cy", 2012.3))
    img_size = int(payload.get("img_size", 4000))
    p = float(payload.get("p", 100000))
    D = float(payload.get("D", 170210000))
    lamda = float(payload.get("lamda", 0.1))
    hkl_max = int(payload.get("hkl_max", 7))

    if tilt_angle_deg:
        img = rotate_around_center(img.astype(np.float64), tilt_angle_deg, cx, cy, order=3)

    a_v, b_v, c_v, V = compute_unit_cell_vectors(
        float(payload.get("a", 0.5939)), float(payload.get("b", 1.1431)), float(payload.get("c", 1.0460)),
        math.radians(float(payload.get("alpha_deg", 90))),
        math.radians(float(payload.get("beta_deg", 90))),
        math.radians(float(payload.get("gamma_deg", 95.4))),
    )
    a_s_v, b_s_v, c_s_v, _c_v_r = compute_reciprocal_vectors(
        a_v, b_v, c_v, V, math.radians(float(payload.get("phi_deg", 0)))
    )
    projection = project_reflections(hkl_max, a_s_v, b_s_v, c_s_v, D, lamda)

    points: list[dict] = []
    for p1, p3, hkl in projection:
        y = float(np.clip(p3 / p + cy, 0, img_size))
        x1 = float(np.clip(p1 / p + cx, 0, img_size))
        x2 = float(np.clip(-p1 / p + cx, 0, img_size))
        points.append({"h": hkl[0], "k": hkl[1], "l": hkl[2], "x": x1, "y": y})
        points.append({"h": hkl[0], "k": hkl[1], "l": hkl[2], "x": x2, "y": y})

    stats = {
        "unit_cell_volume_nm3": V,
        "reflection_count": len(projection),
        "plotted_points": len(points),
    }

    preview_b64, scale, preview_w, preview_h = _to_preview_png(img)

    return {
        "stats": stats,
        "preview_png_base64": preview_b64,
        "preview_scale": scale,
        "preview_size": [preview_w, preview_h],
        "image_size": [img.shape[1], img.shape[0]],
        "points": points,
    }


def _refine_unit_cell(payload: dict, progress_cb, file_bytes) -> dict:
    crystal_system = payload.get("crystal_system", "monoclinic")
    if crystal_system not in CRYSTAL_SYSTEMS:
        raise ValueError(f"未知の結晶系です: {crystal_system}")
    req_points = payload.get("points") or []
    if not req_points:
        raise ValueError("精密化する点が選択されていません")

    img, _header = _decode_image(payload, file_bytes)
    tilt_angle_deg = float(payload.get("tilt_angle_deg", 0))
    cx = float(payload.get("cx", 2011.3))
    cy = float(payload.get("cy", 2012.3))
    if tilt_angle_deg:
        img = rotate_around_center(img.astype(np.float64), tilt_angle_deg, cx, cy, order=3)

    D = float(payload.get("D", 170210000))
    lamda = float(payload.get("lamda", 0.1))
    p = float(payload.get("p", 100000))
    r_window = float(payload.get("r_window", 15))
    phi_window = float(payload.get("phi_window", 8))

    observations: list[dict] = []
    point_results: list[dict] = []

    for pt in req_points:
        h, k, l = int(pt["h"]), int(pt["k"]), int(pt["l"])
        base = {"h": h, "k": k, "l": l, "x_theory": pt["x"], "y_theory": pt["y"]}

        fit_peak = fit_peak_polar(img, pt["x"], pt["y"], cx, cy, r_window=r_window, phi_window=phi_window)
        if fit_peak is None:
            point_results.append({**base, "found": False})
            continue

        R_nm = fit_peak["r0"] * p
        d_obs = radius_nm_to_d(R_nm, D, lamda)
        if d_obs is None:
            point_results.append({**base, "found": False})
            continue

        phi0_rad = math.radians(fit_peak["phi0"])
        x_obs = cx + fit_peak["r0"] * math.cos(phi0_rad)
        y_obs = cy + fit_peak["r0"] * math.sin(phi0_rad)

        observations.append({"hkl": (h, k, l), "d_obs": d_obs})
        point_results.append({
            **base,
            "found": True,
            "x_obs": x_obs, "y_obs": y_obs,
            "r0_px": fit_peak["r0"], "phi0_deg": fit_peak["phi0"],
            "gamma_r_px": fit_peak["gamma_r"], "sigma_phi_deg": fit_peak["sigma_phi_deg"],
            "d_obs": d_obs,
        })

    n_free = len(CRYSTAL_SYSTEMS[crystal_system]["free"])
    if len(observations) < n_free:
        raise ValueError(
            f"{crystal_system} の精密化には{n_free}パラメータに対し"
            f"{n_free}点以上の反射が必要です(有効: {len(observations)}点)"
        )

    initial = {
        "a": float(payload.get("a", 0.5939)), "b": float(payload.get("b", 1.1431)), "c": float(payload.get("c", 1.0460)),
        "alpha_deg": float(payload.get("alpha_deg", 90)), "beta_deg": float(payload.get("beta_deg", 90)),
        "gamma_deg": float(payload.get("gamma_deg", 95.4)),
    }
    try:
        fit = refine_unit_cell(observations, crystal_system, initial)
    except RuntimeError as exc:
        raise ValueError(f"最小二乗フィットに失敗しました: {exc}") from exc

    d_calc_iter = iter(fit["d_calc"])
    for pr in point_results:
        if pr.get("found"):
            d_calc = next(d_calc_iter)
            pr["d_calc"] = d_calc
            pr["diff"] = abs(pr["d_obs"] - d_calc)

    return {
        "refined": {k: v for k, v in fit.items() if k != "d_calc"},
        "points": point_results,
        "n_used": len(observations),
    }


def _lorentz_correction(payload: dict, progress_cb, file_bytes) -> dict:
    fiber_axis = payload.get("fiber_axis", "vertical")
    if fiber_axis not in ("vertical", "horizontal"):
        raise ValueError("fiber_axis は vertical か horizontal を指定してください")

    img, _header = _decode_image(payload, file_bytes)
    tilt_angle_deg = float(payload.get("tilt_angle_deg", 0))
    cx = float(payload.get("cx", 2011.3))
    cy = float(payload.get("cy", 2012.3))
    if tilt_angle_deg:
        img = rotate_around_center(img.astype(np.float64), tilt_angle_deg, cx, cy, order=3)

    D = float(payload.get("D", 170210000))
    lamda = float(payload.get("lamda", 0.1))
    p = float(payload.get("p", 100000))
    A = float(payload.get("A", 0.83))
    beta_fiber_deg = float(payload.get("beta_fiber_deg", 0))

    inv_lp, mask = build_lorentz_map(
        img.shape, cx, cy, D, lamda, p, A,
        beta=math.radians(beta_fiber_deg), fiber_axis=fiber_axis,
    )

    img_corr = img.astype(np.float64) * inv_lp
    img_corr[~mask] = 0.0

    stats = {
        "mean_before": float(img[mask].mean()) if mask.any() else float("nan"),
        "mean_after": float(img_corr[mask].mean()) if mask.any() else float("nan"),
        "valid_fraction": float(mask.mean()),
    }

    preview_b64, _scale, _w, _h = _to_preview_png(img_corr)

    return {"stats": stats, "preview_png_base64": preview_b64}


def _background_removal(payload: dict, progress_cb, file_bytes) -> dict:
    fiber_axis = payload.get("fiber_axis", "vertical")
    if fiber_axis not in ("vertical", "horizontal"):
        raise ValueError("fiber_axis は vertical か horizontal を指定してください")

    img, _header = _decode_image(payload, file_bytes)
    img_f = img.astype(np.float64)
    del img

    cx = float(payload.get("cx", 2011.3))
    cy = float(payload.get("cy", 2012.3))
    tilt_angle_deg = float(payload.get("tilt_angle_deg", 0))
    D = float(payload.get("D", 170210000))
    lamda = float(payload.get("lamda", 0.1))
    p = float(payload.get("p", 100000))
    A = float(payload.get("A", 0.83))
    beta_fiber_deg = float(payload.get("beta_fiber_deg", 0))
    n_phi = int(payload.get("n_phi", 360))
    n_r = int(payload.get("n_r", 2000))
    r_min = int(payload.get("r_min", 125))
    grid_spacing = int(payload.get("grid_spacing", 150))
    n_iter = int(payload.get("n_iter", 50))
    sigma_scale = float(payload.get("sigma_scale", 0.1))
    window = int(payload.get("window", 150))
    smooth_sigma = (float(payload.get("smooth_sigma_phi", 6)), float(payload.get("smooth_sigma_r", 10)))

    # ローレンツ+偏光補正マップは傾き補正前の生画像座標系で計算し、
    # 傾き補正は極座標リビニングの中でまとめて行う。
    inv_lp, mask = build_lorentz_map(
        img_f.shape, cx, cy, D, lamda, p, A,
        beta=math.radians(beta_fiber_deg), fiber_axis=fiber_axis,
    )
    img_corr = img_f * inv_lp
    del inv_lp
    img_corr[~mask] = 0.0
    blind_mask = ~mask
    del mask

    def on_progress(done: int, total: int) -> None:
        progress_cb(done, total)

    result = run_background_removal(
        img_corr, img_f, cx, cy, tilt_angle_deg, blind_mask,
        n_phi=n_phi, n_r=n_r, r_min=r_min,
        grid_spacing=grid_spacing, n_iter=n_iter, sigma_scale=sigma_scale,
        window=window, smooth_sigma=smooth_sigma,
        on_progress=on_progress,
    )
    del img_corr, img_f, blind_mask

    observed_b64, _s, _w, _h = _to_preview_png(np.nan_to_num(result["polar_mean"]))
    bg_b64, _s, _w, _h = _to_preview_png(np.nan_to_num(result["polar_bg_smooth"]))
    sub_b64, _s, _w, _h = _to_preview_png(np.nan_to_num(result["polar_sub_smooth"]))

    stats = {
        "n_phi": n_phi,
        "n_r": n_r,
        "max_subtracted": float(np.nanmax(result["polar_sub_smooth"])),
        "mean_subtracted": float(np.nanmean(result["polar_sub_smooth"])),
    }

    job_id = uuid.uuid4().hex
    background_results[job_id] = {
        "polar_mean": result["polar_mean"],
        "polar_bg_smooth": result["polar_bg_smooth"],
        "polar_sub_smooth": result["polar_sub_smooth"],
        "n_phi": n_phi,
        "n_r": n_r,
    }

    return {
        "job_id": job_id,
        "stats": stats,
        "observed_png_base64": observed_b64,
        "background_png_base64": bg_b64,
        "subtracted_png_base64": sub_b64,
    }


def _background_removal_profile(payload: dict, progress_cb, file_bytes) -> dict:
    job_id = payload.get("background_job_id")
    data = background_results.get(job_id)
    if data is None:
        raise ValueError("ジョブ結果が見つかりません(先にバックグラウンド除去を実行してください)")

    n_phi = data["n_phi"]
    phi = int(payload.get("phi", 0))
    if not (0 <= phi < n_phi):
        raise ValueError(f"phi は 0〜{n_phi - 1} の範囲で指定してください")

    observed = np.nan_to_num(data["polar_mean"][phi]).tolist()
    background = np.nan_to_num(data["polar_bg_smooth"][phi]).tolist()
    subtracted = np.nan_to_num(data["polar_sub_smooth"][phi]).tolist()

    return {
        "phi": phi,
        "n_phi": n_phi,
        "n_r": data["n_r"],
        "observed": observed,
        "background": background,
        "subtracted": subtracted,
    }


def _peak_width_fit(payload: dict, progress_cb, file_bytes) -> dict:
    job_id = payload.get("background_job_id")
    data = background_results.get(job_id)
    if data is None:
        raise ValueError("バックグラウンド除去の結果が見つかりません(先に「6. バックグラウンド除去」を実行してください)")

    polar_sub_smooth = data["polar_sub_smooth"]
    n_phi = data["n_phi"]

    points_in = payload.get("points") or []
    r_window = float(payload.get("r_window", 15))
    phi_window_deg = float(payload.get("phi_window_deg", 8))
    r_avg_width = int(payload.get("r_avg_width", 5))

    radial_results, l0_results = fit_peak_widths(
        points_in, polar_sub_smooth, n_phi,
        r_window=r_window, phi_window_deg=phi_window_deg, r_avg_width=r_avg_width,
    )

    if len(radial_results) < 4:
        raise ValueError(
            f"ピーク幅モデル(wm, weq, c)には4点以上の反射が必要です(動径方向の幅をフィットできた点: {len(radial_results)}点)"
        )
    if len(l0_results) == 0:
        raise ValueError("l=0(赤道)反射の配向幅を1つもフィットできませんでした(先に「4.」でl=0の点を含めて精密化し、フィッティング範囲を確認してください)")

    a_v, b_v, c_v, _V = compute_unit_cell_vectors(
        float(payload.get("a", 0.5939)), float(payload.get("b", 1.1431)), float(payload.get("c", 1.0460)),
        math.radians(float(payload.get("alpha_deg", 90))),
        math.radians(float(payload.get("beta_deg", 90))),
        math.radians(float(payload.get("gamma_deg", 95.4))),
    )
    a_s_v, b_s_v, c_s_v, c_v_r = compute_reciprocal_vectors(
        a_v, b_v, c_v, _V, math.radians(float(payload.get("phi_deg", 0)))
    )

    p = float(payload.get("p", 100000))
    lamda = float(payload.get("lamda", 0.1))
    w0 = float(payload.get("beam_size_nm", 100000)) / p
    observations = [
        {
            "hkl": (r["h"], r["k"], r["l"]), "dstar": 1.0 / r["d_obs"], "gamma_r": r["gamma_r_px"],
            "r0_px": r["r0_px"], "phi0_deg": r["phi0_deg"],
        }
        for r in radial_results
    ]

    try:
        fit = fit_peak_width_model(observations, a_s_v, b_s_v, c_s_v, c_v_r, w0, lamda)
    except RuntimeError as exc:
        raise ValueError(f"最小二乗フィットに失敗しました: {exc}") from exc

    points_out = []
    for obs, w_calc, sigma_deg in zip(observations, fit["w_calc"], fit["sigma_deg"]):
        points_out.append({
            "h": obs["hkl"][0], "k": obs["hkl"][1], "l": obs["hkl"][2],
            "r0_px": obs["r0_px"], "phi0_deg": obs["phi0_deg"],
            "dstar": obs["dstar"],
            "sigma_deg": sigma_deg,
            "gamma_r_obs": obs["gamma_r"],
            "gamma_r_calc": w_calc,
        })

    B = float(np.mean([r["sigma_phi_deg"] for r in l0_results]))
    b_points_out = [
        {"h": r["h"], "k": r["k"], "l": r["l"], "R0": r["r0_px"], "phi0": r["phi0_deg"], "sigma_deg": r["sigma_phi_deg"]}
        for r in l0_results
    ]

    return {
        "fit": {k: v for k, v in fit.items() if k not in ("w_calc", "sigma_deg")},
        "points": points_out,
        "B": B,
        "n_l0_fitted": len(l0_results),
        "b_points": b_points_out,
    }


def _format_merged_points(merged: list[dict]) -> list[dict]:
    out = []
    for m in merged:
        snr_m = float(m["intensity"] / m["sigma_I"]) if (np.isfinite(m["sigma_I"]) and m["sigma_I"] > 0) else 0.0
        out.append({
            "h": m["h"], "k": m["k"], "l": m["l"],
            "d_hkl": m["d_hkl"], "dstar": m["dstar"],
            "intensity": m["intensity"], "sigma_I": m["sigma_I"],
            "snr": snr_m, "multiplicity": m["multiplicity"],
            "r0_px": m["r0_px"], "phi0_list": m["phi0_list"],
            "merged_hkl": [list(hkl) for hkl in m["merged_hkl"]],
        })
    return out


def _block_fit(payload: dict, progress_cb, file_bytes) -> dict:
    job_id = payload.get("background_job_id")
    data = background_results.get(job_id)
    if data is None:
        raise ValueError("バックグラウンド除去の結果が見つかりません(先に「6. バックグラウンド除去」を実行してください)")

    polar_sub_smooth = data["polar_sub_smooth"]
    n_phi = data["n_phi"]
    n_r = data["n_r"]

    a_v, b_v, c_v, _V = compute_unit_cell_vectors(
        float(payload.get("a", 0.5939)), float(payload.get("b", 1.1431)), float(payload.get("c", 1.0460)),
        math.radians(float(payload.get("alpha_deg", 90))),
        math.radians(float(payload.get("beta_deg", 90))),
        math.radians(float(payload.get("gamma_deg", 95.4))),
    )
    a_s_v, b_s_v, c_s_v, c_v_r = compute_reciprocal_vectors(
        a_v, b_v, c_v, _V, math.radians(float(payload.get("phi_deg", 0)))
    )

    D = float(payload.get("D", 170210000))
    lamda = float(payload.get("lamda", 0.1))
    p = float(payload.get("p", 100000))
    hkl_max = int(payload.get("hkl_max", 7))

    projection = project_reflections(hkl_max, a_s_v, b_s_v, c_s_v, D, lamda)
    deduped = expand_symmetric_points(projection)

    # 配向幅Bは「7. ピーク幅の算出」で別途計算済みの値を使う
    B = float(payload.get("B"))
    w0 = float(payload.get("w0"))
    wm = float(payload.get("wm"))
    weq = float(payload.get("weq"))
    c_crystal = float(payload.get("c_crystal"))

    # --- 全対称等価反射のリスト作成 ---
    reflections, list_stats = build_reflections_list(
        deduped, a_s_v, b_s_v, c_s_v, c_v_r, D, lamda, p,
        w0, wm, weq, c_crystal, B, n_r, n_phi,
    )

    if len(reflections) == 0:
        raise ValueError("有効な反射がありません(結晶パラメータやhkl範囲を確認してください)")

    block_R = int(payload.get("block_R", 150))
    r_min_px = int(payload.get("r_min_px", 125))
    relative_error = float(payload.get("relative_error", 0.03))
    goof = float(payload.get("goof", 1.0))

    # --- ブロックごとのNNLSフィット ---
    I_obs = polar_sub_smooth.T
    R_axis = np.arange(n_r, dtype=float)
    phi_axis = np.arange(n_phi, dtype=float)

    def on_progress(done: int, total: int) -> None:
        progress_cb(done, total)

    intensities, counts, bg_values = block_fit(
        I_obs, R_axis, phi_axis, reflections,
        block_R=block_R, R_min_px=r_min_px, on_progress=on_progress,
    )

    sort_idx = np.argsort([ref["R0"] for ref in reflections])
    reflections = [reflections[i] for i in sort_idx]
    intensities = intensities[sort_idx]
    counts = counts[sort_idx]

    # --- 誤差見積もり ---
    bg_flat = float(np.nanmedian(bg_values))
    sigma_I, _delta_chi2, _dA_used, sigma_components = estimate_errors(
        I_obs, R_axis, phi_axis, reflections, intensities, bg_flat,
        sigma_const=0.0, sigma_coeff=None, relative_error=relative_error,
        sigma_scale=goof, delta_mode="unity",
    )

    # --- パターン再構成 + R因子 ---
    I_calc = reconstruct_pattern(R_axis, phi_axis, reflections, intensities, n_phi, bg_flat)
    I_resid = I_obs - I_calc
    valid = (I_obs > 0) & np.isfinite(I_obs) & np.isfinite(I_calc)
    if valid.any() and np.sum(np.abs(I_obs[valid])) > 0:
        R_factor = float(np.sum(np.abs(I_resid[valid])) / np.sum(np.abs(I_obs[valid])))
    else:
        R_factor = float("nan")

    # プレビュー画像は「6. バックグラウンド除去」と同じ向き(phi方向を縦、r方向を横)にするため転置する。
    # I_obs/I_calc/I_resid自体(r方向が軸0)はフィット計算に使うのでそのまま。
    observed_b64, _s, _w, _h = _to_preview_png(np.nan_to_num(I_obs).T)
    calc_b64, _s, _w, _h = _to_preview_png(np.nan_to_num(I_calc).T)
    resid_b64, _s, _w, _h = _to_preview_png(np.nan_to_num(np.abs(I_resid)).T)

    result_job_id = uuid.uuid4().hex
    blockfit_results[result_job_id] = {
        "I_obs": I_obs, "I_calc": I_calc, "I_resid": I_resid,
        "n_r": n_r, "n_phi": n_phi,
    }

    points_out = []
    for i, ref in enumerate(reflections):
        sig = sigma_I[i]
        snr = float(intensities[i] / sig) if (np.isfinite(sig) and sig > 0) else 0.0
        points_out.append({
            "h": ref["hkl"][0], "k": ref["hkl"][1], "l": ref["hkl"][2],
            "R0": ref["R0"], "phi0": ref["phi0"],
            "gamma_R": ref["gamma_R"], "sigma_phi": ref["sigma_phi"],
            "dstar": ref["dstar"],
            "intensity": float(intensities[i]),
            "sigma_I": float(sig) if np.isfinite(sig) else None,
            "snr": snr,
            "used": bool(counts[i] > 0),
        })

    # --- 同一hkl(符号正規化してフリーデル対を含む)の反射を重み付き平均でマージ。
    #     面間隔が近い別hklとのさらなるマージは別ルート (block_fit_merge_by_dspacing) で行う。
    hkl_merged = merge_by_hkl(reflections, intensities, sigma_I, counts, a_s_v, b_s_v, c_s_v)
    merged_out = _format_merged_points(hkl_merged)
    blockfit_results[result_job_id]["hkl_merged"] = hkl_merged

    stats = {
        "n_reflections": len(reflections),
        "n_used": int(np.sum(counts > 0)),
        "n_skipped_range": list_stats["n_skipped_range"],
        "n_skipped_dup": list_stats["n_skipped_dup"],
        "n_skipped_meridian": list_stats["n_skipped_meridian"],
        "bg_flat": bg_flat,
        "r_factor": R_factor,
        "sigma_coeff": sigma_components["sigma_coeff"],
        "n_merged": len(merged_out),
    }

    return {
        "job_id": result_job_id,
        "stats": stats,
        "points": points_out,
        "merged_points": merged_out,
        "observed_png_base64": observed_b64,
        "calc_png_base64": calc_b64,
        "residual_png_base64": resid_b64,
    }


def _block_fit_profile(payload: dict, progress_cb, file_bytes) -> dict:
    job_id = payload.get("job_id")
    data = blockfit_results.get(job_id)
    if data is None:
        raise ValueError("ジョブ結果が見つかりません(先にブロックフィットを実行してください)")

    n_phi = data["n_phi"]
    phi = int(payload.get("phi", 0))
    if not (0 <= phi < n_phi):
        raise ValueError(f"phi は 0〜{n_phi - 1} の範囲で指定してください")

    observed = np.nan_to_num(data["I_obs"][:, phi]).tolist()
    calc = np.nan_to_num(data["I_calc"][:, phi]).tolist()
    residual = np.nan_to_num(data["I_resid"][:, phi]).tolist()

    return {
        "phi": phi,
        "n_phi": n_phi,
        "n_r": data["n_r"],
        "observed": observed,
        "calc": calc,
        "residual": residual,
    }


def _block_fit_merge_by_dspacing(payload: dict, progress_cb, file_bytes) -> dict:
    job_id = payload.get("job_id")
    data = blockfit_results.get(job_id)
    if data is None or "hkl_merged" not in data:
        raise ValueError("ジョブ結果が見つかりません(先にブロックフィットを実行してください)")

    d_tol_nm = float(payload.get("d_tol_nm", 1e-6))
    merged = merge_by_dspacing(data["hkl_merged"], d_tol_nm=d_tol_nm)
    return {"merged_points": _format_merged_points(merged)}


ROUTES = {
    "load_image": _load_image,
    "tilt_search": _tilt_search,
    "reflections": _reflections,
    "refine_unit_cell": _refine_unit_cell,
    "lorentz_correction": _lorentz_correction,
    "background_removal": _background_removal,
    "background_removal_profile": _background_removal_profile,
    "peak_width_fit": _peak_width_fit,
    "block_fit": _block_fit,
    "block_fit_profile": _block_fit_profile,
    "block_fit_merge_by_dspacing": _block_fit_merge_by_dspacing,
}


def dispatch(route: str, payload, progress_cb, file_bytes=None):
    if hasattr(payload, "to_py"):
        payload = payload.to_py()
    payload = dict(payload) if payload is not None else {}

    handler = ROUTES.get(route)
    if handler is None:
        raise ValueError(f"未知のルートです: {route}")
    try:
        return handler(payload, progress_cb, file_bytes)
    finally:
        # 4000x4000規模の大きな配列を扱うルートが多いため、呼び出しの区切りで
        # 明示的にGCを走らせ、循環参照などで解放が遅れる大きな配列があれば
        # すぐに回収してWASMヒープの断片化・肥大化を抑える。
        import gc
        gc.collect()
