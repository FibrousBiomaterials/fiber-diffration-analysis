// Pyodide (ブラウザ内Python) を常駐させる Web Worker。
// メインスレッドをブロックしないよう、numpy/scipy を使う重い解析処理はすべてここで実行する。

const PYODIDE_VERSION = "0.26.4";
const PYODIDE_INDEX_URL = `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

// py/*.py を編集するたびにここを上げ、ブラウザキャッシュされた古いコードが
// Workerに読み込まれ続けるのを防ぐ(pyWorker.js自体のキャッシュ問題と同じ対策)。
const PY_SOURCE_VERSION = 3;

const PY_MODULE_FILES = [
  "detector.py",
  "tilt.py",
  "correction.py",
  "background.py",
  "reflections.py",
  "refine.py",
  "peakwidth.py",
  "blockfit.py",
  "merge.py",
  "api.py",
];

importScripts(`${PYODIDE_INDEX_URL}pyodide.js`);

function postStatus(message, percent) {
  self.postMessage({ type: "status", message, percent });
}

// loadPyodide()/loadPackage() はバイト単位の進捗を返してくれないため、
// 大きなダウンロードの最中に画面がまったく動かず「止まっている」ように見えてしまう。
// 対象のPromiseが解決するまでの間、目標値に指数的に近づく(が超えない)値を
// 一定間隔で送り続けることで、実際の所要時間が読めなくても常に動いて見えるようにする。
async function withTickingProgress(message, fromPercent, toPercent, promiseFactory) {
  postStatus(message, fromPercent);
  let current = fromPercent;
  const timer = setInterval(() => {
    current = current + (toPercent - current) * 0.12;
    if (toPercent - current < 0.3) current = toPercent - 0.3;
    postStatus(message, current);
  }, 400);
  try {
    return await promiseFactory();
  } finally {
    clearInterval(timer);
  }
}

// 各段階の完了時点での到達率(%)。バイト単位の正確な進捗はpyodide側から
// 取得できないため、実際に完了した既知のステップに基づく段階的な値とする。
async function loadPyodideAndPackages() {
  const pyodide = await withTickingProgress(
    "Pyodide core を読み込み中...(数十秒かかる場合があります)", 2, 15,
    () => loadPyodide({ indexURL: PYODIDE_INDEX_URL })
  );
  postStatus("Pyodide core 読み込み完了", 15);

  await withTickingProgress(
    "numpy を読み込み中...(初回のみ時間がかかります)", 15, 40,
    () => pyodide.loadPackage(["numpy"])
  );
  postStatus("numpy 読み込み完了", 40);

  await withTickingProgress(
    "scipy を読み込み中...(数十秒かかる場合があります)", 40, 75,
    () => pyodide.loadPackage(["scipy"])
  );
  postStatus("scipy 読み込み完了", 75);

  await withTickingProgress(
    "Pillow を読み込み中...", 75, 82,
    () => pyodide.loadPackage(["Pillow"])
  );
  postStatus("Pillow 読み込み完了", 82);

  const fileCount = PY_MODULE_FILES.length;
  for (let i = 0; i < fileCount; i++) {
    const name = PY_MODULE_FILES[i];
    postStatus(`解析モジュールを配置中... (${name})`, 82 + Math.round((i / fileCount) * 13));
    const resp = await fetch(`py/${name}?v=${PY_SOURCE_VERSION}`);
    if (!resp.ok) {
      throw new Error(`${name} の取得に失敗しました (HTTP ${resp.status})`);
    }
    const text = await resp.text();
    pyodide.FS.writeFile(`/home/pyodide/${name}`, text);
  }
  postStatus("解析モジュールの配置完了", 95);

  await pyodide.runPythonAsync(`
import sys
if "/home/pyodide" not in sys.path:
    sys.path.insert(0, "/home/pyodide")
`);

  postStatus("自己診断中(scipy関数の存在確認)...", 97);
  const selfTestJson = await pyodide.runPythonAsync(`
