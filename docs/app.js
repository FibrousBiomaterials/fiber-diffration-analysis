const filenameSelect = document.getElementById("filename");
const form = document.getElementById("load-form");
const loadBtn = document.getElementById("load-btn");
const statusEl = document.getElementById("status");
const resultPanel = document.getElementById("load-result");
const statsEl = document.getElementById("stats");
const previewEl = document.getElementById("preview");

// filename(文字列) -> ArrayBuffer。File API で選択/ドロップされたファイルの中身を
// ブラウザ内に保持する(サーバー側ファイルシステムの代わり)。
const loadedFiles = new Map();
const fileInputEl = document.getElementById("file-input");
const fileDropZoneEl = document.getElementById("file-drop-zone");

function refreshFilenameSelect() {
  const previous = filenameSelect.value;
  filenameSelect.innerHTML = "";
  if (loadedFiles.size === 0) {
    const opt = document.createElement("option");
    opt.textContent = t("msg.no_files_selected");
    opt.disabled = true;
    filenameSelect.appendChild(opt);
    return;
  }
  for (const name of loadedFiles.keys()) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    filenameSelect.appendChild(opt);
  }
  if (loadedFiles.has(previous)) filenameSelect.value = previous;
}

function isSupportedImageFile(name) {
  const lower = name.toLowerCase();
  return lower.endsWith(".img") || lower.endsWith(".tif") || lower.endsWith(".tiff");
}

async function addFiles(fileList) {
  for (const file of fileList) {
    if (!isSupportedImageFile(file.name)) continue;
    const buffer = await file.arrayBuffer();
    loadedFiles.set(file.name, buffer);
  }
  refreshFilenameSelect();
}

fileInputEl.addEventListener("change", () => {
  addFiles(fileInputEl.files);
});

fileDropZoneEl.addEventListener("dragover", (event) => {
  event.preventDefault();
  fileDropZoneEl.classList.add("dragover");
});
fileDropZoneEl.addEventListener("dragleave", () => {
  fileDropZoneEl.classList.remove("dragover");
});
fileDropZoneEl.addEventListener("drop", (event) => {
  event.preventDefault();
  fileDropZoneEl.classList.remove("dragover");
  if (event.dataTransfer && event.dataTransfer.files) {
    addFiles(event.dataTransfer.files);
  }
});

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("error", isError);
}

const STAT_LABELS = {
  filename: "ファイル名",
  shape: "shape (H, W)",
  header_bytes: "ヘッダー長 [bytes]",
  min: "min",
  max: "max",
  mean: "mean",
};

const REFLECTIONS_STAT_LABELS = {
  unit_cell_volume_nm3: "単位格子体積 V [nm^3]",
  reflection_count: "反射数",
  plotted_points: "プロット点数 (±両側)",
};

const TILT_STAT_LABELS = {
  angle_opt_deg: "最適角度 [deg]",
  score: "対称性スコア (精密探索)",
  coarse_angle_deg: "粗探索の角度 [deg]",
  coarse_score: "対称性スコア (粗探索)",
};

// labels 引数は「どのキーを、どの順序で表示するか」の定義として使い、実際の表示文言は
// (言語切り替えに追従できるよう)常に i18n 辞書の "stats.<key>" から取得する。
function renderStats(targetEl, stats, labels) {
  targetEl.innerHTML = "";
  for (const key of Object.keys(labels)) {
    const dt = document.createElement("dt");
    dt.textContent = t(`stats.${key}`);
    const dd = document.createElement("dd");
    const value = stats[key];
    dd.textContent = Array.isArray(value) ? value.join(" x ") : value;
    targetEl.appendChild(dt);
    targetEl.appendChild(dd);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const filename = filenameSelect.value;
  if (!filename) {
    setStatus(t("msg.select_file"), true);
    return;
  }
  const fileBytes = loadedFiles.get(filename);
  if (!fileBytes) {
    setStatus(t("msg.file_data_missing"), true);
    return;
  }

  const payload = {
    filename,
    img_width: Number(document.getElementById("img_width").value),
    img_height: Number(document.getElementById("img_height").value),
    img_bit: Number(document.getElementById("img_bit").value),
  };

  loadBtn.disabled = true;
  setStatus(t("msg.loading"));
  resultPanel.hidden = true;

  try {
    await initPyodideWorker();
    const data = await pyCall("load_image", payload, fileBytes);
    renderStats(statsEl, data.stats, STAT_LABELS);
    previewEl.src = `data:image/png;base64,${data.preview_png_base64}`;
    resultPanel.hidden = false;
    setStatus(t("msg.load_done"));
    document.getElementById("tilt-panel").hidden = false;
  } catch (err) {
    setStatus(tf("msg.load_failed", { error: err.message }), true);
  } finally {
    loadBtn.disabled = false;
  }
});

const tiltForm = document.getElementById("tilt-form");
const tiltBtn = document.getElementById("tilt-btn");
const tiltStatusEl = document.getElementById("tilt-status");
const tiltResultPanel = document.getElementById("tilt-result");
const tiltStatsEl = document.getElementById("tilt-stats");
const tiltPreviewEl = document.getElementById("tilt-preview");

function setTiltStatus(message, isError = false) {
  tiltStatusEl.textContent = message;
  tiltStatusEl.classList.toggle("error", isError);
}

const reflectionsPanel = document.getElementById("reflections-panel");
const tiltSkipBtn = document.getElementById("tilt-skip-btn");
const tiltModeRadios = document.querySelectorAll('input[name="tilt_mode"]');

function applyTiltMode() {
  const mode = document.querySelector('input[name="tilt_mode"]:checked').value;
  tiltForm.hidden = mode !== "search";
  tiltSkipBtn.hidden = mode !== "skip";
}

tiltModeRadios.forEach((radio) => radio.addEventListener("change", applyTiltMode));
applyTiltMode();

tiltSkipBtn.addEventListener("click", () => {
  document.getElementById("tilt_angle_deg").value = 0;
  reflectionsPanel.hidden = false;
  setTiltStatus(t("msg.tilt_skip"));
});

tiltForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const filename = filenameSelect.value;
  if (!filename) {
    setTiltStatus(t("msg.select_file_first"), true);
    return;
  }
  const fileBytes = loadedFiles.get(filename);
  if (!fileBytes) {
    setTiltStatus(t("msg.file_data_missing"), true);
    return;
  }

  const payload = {
    filename,
    img_width: Number(document.getElementById("img_width").value),
    img_height: Number(document.getElementById("img_height").value),
    img_bit: Number(document.getElementById("img_bit").value),
    cx: Number(document.getElementById("tilt_cx").value),
    cy: Number(document.getElementById("tilt_cy").value),
    downsample: Number(document.getElementById("downsample").value),
  };

  tiltBtn.disabled = true;
  setTiltStatus(t("msg.tilt_searching"));
  tiltResultPanel.hidden = true;

  try {
    await initPyodideWorker();
    const data = await pyCallWithProgress("tilt_search", payload, (done, total) => {
      const percent = total > 0 ? (done / total) * 100 : 0;
      setTiltStatus(tf("msg.tilt_searching_percent", { percent: percent.toFixed(0) }));
    }, fileBytes);

    renderStats(tiltStatsEl, data.stats, TILT_STAT_LABELS);
    tiltPreviewEl.src = `data:image/png;base64,${data.preview_png_base64}`;
    tiltResultPanel.hidden = false;
    setTiltStatus(t("msg.tilt_done"));

    // 回折点の表示フォームに探索結果の角度を反映する(cx, cyは3.の入力欄をそのまま使う)
    document.getElementById("tilt_angle_deg").value = data.stats.angle_opt_deg;
    reflectionsPanel.hidden = false;
  } catch (err) {
    setTiltStatus(tf("msg.tilt_failed", { error: err.message }), true);
  } finally {
    tiltBtn.disabled = false;
  }
});

const reflectionsForm = document.getElementById("reflections-form");
const reflectionsBtn = document.getElementById("reflections-btn");
const reflectionsStatusEl = document.getElementById("reflections-status");
const reflectionsResultPanel = document.getElementById("reflections-result");
const reflectionsStatsEl = document.getElementById("reflections-stats");
const reflectionsPreviewEl = document.getElementById("reflections-preview");

// =============================================================================
// 結晶系の選択に応じて a,b,c,alpha,beta,gamma の入力可否・連動を切り替える
// =============================================================================

const CRYSTAL_SYSTEMS = {
  triclinic: { linkedLength: {}, linkedAngle: {}, fixedAngle: {}, freeCount: 6 },
  monoclinic: { linkedLength: {}, linkedAngle: {}, fixedAngle: { alpha_deg: 90, beta_deg: 90 }, freeCount: 4 },
  orthorhombic: { linkedLength: {}, linkedAngle: {}, fixedAngle: { alpha_deg: 90, beta_deg: 90, gamma_deg: 90 }, freeCount: 3 },
  tetragonal: { linkedLength: { b: "a" }, linkedAngle: {}, fixedAngle: { alpha_deg: 90, beta_deg: 90, gamma_deg: 90 }, freeCount: 2 },
  hexagonal: { linkedLength: { b: "a" }, linkedAngle: {}, fixedAngle: { alpha_deg: 90, beta_deg: 90, gamma_deg: 120 }, freeCount: 2 },
  trigonal: { linkedLength: { b: "a", c: "a" }, linkedAngle: { beta_deg: "alpha_deg", gamma_deg: "alpha_deg" }, fixedAngle: {}, freeCount: 2 },
  cubic: { linkedLength: { b: "a", c: "a" }, linkedAngle: {}, fixedAngle: { alpha_deg: 90, beta_deg: 90, gamma_deg: 90 }, freeCount: 1 },
};

const crystalSystemSelect = document.getElementById("crystal_system");
const LENGTH_FIELDS = ["a", "b", "c"];
const ANGLE_FIELDS = ["alpha_deg", "beta_deg", "gamma_deg"];

