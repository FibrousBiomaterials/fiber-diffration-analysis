// メインスレッドから pyWorker.js (Pyodide) を呼び出すための薄いクライアント。
// fetch(url,{...}).then(r=>r.json()) と同じ形の非同期関数として提供することで、
// app.js 側の呼び出し箇所は route 名を変えるだけで済むようにする。

let worker = null;
let readyPromise = null;
let initResolvers = null;
let nextMessageId = 1;
const pendingCalls = new Map();
const statusListeners = [];

function onPyodideStatus(cb) {
  statusListeners.push(cb);
}

function emitStatus(message, percent) {
  for (const cb of statusListeners) {
    try {
      cb(message, percent);
    } catch (err) {
      console.error("[pyClient] status listener error", err);
    }
  }
}

function getWorker() {
  if (worker) return worker;
  // pyWorker.js はブラウザにキャッシュされやすいため、変更のたびにクエリの
  // バージョン番号を上げてキャッシュを回避する(index.html側のscriptタグと同じ運用)。
  worker = new Worker("pyWorker.js?v=13");
  worker.onmessage = onWorkerMessage;
  worker.onerror = (ev) => {
    console.error("[pyWorker] worker error", ev);
    if (initResolvers) {
      initResolvers.reject(new Error(ev.message || "Worker の起動に失敗しました"));
      initResolvers = null;
    }
  };
  return worker;
}

function onWorkerMessage(ev) {
  const msg = ev.data;
  if (!msg || !msg.type) return;

  if (msg.type === "status") {
    emitStatus(msg.message, msg.percent);
    return;
  }

  if (msg.type === "init") {
    if (initResolvers) {
      if (msg.status === "ready") {
        initResolvers.resolve(msg);
      } else {
        initResolvers.reject(new Error(msg.message || "Pyodide の初期化に失敗しました"));
      }
      initResolvers = null;
    }
    return;
  }

  const entry = pendingCalls.get(msg.id);
  if (!entry) return;

  if (msg.type === "progress") {
    if (entry.onProgress) entry.onProgress(msg.done, msg.total);
    return;
  }
  if (msg.type === "result") {
    pendingCalls.delete(msg.id);
    entry.resolve(msg.data);
    return;
  }
  if (msg.type === "error") {
    pendingCalls.delete(msg.id);
    entry.reject(new Error(msg.message || "Python側でエラーが発生しました"));
    return;
  }
}

function initPyodideWorker() {
  if (readyPromise) return readyPromise;
  const w = getWorker();
  readyPromise = new Promise((resolve, reject) => {
    initResolvers = { resolve, reject };
  });
  return readyPromise;
}

// fileBytes (Uint8Array/ArrayBuffer, 省略可) は画像バイト列などpayloadのJSONに
// 含めにくいデータを別枠で渡すためのもの。再読込のたびに毎回渡す想定のため、
// 呼び出し元のバッファを破壊しないよう Transferable化はせず構造化クローンでコピーする。
function pyCall(route, payload, fileBytes) {
  return pyCallWithProgress(route, payload, null, fileBytes);
}

function pyCallWithProgress(route, payload, onProgress, fileBytes) {
  const w = getWorker();
  const id = nextMessageId++;
  return new Promise((resolve, reject) => {
    pendingCalls.set(id, { resolve, reject, onProgress });
    w.postMessage({ type: "call", id, route, payload: payload || {}, fileBytes: fileBytes || null });
  });
}