import json, importlib

report = {"modules": [], "scipy_check": {}, "errors": []}

_modules = [
    "detector", "tilt", "correction", "background",
    "reflections", "refine", "peakwidth", "blockfit", "merge", "api",
]
for _mod_name in _modules:
    try:
        importlib.import_module(_mod_name)
        report["modules"].append(_mod_name)
    except Exception as exc:
        report["errors"].append(f"{_mod_name}: {exc!r}")

_scipy_symbols = [
    ("scipy.interpolate", "LSQUnivariateSpline"),
    ("scipy.stats", "binned_statistic_2d"),
    ("scipy.stats", "chi2"),
    ("scipy.optimize", "curve_fit"),
    ("scipy.optimize", "nnls"),
    ("scipy.ndimage", "affine_transform"),
    ("scipy.ndimage", "gaussian_filter"),
    ("scipy.ndimage", "minimum_filter1d"),
    ("scipy.ndimage", "uniform_filter1d"),
]
for _mod_path, _name in _scipy_symbols:
    _key = f"{_mod_path}.{_name}"
    try:
        _mod = importlib.import_module(_mod_path)
        getattr(_mod, _name)
        report["scipy_check"][_key] = True
    except Exception as exc:
        report["scipy_check"][_key] = f"NG: {exc!r}"

json.dumps(report)
`);

  postStatus("準備完了", 100);
  return { pyodide, selfTest: JSON.parse(selfTestJson) };
}

let pyodideInstance = null;

async function handleCall(msg) {
  const { id, route, payload, fileBytes } = msg;
  try {
    if (!pyodideInstance) {
      throw new Error("Pyodide がまだ初期化されていません");
    }
    // JS関数をPython呼び出しの引数として渡すと、Pyodideが自動的にcallableな
    // JsProxyへラップしてくれるため、明示的な create_proxy は不要
    // (この関数はPython側の呼び出し完了と同時に参照が切れるだけの単発コールバックのため)。
    const progressCallback = (done, total) => {
      self.postMessage({ type: "progress", id, done, total });
    };

    const apiModule = pyodideInstance.pyimport("api");
    const payloadPy = pyodideInstance.toPy(payload || {});
    const fileBytesPy = fileBytes ? pyodideInstance.toPy(new Uint8Array(fileBytes)) : null;
    let resultPy;
    let result;
    try {
      resultPy = apiModule.dispatch(route, payloadPy, progressCallback, fileBytesPy);
      if (resultPy && typeof resultPy.toJs === "function") {
        result = resultPy.toJs({ dict_converter: Object.fromEntries });
      } else {
        result = resultPy;
      }
    } finally {
      if (payloadPy && typeof payloadPy.destroy === "function") payloadPy.destroy();
      if (fileBytesPy && typeof fileBytesPy.destroy === "function") fileBytesPy.destroy();
      if (resultPy && typeof resultPy.destroy === "function") resultPy.destroy();
    }

    self.postMessage({ type: "result", id, data: result });
  } catch (err) {
    const rawMessage = err && err.message ? err.message : String(err);
    self.postMessage({ type: "error", id, message: extractPythonErrorMessage(rawMessage) });
  }
}

// Python例外はフルスタックトレースを含む文字列としてFFI境界を越えてくるため、
// UIのステータス表示にはユーザーに意味のある最終行(例外クラス名 + メッセージ)だけを使う。
function extractPythonErrorMessage(raw) {
  const lines = raw.trim().split("\n");
  return lines[lines.length - 1] || raw;
}

self.onmessage = (ev) => {
  const msg = ev.data;
  if (msg && msg.type === "call") {
    handleCall(msg);
  }
};

(async () => {
  try {
    const { pyodide, selfTest } = await loadPyodideAndPackages();
    pyodideInstance = pyodide;
    self.postMessage({ type: "init", status: "ready", selfTest });
  } catch (err) {
    self.postMessage({ type: "init", status: "error", message: err && err.message ? err.message : String(err) });
  }
})();