function applyCrystalSystem() {
  const sys = CRYSTAL_SYSTEMS[crystalSystemSelect.value];

  for (const id of [...LENGTH_FIELDS, ...ANGLE_FIELDS]) {
    document.getElementById(id).disabled = false;
  }
  for (const [field, value] of Object.entries(sys.fixedAngle)) {
    const el = document.getElementById(field);
    el.value = value;
    el.disabled = true;
  }
  for (const [field, source] of Object.entries(sys.linkedLength)) {
    const el = document.getElementById(field);
    el.value = document.getElementById(source).value;
    el.disabled = true;
  }
  for (const [field, source] of Object.entries(sys.linkedAngle)) {
    const el = document.getElementById(field);
    el.value = document.getElementById(source).value;
    el.disabled = true;
  }
}

crystalSystemSelect.addEventListener("change", () => {
  applyCrystalSystem();
  updateSelectionCount();
});

document.getElementById("a").addEventListener("input", (e) => {
  const sys = CRYSTAL_SYSTEMS[crystalSystemSelect.value];
  for (const [field, source] of Object.entries(sys.linkedLength)) {
    if (source === "a") document.getElementById(field).value = e.target.value;
  }
});

document.getElementById("alpha_deg").addEventListener("input", (e) => {
  const sys = CRYSTAL_SYSTEMS[crystalSystemSelect.value];
  for (const [field, source] of Object.entries(sys.linkedAngle)) {
    if (source === "alpha_deg") document.getElementById(field).value = e.target.value;
  }
});

applyCrystalSystem();

function setReflectionsStatus(message, isError = false) {
  reflectionsStatusEl.textContent = message;
  reflectionsStatusEl.classList.toggle("error", isError);
}

reflectionsForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const filename = filenameSelect.value;
  if (!filename) {
    setReflectionsStatus(t("msg.select_file_first"), true);
    return;
  }
  const fileBytes = loadedFiles.get(filename);
  if (!fileBytes) {
    setReflectionsStatus(t("msg.file_data_missing"), true);
    return;
  }

  const payload = {
    filename,
    img_width: Number(document.getElementById("img_width").value),
    img_height: Number(document.getElementById("img_height").value),
    img_bit: Number(document.getElementById("img_bit").value),
    a: Number(document.getElementById("a").value),
    b: Number(document.getElementById("b").value),
    c: Number(document.getElementById("c").value),
    alpha_deg: Number(document.getElementById("alpha_deg").value),
    beta_deg: Number(document.getElementById("beta_deg").value),
    gamma_deg: Number(document.getElementById("gamma_deg").value),
    D: Number(document.getElementById("D").value),
    lamda: Number(document.getElementById("lamda").value),
    p: Number(document.getElementById("p").value),
    cx: Number(document.getElementById("tilt_cx").value),
    cy: Number(document.getElementById("tilt_cy").value),
    phi_deg: Number(document.getElementById("phi_deg").value),
    tilt_angle_deg: Number(document.getElementById("tilt_angle_deg").value),
    hkl_max: Number(document.getElementById("hkl_max").value),
  };

  reflectionsBtn.disabled = true;
  setReflectionsStatus(t("msg.reflections_calculating"));
  reflectionsResultPanel.hidden = true;

  try {
    await initPyodideWorker();
    const data = await pyCall("reflections", payload, fileBytes);
    renderStats(reflectionsStatsEl, data.stats, REFLECTIONS_STAT_LABELS);
    reflectionsResultPanel.hidden = false;
    setReflectionsStatus(t("msg.reflections_done"));
    initViewer(data);
  } catch (err) {
    setReflectionsStatus(tf("msg.reflections_failed", { error: err.message }), true);
  } finally {
    reflectionsBtn.disabled = false;
  }
});

// =============================================================================
// 3. 回折点の表示 — ズーム/パン可能なビューアと反射点のクリック選択
// =============================================================================

const viewerEl = document.getElementById("viewer");
const viewerStageEl = document.getElementById("viewer-stage");
const overlaySvg = document.getElementById("viewer-overlay");
const viewerTooltipEl = document.getElementById("viewer-tooltip");
const selectionCountEl = document.getElementById("selection-count");
const viewerResetBtn = document.getElementById("viewer-reset-btn");
const clearSelectionBtn = document.getElementById("clear-selection-btn");
const refinePanel = document.getElementById("refine-panel");
const refineBtn = document.getElementById("refine-btn");

const CLICK_DRAG_THRESHOLD = 4; // これ以上動いたらドラッグ扱い(クリックとして扱わない)
const TOOLTIP_DURATION_MS = 1800; // 選択時のhklポップアップを自動で隠すまでの時間

const selectedListEl = document.getElementById("selected-list");

let currentPoints = [];
let selectedIndices = new Set();
let viewTransform = { scale: 1, tx: 0, ty: 0, fitScale: 1 };
let panState = null;
let dragDistance = 0;
let previewScale = 1;
let tooltipHideTimer = null;

function initViewer(data) {
  currentPoints = data.points;
  selectedIndices = new Set();
  hidePointTooltip();

  reflectionsPreviewEl.src = `data:image/png;base64,${data.preview_png_base64}`;
  const [pw, ph] = data.preview_size;
  previewScale = data.preview_scale;

  viewerStageEl.style.width = `${pw}px`;
  viewerStageEl.style.height = `${ph}px`;
  reflectionsPreviewEl.style.width = `${pw}px`;
  reflectionsPreviewEl.style.height = `${ph}px`;
  overlaySvg.setAttribute("width", pw);
  overlaySvg.setAttribute("height", ph);
  overlaySvg.setAttribute("viewBox", `0 0 ${pw} ${ph}`);

  overlaySvg.innerHTML = "";
  const fragment = document.createDocumentFragment();
  const dotRadius = dotSizeSlider.value;
  currentPoints.forEach((pt, idx) => {
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("cx", pt.x * previewScale);
    circle.setAttribute("cy", pt.y * previewScale);
    circle.setAttribute("r", dotRadius);
    circle.dataset.idx = idx;
    fragment.appendChild(circle);
  });
  overlaySvg.appendChild(fragment);

  // コンテナに合わせて全体を表示するスケールを初期値にする
  const fitScale = viewerEl.clientWidth / pw;
  viewTransform = { scale: fitScale, tx: 0, ty: 0, fitScale };
  applyViewerTransform();

  updateSelectionCount();
  renderSelectedList();
  applyImageFilter();
  refinePanel.hidden = false;
  document.getElementById("refine-result").hidden = true;
  document.getElementById("lorentz-panel").hidden = false;
}

// =============================================================================
// コントラスト・明るさ調整 (画像のみに適用し、反射点ドットの色には影響させない)
// =============================================================================

const contrastSlider = document.getElementById("contrast-slider");
const brightnessSlider = document.getElementById("brightness-slider");
const contrastValueEl = document.getElementById("contrast-value");
const brightnessValueEl = document.getElementById("brightness-value");
const contrastResetBtn = document.getElementById("contrast-reset-btn");
const dotSizeSlider = document.getElementById("dot-size-slider");
const dotSizeValueEl = document.getElementById("dot-size-value");

function applyImageFilter() {
  const contrast = contrastSlider.value;
  const brightness = brightnessSlider.value;
  reflectionsPreviewEl.style.filter = `contrast(${contrast}%) brightness(${brightness}%)`;
  contrastValueEl.textContent = `${contrast}%`;
  brightnessValueEl.textContent = `${brightness}%`;
}

function applyDotSize() {
  const radius = dotSizeSlider.value;
  dotSizeValueEl.textContent = `${radius}px`;
  overlaySvg.querySelectorAll("circle").forEach((circle) => circle.setAttribute("r", radius));
}

contrastSlider.addEventListener("input", applyImageFilter);
brightnessSlider.addEventListener("input", applyImageFilter);
dotSizeSlider.addEventListener("input", applyDotSize);
contrastResetBtn.addEventListener("click", () => {
  contrastSlider.value = 100;
  brightnessSlider.value = 100;
  dotSizeSlider.value = 4;
  applyImageFilter();
  applyDotSize();
});

function getCircle(idx) {
  return overlaySvg.querySelector(`circle[data-idx="${idx}"]`);
}

// 選択した反射点のhkl指数をポップアップ表示する(ズーム・パンにも追従し、
// 一定時間後に自動で消える)。
function positionTooltip(idx) {
  const pt = currentPoints[idx];
  if (!pt) return;
  const stageX = pt.x * previewScale;
  const stageY = pt.y * previewScale;
  const inverseScale = 1 / viewTransform.scale;
  viewerTooltipEl.style.left = `${stageX}px`;
  viewerTooltipEl.style.top = `${stageY}px`;
  viewerTooltipEl.style.transform = `translate(-50%, -100%) scale(${inverseScale})`;
}

function showPointTooltip(idx) {
  const pt = currentPoints[idx];
  if (!pt) return;
  viewerTooltipEl.textContent = `(${pt.h}, ${pt.k}, ${pt.l})`;
  viewerTooltipEl.dataset.idx = idx;
  viewerTooltipEl.hidden = false;
  positionTooltip(idx);

  if (tooltipHideTimer) clearTimeout(tooltipHideTimer);
  tooltipHideTimer = setTimeout(hidePointTooltip, TOOLTIP_DURATION_MS);
}

function hidePointTooltip() {
  if (tooltipHideTimer) {
    clearTimeout(tooltipHideTimer);
    tooltipHideTimer = null;
  }
  viewerTooltipEl.hidden = true;
  delete viewerTooltipEl.dataset.idx;
}

function selectPoint(idx) {
  if (selectedIndices.has(idx)) return;
  selectedIndices.add(idx);
  const circle = getCircle(idx);
  if (circle) circle.classList.add("selected");
  updateSelectionCount();
  renderSelectedList();
  showPointTooltip(idx);
}

function deselectPoint(idx) {
  if (!selectedIndices.has(idx)) return;
  selectedIndices.delete(idx);
  const circle = getCircle(idx);
  if (circle) circle.classList.remove("selected");
  updateSelectionCount();
  renderSelectedList();
  if (!viewerTooltipEl.hidden && Number(viewerTooltipEl.dataset.idx) === idx) {
    hidePointTooltip();
  }
}

function toggleSelection(idx) {
  if (selectedIndices.has(idx)) {
    deselectPoint(idx);
  } else {
    selectPoint(idx);
  }
}

