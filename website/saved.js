// Saved ads, kept in this browser (IndexedDB): the ad's details, its scores
// and a copy of the picture, because Meta's picture links stop working within
// days. Nothing leaves the visitor's computer. Clearing the browser's site
// data removes them.

const DB = "creative-lens";
const STORE = "saved";
let dbPromise = null;

function open() {
  if (!dbPromise) {
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB, 1);
      req.onupgradeneeded = () => req.result.createObjectStore(STORE, { keyPath: "key" });
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }
  return dbPromise;
}

async function run(mode, work) {
  const db = await open();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    const req = work(tx.objectStore(STORE));
    tx.oncomplete = () => resolve(req?.result);
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new Error("Saving was cancelled (the browser may be out of space)."));
  });
}

/** Every saved ad, newest first. */
export async function allSaved() {
  const rows = (await run("readonly", (s) => s.getAll())) || [];
  return rows.sort((a, b) => b.savedAt - a.savedAt);
}

/** entry: {key, ad, index, record, row, query, picture: Blob} */
export function saveAd(entry) {
  return run("readwrite", (s) => s.put({ ...entry, savedAt: Date.now() }));
}

export function unsaveAd(key) {
  return run("readwrite", (s) => s.delete(key));
}