function setHovered(idx, hovered) {
  const circle = getCircle(idx);
  if (circle) circle.classList.toggle("hovered", hovered);
  const li = selectedListEl.querySelector(`li[data-idx="${idx}"]`);
  if (li) li.classList.toggle("hovered", hovered);
}

function centerOnPoint(idx) {
  const pt = currentPoints[idx];
  if (!pt) return;
  const stageX = pt.x * previewScale;
  const stageY = pt.y * previewScale;
  viewTransform.tx = viewerEl.clientWidth / 2 - stageX * viewTransform.scale;
  viewTransform.ty = viewerEl.clientHeight / 2 - stageY * viewTransform.scale;
  applyViewerTransform();
}

function renderSelectedList() {
  selectedListEl.innerHTML = "";
  const fragment = document.createDocumentFragment();
  for (const idx of selectedIndices) {
    const pt = currentPoints[idx];
    const li = document.createElement("li");
    li.dataset.idx = idx;
    li.title = t("msg.click_to_center");

    const label = document.createElement("span");
    label.textContent = `(${pt.h}, ${pt.k}, ${pt.l})`;
    li.appendChild(label);

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "×";
    removeBtn.title = t("msg.deselect");
    removeBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      deselectPoint(idx);
    });
    li.appendChild(removeBtn);

    li.addEventListener("click", () => centerOnPoint(idx));
    li.addEventListener("mouseenter", () => setHovered(idx, true));
    li.addEventListener("mouseleave", () => setHovered(idx, false));

    fragment.appendChild(li);
  }
  selectedListEl.appendChild(fragment);
}

function applyViewerTransform() {
  viewerStageEl.style.transform =
    `translate(${viewTransform.tx}px, ${viewTransform.ty}px) scale(${viewTransform.scale})`;
  // ポップアップの位置(stage内の静的な座標)はステージと一緒に動くが、文字が
  // 拡大縮小されないよう、ズーム倍率の逆数を毎回かけ直す。
  if (!viewerTooltipEl.hidden && viewerTooltipEl.dataset.idx !== undefined) {
    positionTooltip(Number(viewerTooltipEl.dataset.idx));
  }
}

function minPointsRequired() {
  return CRYSTAL_SYSTEMS[crystalSystemSelect.value].freeCount;
}

function hasL0Selected() {
  return Array.from(selectedIndices).some((idx) => currentPoints[idx].l === 0);
}

function updateSelectionCount() {
  const min = minPointsRequired();
  const l0Ok = hasL0Selected();
  const l0Note = l0Ok ? t("msg.l0_included") : t("msg.l0_required");
  selectionCountEl.textContent = tf("msg.selection_count", { count: selectedIndices.size, min, l0Note });
  selectionCountEl.classList.toggle("error", !l0Ok);
  refineBtn.disabled = selectedIndices.size < min || !l0Ok;
}

viewerEl.addEventListener("mousedown", (event) => {
  if (event.button !== 0) return;
  panState = {
    startMx: event.clientX,
    startMy: event.clientY,
    startTx: viewTransform.tx,
    startTy: viewTransform.ty,
  };
  dragDistance = 0;
  viewerEl.classList.add("dragging");
});

window.addEventListener("mousemove", (event) => {
  if (!panState) return;
  const dx = event.clientX - panState.startMx;
  const dy = event.clientY - panState.startMy;
  dragDistance = Math.max(dragDistance, Math.hypot(dx, dy));
  viewTransform.tx = panState.startTx + dx;
  viewTransform.ty = panState.startTy + dy;
  applyViewerTransform();
});

window.addEventListener("mouseup", () => {
  if (panState) {
    panState = null;
    viewerEl.classList.remove("dragging");
  }
});

viewerEl.addEventListener("wheel", (event) => {
  event.preventDefault();
  const rect = viewerEl.getBoundingClientRect();
  const mx = event.clientX - rect.left;
  const my = event.clientY - rect.top;
  const stageX = (mx - viewTransform.tx) / viewTransform.scale;
  const stageY = (my - viewTransform.ty) / viewTransform.scale;

  const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
  const minScale = viewTransform.fitScale * 0.4;
  const maxScale = viewTransform.fitScale * 25;
  const newScale = Math.min(maxScale, Math.max(minScale, viewTransform.scale * factor));

  viewTransform.tx = mx - stageX * newScale;
  viewTransform.ty = my - stageY * newScale;
  viewTransform.scale = newScale;
  applyViewerTransform();
}, { passive: false });

overlaySvg.addEventListener("click", (event) => {
  if (dragDistance > CLICK_DRAG_THRESHOLD) return;
  const target = event.target;
  if (target.tagName !== "circle") return;
  toggleSelection(Number(target.dataset.idx));
});

overlaySvg.addEventListener("mouseover", (event) => {
  if (event.target.tagName !== "circle") return;
  setHovered(Number(event.target.dataset.idx), true);
});

overlaySvg.addEventListener("mouseout", (event) => {
  if (event.target.tagName !== "circle") return;
  setHovered(Number(event.target.dataset.idx), false);
});

viewerResetBtn.addEventListener("click", () => {
  viewTransform.scale = viewTransform.fitScale;
  viewTransform.tx = 0;
  viewTransform.ty = 0;
  applyViewerTransform();
});

clearSelectionBtn.addEventListener("click", () => {
  for (const idx of selectedIndices) {
    const circle = getCircle(idx);
    if (circle) circle.classList.remove("selected");
  }
  selectedIndices.clear();
  updateSelectionCount();
  renderSelectedList();
  hidePointTooltip();
});

// =============================================================================
// 4. ユニットセル精密化
// =============================================================================

const refineStatusEl = document.getElementById("refine-status");
const refineResultEl = document.getElementById("refine-result");
const refineStatsEl = document.getElementById("refine-stats");
const refineTableEl = document.getElementById("refine-table");
const applyRefinedBtn = document.getElementById("apply-refined-btn");

// 値 ± 誤差 の形式で表示する項目。[値キー, 誤差キー, ラベル, 小数桁数]
const REFINE_VALUE_ERR_ROWS = [
  ["a", "a_err", "refine.row_a", 5],
  ["b", "b_err", "refine.row_b", 5],
  ["c", "c_err", "refine.row_c", 5],
  ["alpha_deg", "alpha_err_deg", "refine.row_alpha", 3],
  ["beta_deg", "beta_err_deg", "refine.row_beta", 3],
  ["gamma_deg", "gamma_err_deg", "refine.row_gamma", 3],
];

let lastRefinedCell = null;
let lastRefinePoints = null;

function setRefineStatus(message, isError = false) {
  refineStatusEl.textContent = message;
  refineStatusEl.classList.toggle("error", isError);
}

function renderRefineStats(refined) {
  refineStatsEl.innerHTML = "";
  const addRow = (label, valueHtml) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.innerHTML = valueHtml;
    refineStatsEl.appendChild(dt);
    refineStatsEl.appendChild(dd);
  };

  for (const [valueKey, errKey, labelKey, decimals] of REFINE_VALUE_ERR_ROWS) {
    const value = refined[valueKey].toFixed(decimals);
    const err = refined[errKey].toFixed(decimals);
    addRow(t(labelKey), `${value} &plusmn; ${err}`);
  }
  addRow(t("refine.row_free_params"), refined.free_params.join(", "));
  addRow(t("refine.row_chi2"), refined.chi2.toExponential(3));
  addRow(t("refine.row_dof"), String(refined.dof));
  addRow(t("refine.row_pvalue"), refined.pvalue.toExponential(3));
}

// l の値ごと(大きい方から: 0, -1, -2, ... または 2, 1, 0, -1, ...)にグループ化し、
// 各グループ内では面間隔 d_obs の大きい順(低角側から)に並べる。ピーク未検出の点は末尾にまとめる。
function sortRefinePoints(points) {
  const found = points.filter((pt) => pt.found);
  const notFound = points.filter((pt) => !pt.found);
  found.sort((a, b) => (b.l - a.l) || (b.d_obs - a.d_obs));
  return [...found, ...notFound];
}

function renderRefineTable(points) {
  const sorted = sortRefinePoints(points);
  let prevL = null;
  const rows = sorted.map((pt) => {
    const hklLabel = `(${pt.h}, ${pt.k}, ${pt.l})`;
    const groupStart = pt.found && pt.l !== prevL;
    if (pt.found) prevL = pt.l;
    const rowClass = groupStart ? ' class="group-start"' : "";

    if (!pt.found) {
      return `<tr${rowClass}><td>${hklLabel}</td><td class="not-found" colspan="5">${t("refine.peak_not_found")}</td></tr>`;
    }
    return `<tr${rowClass}>
      <td>${hklLabel}</td>
      <td>${pt.d_obs.toFixed(5)}</td>
      <td>${pt.d_calc.toFixed(5)}</td>
      <td>${pt.diff.toFixed(5)}</td>
      <td>${pt.gamma_r_px.toFixed(2)}</td>
      <td>${pt.sigma_phi_deg.toFixed(2)}</td>
    </tr>`;
  }).join("");

  refineTableEl.innerHTML = `
    <thead>
      <tr><th>${t("refine.table_hkl")}</th><th>${t("refine.table_d_obs")}</th><th>${t("refine.table_d_calc")}</th><th>${t("refine.table_diff")}</th><th>${t("refine.table_gamma_r")}</th><th>${t("refine.table_sigma_phi")}</th></tr>
    </thead>
    <tbody>${rows}</tbody>
  `;
}

refineBtn.addEventListener("click", async () => {
  const filename = filenameSelect.value;
  if (!filename || selectedIndices.size < minPointsRequired() || !hasL0Selected()) return;
  const fileBytes = loadedFiles.get(filename);
  if (!fileBytes) {
    setRefineStatus(t("msg.file_data_missing"), true);
    return;
  }

  const points = Array.from(selectedIndices).map((idx) => {
    const pt = currentPoints[idx];
    return { h: pt.h, k: pt.k, l: pt.l, x: pt.x, y: pt.y };
  });

  const payload = {
    filename,
    img_width: Number(document.getElementById("img_width").value),
    img_height: Number(document.getElementById("img_height").value),
    img_bit: Number(document.getElementById("img_bit").value),
    cx: Number(document.getElementById("tilt_cx").value),
    cy: Number(document.getElementById("tilt_cy").value),
    tilt_angle_deg: Number(document.getElementById("tilt_angle_deg").value),
    D: Number(document.getElementById("D").value),
    lamda: Number(document.getElementById("lamda").value),
    p: Number(document.getElementById("p").value),
    crystal_system: crystalSystemSelect.value,
    a: Number(document.getElementById("a").value),
    b: Number(document.getElementById("b").value),
    c: Number(document.getElementById("c").value),
    alpha_deg: Number(document.getElementById("alpha_deg").value),
    beta_deg: Number(document.getElementById("beta_deg").value),
    gamma_deg: Number(document.getElementById("gamma_deg").value),
    r_window: Number(document.getElementById("refine_r_window").value),
    phi_window: Number(document.getElementById("refine_phi_window").value),
    points,
  };

  refineBtn.disabled = true;
  setRefineStatus(t("msg.refine_calculating"));
  refineResultEl.hidden = true;

  try {
    await initPyodideWorker();
    const data = await pyCall("refine_unit_cell", payload, fileBytes);
    lastRefinedCell = data.refined;
    lastRefinePoints = data.points;
    renderRefineStats(data.refined);
    renderRefineTable(data.points);
    refineResultEl.hidden = false;
    setRefineStatus(tf("msg.refine_done", { n_used: data.n_used }));
    document.getElementById("peakwidth_r_window").value = document.getElementById("refine_r_window").value;
    document.getElementById("peakwidth_phi_window").value = document.getElementById("refine_phi_window").value;
    document.getElementById("peakwidth-panel").hidden = false;
  } catch (err) {
    setRefineStatus(tf("msg.refine_failed", { error: err.message }), true);
  } finally {
    refineBtn.disabled = selectedIndices.size < minPointsRequired();
  }
});

applyRefinedBtn.addEventListener("click", () => {
  if (!lastRefinedCell) return;
  document.getElementById("a").value = lastRefinedCell.a;
  document.getElementById("b").value = lastRefinedCell.b;
  document.getElementById("c").value = lastRefinedCell.c;
  document.getElementById("alpha_deg").value = lastRefinedCell.alpha_deg;
  document.getElementById("beta_deg").value = lastRefinedCell.beta_deg;
  document.getElementById("gamma_deg").value = lastRefinedCell.gamma_deg;
});

const csvFilenameInput = document.getElementById("csv-filename");
const exportCsvBtn = document.getElementById("export-csv-btn");

function csvEscape(value) {
  const s = String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

exportCsvBtn.addEventListener("click", () => {
  if (!lastRefinePoints || !lastRefinedCell) return;

  const lines = [];
  lines.push("# unit cell refinement result");
  for (const [valueKey, errKey, label] of REFINE_VALUE_ERR_ROWS) {
    lines.push(`# ${label} = ${lastRefinedCell[valueKey]} +/- ${lastRefinedCell[errKey]}`);
  }
  lines.push(`# free_params = ${lastRefinedCell.free_params.join(" ")}`);
  lines.push(`# chi2 = ${lastRefinedCell.chi2}, dof = ${lastRefinedCell.dof}, pvalue = ${lastRefinedCell.pvalue}`);

  const header = [
    "h", "k", "l", "d_obs_nm", "d_calc_nm", "abs_diff_nm",
    "gamma_r_px", "sigma_phi_deg", "found",
  ];
  lines.push(header.join(","));

  for (const pt of sortRefinePoints(lastRefinePoints)) {
    const row = pt.found
      ? [pt.h, pt.k, pt.l, pt.d_obs, pt.d_calc, pt.diff, pt.gamma_r_px, pt.sigma_phi_deg, "true"]
      : [pt.h, pt.k, pt.l, "", "", "", "", "", "false"];
    lines.push(row.map(csvEscape).join(","));
  }

  const csvContent = lines.join("\n");
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);

  let filename = csvFilenameInput.value.trim() || "refined_reflections.csv";
  if (!filename.toLowerCase().endsWith(".csv")) filename += ".csv";

  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
});

// =============================================================================
// 5. ローレンツ・偏光補正
// =============================================================================

const lorentzForm = document.getElementById("lorentz-form");
const lorentzBtn = document.getElementById("lorentz-btn");
const lorentzStatusEl = document.getElementById("lorentz-status");
const lorentzResultPanel = document.getElementById("lorentz-result");
const lorentzStatsEl = document.getElementById("lorentz-stats");
const lorentzPreviewEl = document.getElementById("lorentz-preview");

const LORENTZ_STAT_LABELS = {
  mean_before: "平均強度 (補正前)",
  mean_after: "平均強度 (補正後)",
  valid_fraction: "有効画素の割合",
};

function setLorentzStatus(message, isError = false) {
  lorentzStatusEl.textContent = message;
  lorentzStatusEl.classList.toggle("error", isError);
}

lorentzForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const filename = filenameSelect.value;
  if (!filename) {
    setLorentzStatus(t("msg.select_file_first"), true);
    return;
  }
  const fileBytes = loadedFiles.get(filename);
  if (!fileBytes) {
    setLorentzStatus(t("msg.file_data_missing"), true);
    return;
  }

  const payload = {
    filename,
    img_width: Number(document.getElementById("img_width").value),
    img_height: Number(document.getElementById("img_height").value),
    img_bit: Number(document.getElementById("img_bit").value),
    cx: Number(document.getElementById("tilt_cx").value),
    cy: Number(document.getElementById("tilt_cy").value),
    tilt_angle_deg: Number(document.getElementById("tilt_angle_deg").value),
    D: Number(document.getElementById("D").value),
    lamda: Number(document.getElementById("lamda").value),
    p: Number(document.getElementById("p").value),
    A: Number(document.getElementById("polar_a").value),
    beta_fiber_deg: Number(document.getElementById("phi_deg").value),
    fiber_axis: document.getElementById("fiber_axis").value,
  };

  lorentzBtn.disabled = true;
  setLorentzStatus(t("msg.lorentz_calculating"));
  lorentzResultPanel.hidden = true;

  try {
    await initPyodideWorker();
    const data = await pyCall("lorentz_correction", payload, fileBytes);
    renderStats(lorentzStatsEl, data.stats, LORENTZ_STAT_LABELS);
    lorentzPreviewEl.src = `data:image/png;base64,${data.preview_png_base64}`;
    lorentzResultPanel.hidden = false;
    setLorentzStatus(t("msg.lorentz_done"));
    document.getElementById("background-panel").hidden = false;
  } catch (err) {
    setLorentzStatus(tf("msg.lorentz_failed", { error: err.message }), true);
  } finally {
    lorentzBtn.disabled = false;
  }
});

// =============================================================================
// 7. ピーク幅の算出
// =============================================================================

const peakwidthBtn = document.getElementById("peakwidth-btn");
const peakwidthRefitBtn = document.getElementById("peakwidth-refit-btn");
const peakwidthStatusEl = document.getElementById("peakwidth-status");
const peakwidthResultEl = document.getElementById("peakwidth-result");
const peakwidthStatsEl = document.getElementById("peakwidth-stats");
const peakwidthTableEl = document.getElementById("peakwidth-table");
const peakwidthBStatsEl = document.getElementById("peakwidth-b-stats");
const peakwidthBTableEl = document.getElementById("peakwidth-b-table");

const PEAKWIDTH_VALUE_ERR_ROWS = [
  ["wm", "wm_err", "peakwidth.row_wm"],
  ["weq", "weq_err", "peakwidth.row_weq"],
  ["c", "c_err", "peakwidth.row_c"],
];

let lastPeakWidthFit = null;
let lastOrientationWidth = null;
let lastPeakWidthAllPoints = null; // 除外前の全点(再フィット時にここから除外を反映する)

function setPeakwidthStatus(message, isError = false) {
  peakwidthStatusEl.textContent = message;
  peakwidthStatusEl.classList.toggle("error", isError);
}

function renderPeakwidthStats(fit) {
  peakwidthStatsEl.innerHTML = "";
  const addRow = (label, valueHtml) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.innerHTML = valueHtml;
    peakwidthStatsEl.appendChild(dt);
    peakwidthStatsEl.appendChild(dd);
  };

  addRow(t("peakwidth.row_w0"), fit.w0.toFixed(4));
  for (const [valueKey, errKey, labelKey] of PEAKWIDTH_VALUE_ERR_ROWS) {
    addRow(t(labelKey), `${fit[valueKey].toFixed(4)} &plusmn; ${fit[errKey].toFixed(4)}`);
  }
  addRow(t("peakwidth.row_chi2"), fit.chi2.toExponential(3));
  addRow(t("peakwidth.row_dof"), String(fit.dof));
  addRow(t("peakwidth.row_pvalue"), fit.pvalue.toExponential(3));
}

function renderPeakwidthBStats(B, nL0Fitted) {
  peakwidthBStatsEl.innerHTML = "";
  const addRow = (label, valueHtml) => {
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.innerHTML = valueHtml;
    peakwidthBStatsEl.appendChild(dt);
    peakwidthBStatsEl.appendChild(dd);
  };

  addRow(t("peakwidth.row_B"), B.toFixed(3));
  addRow(t("peakwidth.row_n_l0_fitted"), String(nL0Fitted));
}

// hklだけだと対称等価な点(l=0反射がphi=0側と180側の両方で選ばれた場合など)が
// 衝突するため、実測位置(r0_px, phi0_deg)も含めて一意なキーにする。
function peakwidthPointKey(pt) {
  return `${pt.h},${pt.k},${pt.l},${pt.r0_px.toFixed(3)},${pt.phi0_deg.toFixed(3)}`;
}

function renderPeakwidthTable(points) {
  const sorted = [...points].sort((a, b) => a.dstar - b.dstar);
  const rows = sorted.map((pt) => {
    const diff = pt.gamma_r_obs - pt.gamma_r_calc;
    return `<tr>
      <td><input type="checkbox" class="peakwidth-exclude-cb" data-hkl="${peakwidthPointKey(pt)}"></td>
      <td>(${pt.h}, ${pt.k}, ${pt.l})</td>
      <td>${pt.dstar.toFixed(4)}</td>
      <td>${pt.sigma_deg.toFixed(2)}</td>
      <td>${pt.gamma_r_obs.toFixed(3)}</td>
      <td>${pt.gamma_r_calc.toFixed(3)}</td>
      <td>${diff >= 0 ? "+" : ""}${diff.toFixed(3)}</td>
    </tr>`;
  }).join("");

  peakwidthTableEl.innerHTML = `
    <thead>
      <tr><th>${t("peakwidth.table_exclude")}</th><th>${t("peakwidth.table_hkl")}</th><th>${t("peakwidth.table_dstar")}</th><th>${t("peakwidth.table_sigma")}</th><th>${t("peakwidth.table_gamma_r_obs")}</th><th>${t("peakwidth.table_gamma_r_calc")}</th><th>${t("peakwidth.table_diff")}</th></tr>
    </thead>
    <tbody>${rows}</tbody>
  `;
}

function renderPeakwidthBTable(bPoints) {
  const sorted = [...bPoints].sort((a, b) => a.R0 - b.R0);
  const rows = sorted.map((pt) => `<tr>
    <td>(${pt.h}, ${pt.k}, ${pt.l})</td>
    <td>${pt.R0.toFixed(2)}</td>
    <td>${pt.phi0.toFixed(2)}</td>
    <td>${pt.sigma_deg.toFixed(3)}</td>
  </tr>`).join("");

  peakwidthBTableEl.innerHTML = `
    <thead>
      <tr><th>${t("peakwidth.b_table_hkl")}</th><th>${t("peakwidth.b_table_R0")}</th><th>${t("peakwidth.b_table_phi0")}</th><th>${t("peakwidth.b_table_sigma")}</th></tr>
    </thead>
    <tbody>${rows}</tbody>
  `;
}

async function runPeakWidthFit(points) {
  if (points.length < 4) {
    setPeakwidthStatus(tf("msg.peakwidth_min_points", { count: points.length }), true);
    return;
  }
  if (!points.some((pt) => pt.l === 0)) {
    setPeakwidthStatus(t("msg.peakwidth_no_l0"), true);
    return;
  }

  const payload = {
    background_job_id: currentBackgroundJobId,
    a: Number(document.getElementById("a").value),
    b: Number(document.getElementById("b").value),
    c: Number(document.getElementById("c").value),
    alpha_deg: Number(document.getElementById("alpha_deg").value),
    beta_deg: Number(document.getElementById("beta_deg").value),
    gamma_deg: Number(document.getElementById("gamma_deg").value),
    phi_deg: Number(document.getElementById("phi_deg").value),
    lamda: Number(document.getElementById("lamda").value),
    p: Number(document.getElementById("p").value),
    beam_size_nm: Number(document.getElementById("beam_size_nm").value),
    r_window: Number(document.getElementById("peakwidth_r_window").value),
    phi_window_deg: Number(document.getElementById("peakwidth_phi_window").value),
    r_avg_width: Number(document.getElementById("peakwidth_r_avg_width").value),
    points,
  };

  peakwidthBtn.disabled = true;
  peakwidthRefitBtn.disabled = true;
  setPeakwidthStatus(t("msg.peakwidth_fitting"));
  peakwidthResultEl.hidden = true;

  try {
    await initPyodideWorker();
    const data = await pyCall("peak_width_fit", payload);
    renderPeakwidthStats(data.fit);
    renderPeakwidthTable(data.points);
    renderPeakwidthBStats(data.B, data.n_l0_fitted);
    renderPeakwidthBTable(data.b_points);
    peakwidthResultEl.hidden = false;
    setPeakwidthStatus(tf("msg.peakwidth_done", { radial: data.points.length, b: data.n_l0_fitted }));

    lastPeakWidthFit = data.fit;
    lastOrientationWidth = { B: data.B, n_l0_fitted: data.n_l0_fitted };
    document.getElementById("blockfit-panel").hidden = false;
  } catch (err) {
    setPeakwidthStatus(tf("msg.peakwidth_failed", { error: err.message }), true);
  } finally {
    peakwidthBtn.disabled = false;
    peakwidthRefitBtn.disabled = false;
  }
}

peakwidthBtn.addEventListener("click", () => {
  if (!currentBackgroundJobId) {
    setPeakwidthStatus(t("msg.peakwidth_need_background"), true);
    return;
  }
  if (!lastRefinePoints) {
    setPeakwidthStatus(t("msg.peakwidth_need_refine"), true);
    return;
  }

  const points = lastRefinePoints
    .filter((pt) => pt.found)
    .map((pt) => ({ h: pt.h, k: pt.k, l: pt.l, d_obs: pt.d_obs, r0_px: pt.r0_px, phi0_deg: pt.phi0_deg }));

  lastPeakWidthAllPoints = points;
  runPeakWidthFit(points);
});

peakwidthRefitBtn.addEventListener("click", () => {
  if (!lastPeakWidthAllPoints) {
    setPeakwidthStatus(t("msg.peakwidth_need_first_fit"), true);
    return;
  }
  const excluded = new Set(
    Array.from(peakwidthTableEl.querySelectorAll(".peakwidth-exclude-cb:checked")).map((cb) => cb.dataset.hkl)
  );
  const points = lastPeakWidthAllPoints.filter((pt) => !excluded.has(peakwidthPointKey(pt)));
  runPeakWidthFit(points);
});

// =============================================================================
// 6. バックグラウンド除去
// =============================================================================

const backgroundForm = document.getElementById("background-form");
const backgroundBtn = document.getElementById("background-btn");
const backgroundStatusEl = document.getElementById("background-status");
const backgroundResultPanel = document.getElementById("background-result");
const backgroundStatsEl = document.getElementById("background-stats");
const backgroundObservedPreviewEl = document.getElementById("background-observed-preview");
const backgroundBgPreviewEl = document.getElementById("background-bg-preview");
const backgroundSubPreviewEl = document.getElementById("background-sub-preview");

const BACKGROUND_STAT_LABELS = {
  n_phi: "方位角ビン数",
  n_r: "動径ビン数",
  max_subtracted: "最大強度 (差し引き後)",
  mean_subtracted: "平均強度 (差し引き後)",
};

function setBackgroundStatus(message, isError = false) {
  backgroundStatusEl.textContent = message;
  backgroundStatusEl.classList.toggle("error", isError);
}

backgroundForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const filename = filenameSelect.value;
  if (!filename) {
    setBackgroundStatus(t("msg.select_file_first"), true);
    return;
  }
  const fileBytes = loadedFiles.get(filename);
  if (!fileBytes) {
    setBackgroundStatus(t("msg.file_data_missing"), true);
    return;
  }

  const payload = {
    filename,
    img_width: Number(document.getElementById("img_width").value),
    img_height: Number(document.getElementById("img_height").value),
    img_bit: Number(document.getElementById("img_bit").value),
    cx: Number(document.getElementById("tilt_cx").value),
    cy: Number(document.getElementById("tilt_cy").value),
    tilt_angle_deg: Number(document.getElementById("tilt_angle_deg").value),
    D: Number(document.getElementById("D").value),
    lamda: Number(document.getElementById("lamda").value),
    p: Number(document.getElementById("p").value),
    A: Number(document.getElementById("polar_a").value),
    beta_fiber_deg: Number(document.getElementById("phi_deg").value),
    fiber_axis: document.getElementById("fiber_axis").value,
    n_phi: Number(document.getElementById("n_phi").value),
    n_r: Number(document.getElementById("n_r").value),
    r_min: Number(document.getElementById("r_min").value),
    grid_spacing: Number(document.getElementById("grid_spacing").value),
    n_iter: Number(document.getElementById("n_iter").value),
    sigma_scale: Number(document.getElementById("sigma_scale").value),
    window: Number(document.getElementById("window").value),
    smooth_sigma_phi: Number(document.getElementById("smooth_sigma_phi").value),
    smooth_sigma_r: Number(document.getElementById("smooth_sigma_r").value),
  };

  backgroundBtn.disabled = true;
  setBackgroundStatus(t("msg.background_starting"));
  backgroundResultPanel.hidden = true;

  try {
    await initPyodideWorker();
    const data = await pyCallWithProgress("background_removal", payload, (done, total) => {
      const percent = total > 0 ? (done / total) * 100 : 0;
      setBackgroundStatus(tf("msg.background_estimating_percent", { percent: percent.toFixed(0) }));
    }, fileBytes);

    renderStats(backgroundStatsEl, data.stats, BACKGROUND_STAT_LABELS);
    backgroundObservedPreviewEl.src = `data:image/png;base64,${data.observed_png_base64}`;
    backgroundBgPreviewEl.src = `data:image/png;base64,${data.background_png_base64}`;
    backgroundSubPreviewEl.src = `data:image/png;base64,${data.subtracted_png_base64}`;
    backgroundResultPanel.hidden = false;
    setBackgroundStatus(t("msg.background_done"));

    currentBackgroundJobId = data.job_id;
    currentBackgroundNPhi = data.stats.n_phi;
    profilePhiSlider.max = currentBackgroundNPhi - 1;
    profilePhiSlider.value = 0;
    profilePhiMaxEl.textContent = currentBackgroundNPhi - 1;
    updateProfilePhiLabel(0);
    loadProfile(0);
    updateIndicatorLines(0);
    document.getElementById("peakwidth-panel").hidden = false;
    if (lastPeakWidthFit) document.getElementById("blockfit-panel").hidden = false;
  } catch (err) {
    setBackgroundStatus(tf("msg.background_failed", { error: err.message }), true);
  } finally {
    backgroundBtn.disabled = false;
  }
});

// =============================================================================
// 6. バックグラウンド除去 — 動径方向 1Dプロファイルのインタラクティブ表示
// =============================================================================

const profilePhiSlider = document.getElementById("profile-phi-slider");
const profilePhiValueEl = document.getElementById("profile-phi-value");
const profilePhiBinEl = document.getElementById("profile-phi-bin");
const profilePhiMaxEl = document.getElementById("profile-phi-max");
const profileChartEl = document.getElementById("profile-chart");
const profileStatusEl = document.getElementById("profile-status");

let currentBackgroundJobId = null;
let currentBackgroundNPhi = 360;
let profileRequestSeq = 0;

function setProfileStatus(message, isError = false) {
  profileStatusEl.textContent = message;
  profileStatusEl.classList.toggle("error", isError);
}

function updateProfilePhiLabel(bin) {
  const deg = (bin * 360 / currentBackgroundNPhi).toFixed(1);
  profilePhiValueEl.textContent = deg;
  profilePhiBinEl.textContent = bin;
}

async function loadProfile(phiBin) {
  if (!currentBackgroundJobId) return;
  const seq = ++profileRequestSeq;
  setProfileStatus(t("msg.profile_loading"));
  try {
    await initPyodideWorker();
    const data = await pyCall("background_removal_profile", { background_job_id: currentBackgroundJobId, phi: phiBin });
    if (seq !== profileRequestSeq) return; // スライダーが素早く動かされた場合、古い応答は捨てる
    renderProfileChart(data);
    setProfileStatus("");
  } catch (err) {
    if (seq !== profileRequestSeq) return;
    setProfileStatus(tf("msg.profile_failed", { error: err.message }), true);
  }
}

function renderProfileChart(data) {
  const { observed, background, subtracted, n_r } = data;
  // viewBoxを実際の表示ピクセルサイズに合わせることで、非等倍スケーリングによる
  // 文字の潰れ(縦横比が合わずテキストが縦につぶれる/横に伸びる)を防ぐ。
  const width = profileChartEl.clientWidth || 900;
  const height = profileChartEl.clientHeight || 260;
  profileChartEl.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const marginLeft = 56;
  const marginRight = 10;
  const marginTop = 10;
  const marginBottom = 26;
  const plotW = width - marginLeft - marginRight;
  const plotH = height - marginTop - marginBottom;

  const maxVal = Math.max(...observed, 1);

  const xScale = (r) => marginLeft + (r / (n_r - 1)) * plotW;
  const yScale = (v) => marginTop + plotH - (Math.max(v, 0) / maxVal) * plotH;

  const toPoints = (arr) => arr.map((v, i) => `${xScale(i).toFixed(1)},${yScale(v).toFixed(1)}`).join(" ");

  const observedPoints = toPoints(observed);
  const backgroundPoints = toPoints(background);
  const subtractedPoints = toPoints(subtracted);
  const baselineY = (marginTop + plotH).toFixed(1);
  const subtractedArea = `${marginLeft},${baselineY} ${subtractedPoints} ${(marginLeft + plotW).toFixed(1)},${baselineY}`;

  profileChartEl.innerHTML = `
    <line x1="${marginLeft}" y1="${marginTop}" x2="${marginLeft}" y2="${marginTop + plotH}" stroke="#2a2e38" />
    <line x1="${marginLeft}" y1="${marginTop + plotH}" x2="${marginLeft + plotW}" y2="${marginTop + plotH}" stroke="#2a2e38" />
    <text x="${marginLeft}" y="${height - 8}" fill="#9aa1ac" font-size="11">0</text>
    <text x="${marginLeft + plotW}" y="${height - 8}" fill="#9aa1ac" font-size="11" text-anchor="end">${n_r}</text>
    <text x="6" y="${marginTop + 10}" fill="#9aa1ac" font-size="11">${maxVal.toFixed(0)}</text>
    <text x="6" y="${marginTop + plotH}" fill="#9aa1ac" font-size="11">0</text>
    <polygon points="${subtractedArea}" fill="rgba(79,157,255,0.22)" stroke="none" />
    <polyline points="${observedPoints}" fill="none" stroke="#e6e8eb" stroke-width="1" />
    <polyline points="${backgroundPoints}" fill="none" stroke="#ff6b6b" stroke-width="1.3" />
    <polyline points="${subtractedPoints}" fill="none" stroke="#4f9dff" stroke-width="1.3" />
  `;
}

// 極座標画像(観測・背景・差し引き後)上に、現在選択中の方位角断面を示す線を重ねる。
// 画像をクリックするとその断面にジャンプできる。
const backgroundIndicatorTargets = [
  { img: backgroundObservedPreviewEl, line: document.getElementById("indicator-observed") },
  { img: backgroundBgPreviewEl, line: document.getElementById("indicator-bg") },
  { img: backgroundSubPreviewEl, line: document.getElementById("indicator-sub") },
];

function updateIndicatorLines(phiBin) {
  const fraction = phiBin / (currentBackgroundNPhi - 1);
  for (const { img, line } of backgroundIndicatorTargets) {
    if (!img.naturalWidth) {
      line.style.display = "none";
      continue;
    }
    const wrapRect = img.parentElement.getBoundingClientRect();
    const imgRect = img.getBoundingClientRect();
    const contentTop = imgRect.top - wrapRect.top + 8; // img自身のpadding-topぶんを除く
    const contentHeight = imgRect.height - 16; // 上下のpadding(8px x2)を除く
    line.style.top = `${contentTop + fraction * contentHeight}px`;
    line.style.display = "block";
  }
}

function onBackgroundImageClick(event) {
  if (!currentBackgroundJobId) return;
  const img = event.currentTarget.querySelector("img");
  const imgRect = img.getBoundingClientRect();
  const contentTop = imgRect.top + 8;
  const contentHeight = imgRect.height - 16;
  const fraction = (event.clientY - contentTop) / contentHeight;
  const clamped = Math.min(Math.max(fraction, 0), 1);
  const bin = Math.round(clamped * (currentBackgroundNPhi - 1));

  profilePhiSlider.value = bin;
  updateProfilePhiLabel(bin);
  updateIndicatorLines(bin);
  loadProfile(bin);
}

backgroundIndicatorTargets.forEach(({ img }) => {
  img.addEventListener("load", () => updateIndicatorLines(Number(profilePhiSlider.value)));
  img.parentElement.addEventListener("click", onBackgroundImageClick);
});

window.addEventListener("resize", () => {
  if (currentBackgroundJobId) updateIndicatorLines(Number(profilePhiSlider.value));
});

profilePhiSlider.addEventListener("input", () => {
  const bin = Number(profilePhiSlider.value);
  updateProfilePhiLabel(bin);
  updateIndicatorLines(bin);
  loadProfile(bin);
});

// =============================================================================
// 8. ブロックフィット (強度抽出)
// =============================================================================

const blockfitBtn = document.getElementById("blockfit-btn");
const blockfitStatusEl = document.getElementById("blockfit-status");
const blockfitResultEl = document.getElementById("blockfit-result");
const blockfitStatsEl = document.getElementById("blockfit-stats");
const blockfitMergedTableEl = document.getElementById("blockfit-merged-table");
const blockfitMergeDspacingBtn = document.getElementById("blockfit-merge-dspacing-btn");
const blockfitMergeDspacingStatusEl = document.getElementById("blockfit-merge-dspacing-status");
const blockfitDspacingResultEl = document.getElementById("blockfit-dspacing-result");
const blockfitDspacingTableEl = document.getElementById("blockfit-dspacing-table");
const blockfitObservedPreviewEl = document.getElementById("blockfit-observed-preview");
const blockfitCalcPreviewEl = document.getElementById("blockfit-calc-preview");
const blockfitResidPreviewEl = document.getElementById("blockfit-resid-preview");

const blockfitProfilePhiSlider = document.getElementById("blockfit-profile-phi-slider");
const blockfitProfilePhiValueEl = document.getElementById("blockfit-profile-phi-value");
const blockfitProfilePhiBinEl = document.getElementById("blockfit-profile-phi-bin");
const blockfitProfilePhiMaxEl = document.getElementById("blockfit-profile-phi-max");
const blockfitProfileChartEl = document.getElementById("blockfit-profile-chart");
const blockfitProfileStatusEl = document.getElementById("blockfit-profile-status");

let currentBlockfitJobId = null;
let currentBlockfitNPhi = 360;
let blockfitProfileRequestSeq = 0;

const BLOCKFIT_STAT_LABELS = {
  n_reflections: "生成した反射数",
  n_used: "ブロックフィットで採用された反射数",
  n_skipped_range: "範囲外スキップ数",
  n_skipped_dup: "重複スキップ数",
  n_skipped_meridian: "子午線反射スキップ数",
  bg_flat: "平板背景 (ブロック中央値)",
  r_factor: "R因子",
  sigma_coeff: "σI overlap項の較正係数 (自動較正)",
  n_merged: "マージ後の反射数",
};

let lastMergedPoints = null;
let lastDspacingMergedPoints = null;
const BLOCKFIT_PEAK_PHI_TOLERANCE_BINS = 15; // このbin数以内にphi0があるピークを「このプロファイルに写っている」とみなす

function setBlockfitStatus(message, isError = false) {
  blockfitStatusEl.textContent = message;
  blockfitStatusEl.classList.toggle("error", isError);
}

function sortMergedPoints(points) {
  // lの昇順(l=0が先頭)、各l内では面間隔dの大きい順。
  return [...points].sort((a, b) => (a.l - b.l) || (b.d_hkl - a.d_hkl));
}

function renderMergedTable(tableEl, points) {
  const sorted = sortMergedPoints(points);
  const rows = sorted.map((pt) => `<tr>
    <td>(${pt.h}, ${pt.k}, ${pt.l})</td>
    <td>${pt.d_hkl.toFixed(5)}</td>
    <td>${pt.dstar.toFixed(4)}</td>
    <td>${pt.intensity.toFixed(3)}</td>
    <td>${pt.sigma_I.toFixed(3)}</td>
    <td>${pt.snr.toFixed(2)}</td>
    <td>${pt.multiplicity}</td>
  </tr>`).join("");

  tableEl.innerHTML = `
    <thead>
      <tr><th>${t("blockfit.table_hkl")}</th><th>${t("blockfit.table_d")}</th><th>${t("blockfit.table_dstar")}</th><th>${t("blockfit.table_intensity")}</th><th>${t("blockfit.table_sigma_intensity")}</th><th>${t("blockfit.table_snr")}</th><th>${t("blockfit.table_multiplicity")}</th></tr>
    </thead>
    <tbody>${rows}</tbody>
  `;
}

function renderBlockfitMergedTable(points) {
  renderMergedTable(blockfitMergedTableEl, points);
}

// phiBinの断面プロファイルに写っている(=phi0が近い)ピークを一覧から探す。
// 周期境界(0°/360°)をまたぐ場合も考慮する。
function findPeaksNearPhiBin(phiBin) {
  if (!lastMergedPoints) return [];
  const nPhi = currentBlockfitNPhi;
  return lastMergedPoints.filter((pt) =>
    (pt.phi0_list || []).some((phi0) => {
      const diff = Math.abs(((phi0 - phiBin + nPhi / 2) % nPhi) - nPhi / 2);
      return diff <= BLOCKFIT_PEAK_PHI_TOLERANCE_BINS;
    })
  );
}

function setBlockfitProfileStatus(message, isError = false) {
  blockfitProfileStatusEl.textContent = message;
  blockfitProfileStatusEl.classList.toggle("error", isError);
}

function updateBlockfitProfilePhiLabel(bin) {
  const deg = (bin * 360 / currentBlockfitNPhi).toFixed(1);
  blockfitProfilePhiValueEl.textContent = deg;
  blockfitProfilePhiBinEl.textContent = bin;
}

async function loadBlockfitProfile(phiBin) {
  if (!currentBlockfitJobId) return;
  const seq = ++blockfitProfileRequestSeq;
  setBlockfitProfileStatus(t("msg.profile_loading"));
  try {
    await initPyodideWorker();
    const data = await pyCall("block_fit_profile", { job_id: currentBlockfitJobId, phi: phiBin });
    if (seq !== blockfitProfileRequestSeq) return;
    const peaksHere = findPeaksNearPhiBin(phiBin);
    renderBlockfitProfileChart(data, peaksHere);
    setBlockfitProfileStatus(peaksHere.length > 0 ? tf("msg.blockfit_reflections_here", { count: peaksHere.length }) : t("msg.blockfit_no_reflections_here"));
  } catch (err) {
    if (seq !== blockfitProfileRequestSeq) return;
    setBlockfitProfileStatus(tf("msg.profile_failed", { error: err.message }), true);
  }
}

function renderBlockfitProfileChart(data, peaks = []) {
  const { observed, calc, residual, n_r } = data;
  // viewBoxを実際の表示ピクセルサイズに合わせ、文字の潰れを防ぐ(renderProfileChartと同様)。
  const width = blockfitProfileChartEl.clientWidth || 900;
  const height = blockfitProfileChartEl.clientHeight || 260;
  blockfitProfileChartEl.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const marginLeft = 56;
  const marginRight = 10;
  const marginTop = 10;
  const marginBottom = 26;
  const plotW = width - marginLeft - marginRight;
  const plotH = height - marginTop - marginBottom;

  const maxVal = Math.max(...observed, ...calc, ...residual.map(Math.abs), 1);

  const xScale = (r) => marginLeft + (r / (n_r - 1)) * plotW;
  const yScale = (v) => marginTop + plotH - (Math.max(v, 0) / maxVal) * plotH;

  const toPoints = (arr, scale) => arr.map((v, i) => `${xScale(i).toFixed(1)},${scale(v).toFixed(1)}`).join(" ");

  const observedPoints = toPoints(observed, yScale);
  const calcPoints = toPoints(calc, yScale);
  const residualPoints = toPoints(residual, yScale);

  // この断面に写っているピークを、動径位置にラベル付きの縦線で示す。
  // 近接するピークのラベルが重ならないよう、縦位置を数段に振り分ける。
  const peaksInRange = [...peaks]
    .filter((pt) => pt.r0_px >= 0 && pt.r0_px < n_r)
    .sort((a, b) => a.r0_px - b.r0_px);
  const labelLevels = 4;
  const markerSvg = peaksInRange.map((pt, i) => {
    const markerX = xScale(pt.r0_px).toFixed(1);
    const labelY = marginTop + 12 + (i % labelLevels) * 13;
    const hklLabel = `(${pt.h},${pt.k},${pt.l})`;
    return `
      <line x1="${markerX}" y1="${marginTop}" x2="${markerX}" y2="${marginTop + plotH}" stroke="#ff6b6b" stroke-width="1" stroke-dasharray="4,3" opacity="0.8" />
      <text x="${markerX}" y="${labelY}" fill="#ff6b6b" font-size="10" text-anchor="middle">${hklLabel}</text>
    `;
  }).join("");

  blockfitProfileChartEl.innerHTML = `
    <line x1="${marginLeft}" y1="${marginTop}" x2="${marginLeft}" y2="${marginTop + plotH}" stroke="#2a2e38" />
    <line x1="${marginLeft}" y1="${marginTop + plotH}" x2="${marginLeft + plotW}" y2="${marginTop + plotH}" stroke="#2a2e38" />
    <text x="${marginLeft}" y="${height - 8}" fill="#9aa1ac" font-size="11">0</text>
    <text x="${marginLeft + plotW}" y="${height - 8}" fill="#9aa1ac" font-size="11" text-anchor="end">${n_r}</text>
    <text x="6" y="${marginTop + 10}" fill="#9aa1ac" font-size="11">${maxVal.toFixed(0)}</text>
    <text x="6" y="${marginTop + plotH}" fill="#9aa1ac" font-size="11">0</text>
    <polyline points="${observedPoints}" fill="none" stroke="#e6e8eb" stroke-width="1" />
    <polyline points="${calcPoints}" fill="none" stroke="#4f9dff" stroke-width="1.3" />
    <polyline points="${residualPoints}" fill="none" stroke="#ffb347" stroke-width="1" />
    ${markerSvg}
  `;
}

// ブロックフィットの3画像(観測・計算・残差)上に、現在選択中の方位角断面を示す横線を重ねる。
// プレビュー画像は「6.」と同じ向き(phi方向が縦、r方向が横)にしているので、
// 断面線・クリック位置の扱いも6.と同じ(横線・Y座標)。
const blockfitIndicatorTargets = [
  { img: blockfitObservedPreviewEl, line: document.getElementById("blockfit-indicator-observed") },
  { img: blockfitCalcPreviewEl, line: document.getElementById("blockfit-indicator-calc") },
  { img: blockfitResidPreviewEl, line: document.getElementById("blockfit-indicator-resid") },
];

function updateBlockfitIndicatorLines(phiBin) {
  const fraction = phiBin / (currentBlockfitNPhi - 1);
  for (const { img, line } of blockfitIndicatorTargets) {
    if (!img.naturalWidth) {
      line.style.display = "none";
      continue;
    }
    const wrapRect = img.parentElement.getBoundingClientRect();
    const imgRect = img.getBoundingClientRect();
    const contentTop = imgRect.top - wrapRect.top + 8; // img自身のpadding-topぶんを除く
    const contentHeight = imgRect.height - 16; // 上下のpadding(8px x2)を除く
    line.style.top = `${contentTop + fraction * contentHeight}px`;
    line.style.display = "block";
  }
}

function onBlockfitImageClick(event) {
  if (!currentBlockfitJobId) return;
  const img = event.currentTarget.querySelector("img");
  const imgRect = img.getBoundingClientRect();
  const contentTop = imgRect.top + 8;
  const contentHeight = imgRect.height - 16;
  const fraction = (event.clientY - contentTop) / contentHeight;
  const clamped = Math.min(Math.max(fraction, 0), 1);
  const bin = Math.round(clamped * (currentBlockfitNPhi - 1));

  blockfitProfilePhiSlider.value = bin;
  updateBlockfitProfilePhiLabel(bin);
  updateBlockfitIndicatorLines(bin);
  loadBlockfitProfile(bin);
}

blockfitIndicatorTargets.forEach(({ img }) => {
  img.addEventListener("load", () => updateBlockfitIndicatorLines(Number(blockfitProfilePhiSlider.value)));
  img.parentElement.addEventListener("click", onBlockfitImageClick);
});

window.addEventListener("resize", () => {
  if (currentBlockfitJobId) updateBlockfitIndicatorLines(Number(blockfitProfilePhiSlider.value));
});

blockfitProfilePhiSlider.addEventListener("input", () => {
  const bin = Number(blockfitProfilePhiSlider.value);
  updateBlockfitProfilePhiLabel(bin);
  updateBlockfitIndicatorLines(bin);
  loadBlockfitProfile(bin);
});

blockfitBtn.addEventListener("click", async () => {
  if (!currentBackgroundJobId) {
    setBlockfitStatus(t("msg.blockfit_need_background"), true);
    return;
  }
  if (!lastPeakWidthFit) {
    setBlockfitStatus(t("msg.blockfit_need_peakwidth"), true);
    return;
  }
  if (!lastOrientationWidth) {
    setBlockfitStatus(t("msg.blockfit_need_orientation"), true);
    return;
  }

  const payload = {
    background_job_id: currentBackgroundJobId,
    a: Number(document.getElementById("a").value),
    b: Number(document.getElementById("b").value),
    c: Number(document.getElementById("c").value),
    alpha_deg: Number(document.getElementById("alpha_deg").value),
    beta_deg: Number(document.getElementById("beta_deg").value),
    gamma_deg: Number(document.getElementById("gamma_deg").value),
    phi_deg: Number(document.getElementById("phi_deg").value),
    D: Number(document.getElementById("D").value),
    lamda: Number(document.getElementById("lamda").value),
    p: Number(document.getElementById("p").value),
    hkl_max: Number(document.getElementById("blockfit_hkl_max").value),
    w0: lastPeakWidthFit.w0,
    wm: lastPeakWidthFit.wm,
    weq: lastPeakWidthFit.weq,
    c_crystal: lastPeakWidthFit.c,
    B: lastOrientationWidth.B,
    block_R: Number(document.getElementById("block_R").value),
    r_min_px: Number(document.getElementById("r_min_px").value),
    relative_error: Number(document.getElementById("blockfit_relative_error").value),
    goof: Number(document.getElementById("blockfit_goof").value),
  };

  blockfitBtn.disabled = true;
  setBlockfitStatus(t("msg.blockfit_starting"));
  blockfitResultEl.hidden = true;

  try {
    await initPyodideWorker();
    const data = await pyCallWithProgress("block_fit", payload, (done, total) => {
      const percent = total > 0 ? (done / total) * 100 : 0;
      setBlockfitStatus(tf("msg.blockfit_running_percent", { percent: percent.toFixed(0) }));
    });

    renderStats(blockfitStatsEl, data.stats, BLOCKFIT_STAT_LABELS);
    renderBlockfitMergedTable(data.merged_points);
    blockfitObservedPreviewEl.src = `data:image/png;base64,${data.observed_png_base64}`;
    blockfitCalcPreviewEl.src = `data:image/png;base64,${data.calc_png_base64}`;
    blockfitResidPreviewEl.src = `data:image/png;base64,${data.residual_png_base64}`;
    blockfitResultEl.hidden = false;
    setBlockfitStatus(tf("msg.blockfit_done", { used: data.stats.n_used, total: data.stats.n_reflections, merged: data.merged_points.length }));

    lastMergedPoints = data.merged_points;
    lastDspacingMergedPoints = null;
    document.getElementById("blockfit-dspacing-result").hidden = true;

    currentBlockfitJobId = data.job_id;
    currentBlockfitNPhi = currentBackgroundNPhi;
    blockfitProfilePhiSlider.max = currentBlockfitNPhi - 1;
    blockfitProfilePhiSlider.value = 0;
    blockfitProfilePhiMaxEl.textContent = currentBlockfitNPhi - 1;
    updateBlockfitProfilePhiLabel(0);
    loadBlockfitProfile(0);
    updateBlockfitIndicatorLines(0);
  } catch (err) {
    setBlockfitStatus(tf("msg.blockfit_failed", { error: err.message }), true);
  } finally {
    blockfitBtn.disabled = false;
  }
});

function downloadMergedCsv(points, filenameInputId, defaultFilename, headerComment) {
  const lines = [];
  lines.push(headerComment);
  const header = ["h", "k", "l", "d_hkl_nm", "dstar_1_per_nm", "intensity", "sigma_I", "snr", "multiplicity"];
  lines.push(header.join(","));

  for (const pt of sortMergedPoints(points)) {
    const row = [pt.h, pt.k, pt.l, pt.d_hkl, pt.dstar, pt.intensity, pt.sigma_I, pt.snr, pt.multiplicity];
    lines.push(row.map(csvEscape).join(","));
  }

  const csvContent = lines.join("\n");
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);

  let filename = document.getElementById(filenameInputId).value.trim() || defaultFilename;
  if (!filename.toLowerCase().endsWith(".csv")) filename += ".csv";

  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

document.getElementById("blockfit-merged-export-csv-btn").addEventListener("click", () => {
  if (!lastMergedPoints) return;
  downloadMergedCsv(lastMergedPoints, "blockfit-merged-csv-filename", "merged_intensities.csv",
    "# merged reflection intensities (symmetry-equivalent hkl + Friedel pairs averaged)");
});

// 固定幅フォーマットで h,k,l,I,sigma,m を書き出す。l は正の値にそろえ、
// l昇順→同一l内はR0昇順→h→kで並べる。
function padIntField(value, width) {
  return String(Math.trunc(value)).padStart(width, " ");
}

function padFloatField(value, width, decimals) {
  return value.toFixed(decimals).padStart(width, " ");
}

function downloadMergedHkl(points, filenameInputId, scaleInputId, defaultFilename) {
  const scale = Number(document.getElementById(scaleInputId).value) || 1;

  const rows = points.map((pt) => ({
    h: pt.h,
    k: pt.k,
    l: Math.abs(pt.l),
    I: pt.intensity / scale,
    sigma: pt.sigma_I / scale,
    // フリーデル対は正規化hklの時点で同一グループとして扱われているため、
    // multiplicity は「フリーデル対を除いた、同一(等価)反射として観測・平均された数」になる。
    m: pt.multiplicity,
    r0Order: pt.r0_px,
  }));

  rows.sort((a, b) => (a.l - b.l) || (a.r0Order - b.r0Order) || (a.h - b.h) || (a.k - b.k));

  const lines = rows.map((row) =>
    padIntField(row.h, 4) + padIntField(row.k, 4) + padIntField(row.l, 4) +
    padFloatField(row.I, 8, 2) + padFloatField(row.sigma, 8, 2) + padIntField(row.m, 4)
  );

  const content = lines.join("\n") + "\n";
  const blob = new Blob([content], { type: "text/plain;charset=utf-8;" });
  const url = URL.createObjectURL(blob);

  let filename = document.getElementById(filenameInputId).value.trim() || defaultFilename;
  if (!filename.toLowerCase().endsWith(".hkl")) filename += ".hkl";

  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

document.getElementById("blockfit-merged-export-hkl-btn").addEventListener("click", () => {
  if (!lastMergedPoints) return;
  downloadMergedHkl(lastMergedPoints, "blockfit-merged-hkl-filename", "blockfit-merged-hkl-scale", "merged_intensities.hkl");
});

blockfitMergeDspacingBtn.addEventListener("click", async () => {
  if (!currentBlockfitJobId) {
    blockfitMergeDspacingStatusEl.textContent = t("msg.dspacing_need_blockfit");
    blockfitMergeDspacingStatusEl.classList.add("error");
    return;
  }

  const payload = { job_id: currentBlockfitJobId, d_tol_nm: Number(document.getElementById("blockfit_d_tol_nm").value) };

  blockfitMergeDspacingBtn.disabled = true;
  blockfitMergeDspacingStatusEl.textContent = t("msg.dspacing_merging");
  blockfitMergeDspacingStatusEl.classList.remove("error");
  blockfitDspacingResultEl.hidden = true;

  try {
    await initPyodideWorker();
    const data = await pyCall("block_fit_merge_by_dspacing", payload);
    renderMergedTable(blockfitDspacingTableEl, data.merged_points);
    blockfitDspacingResultEl.hidden = false;
    blockfitMergeDspacingStatusEl.textContent = tf("msg.dspacing_done", { before: lastMergedPoints.length, after: data.merged_points.length });

    lastDspacingMergedPoints = data.merged_points;
  } catch (err) {
    blockfitMergeDspacingStatusEl.textContent = tf("msg.dspacing_failed", { error: err.message });
    blockfitMergeDspacingStatusEl.classList.add("error");
  } finally {
    blockfitMergeDspacingBtn.disabled = false;
  }
});

document.getElementById("blockfit-dspacing-export-csv-btn").addEventListener("click", () => {
  if (!lastDspacingMergedPoints) return;
  downloadMergedCsv(lastDspacingMergedPoints, "blockfit-dspacing-csv-filename", "merged_intensities_dspacing.csv",
    "# merged reflection intensities (symmetry-equivalent hkl + Friedel pairs + close d-spacing averaged)");
});

document.getElementById("blockfit-dspacing-export-hkl-btn").addEventListener("click", () => {
  if (!lastDspacingMergedPoints) return;
  downloadMergedHkl(lastDspacingMergedPoints, "blockfit-dspacing-hkl-filename", "blockfit-dspacing-hkl-scale", "merged_intensities_dspacing.hkl");
});

// =============================================================================
// 目次 (ステージ間ジャンプ)。表示されていない(hidden)ステージのボタンは無効化する。
// =============================================================================

const tocItems = document.querySelectorAll(".toc-item");

function updateToc() {
  tocItems.forEach((btn) => {
    const target = document.getElementById(btn.dataset.target);
    if (!target) return;
    btn.disabled = target.hidden;
  });
}

tocItems.forEach((btn) => {
  btn.addEventListener("click", () => {
    const target = document.getElementById(btn.dataset.target);
    if (target) target.scrollIntoView({ behavior: "instant", block: "start" });
  });
});

const tocObserver = new MutationObserver(updateToc);
tocItems.forEach((btn) => {
  const target = document.getElementById(btn.dataset.target);
  if (target) tocObserver.observe(target, { attributes: true, attributeFilter: ["hidden"] });
});

updateToc();

refreshFilenameSelect();

// =============================================================================
// Pyodide (ブラウザ内Python実行) 起動。
// =============================================================================

const pyodideBootOverlayEl = document.getElementById("pyodide-boot-overlay");
const pyodideBootMessageEl = document.getElementById("pyodide-boot-message");
const pyodideBootBarFillEl = document.getElementById("pyodide-boot-bar-fill");
const pyodideBootPercentEl = document.getElementById("pyodide-boot-percent");

// ロード完了までは全画面オーバーレイでUI操作をブロックする(下の要素へのクリックを
// 通さない)。percent は各ロード段階の完了時点での到達率(バイト単位の正確な
// 進捗ではなく、既知のステップに基づく段階的な値)。
function showPyodideBootStatus(message, percent, isError) {
  if (!pyodideBootOverlayEl) return;
  pyodideBootOverlayEl.hidden = false;
  pyodideBootOverlayEl.classList.toggle("error", !!isError);
  if (pyodideBootMessageEl) pyodideBootMessageEl.textContent = message;
  if (typeof percent === "number") {
    const clamped = Math.max(0, Math.min(100, percent));
    if (pyodideBootBarFillEl) pyodideBootBarFillEl.style.width = `${clamped}%`;
    if (pyodideBootPercentEl) pyodideBootPercentEl.textContent = `${Math.round(clamped)}%`;
  }
}

function hidePyodideBootOverlay() {
  if (pyodideBootOverlayEl) pyodideBootOverlayEl.hidden = true;
}

onPyodideStatus((message, percent) => showPyodideBootStatus(message, percent));

showPyodideBootStatus(t("msg.pyodide_preparing"), 0);
initPyodideWorker()
  .then((initMsg) => {
    console.log("[pyodide] ready", initMsg.selfTest);
    const errors = (initMsg.selfTest && initMsg.selfTest.errors) || [];
    if (errors.length > 0) {
      showPyodideBootStatus(tf("msg.pyodide_selftest_error", { errors: errors.join(", ") }), 100, true);
      return;
    }
    hidePyodideBootOverlay();
  })
  .catch((err) => {
    console.error("[pyodide] init failed", err);
    showPyodideBootStatus(tf("msg.pyodide_init_failed", { error: err.message }), 100, true);
  });

// 言語切り替え時、静的なラベル([data-i18n-html]等)はi18n.js側で自動更新されるが、
// JSが動的に生成した要素(ファイル未選択時のプレースホルダーなど)は個別に再描画する。
// 既に表示済みの計算結果(ステータスメッセージやテーブル)は、次回の操作で新しい言語になる。
document.addEventListener("i18n:changed", () => {
  if (typeof refreshFilenameSelect === "function") refreshFilenameSelect();
});
