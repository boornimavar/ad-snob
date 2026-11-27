// Runs only on the Ad Snob website (see manifest content_scripts). Lets the
// page ask the helper for a search: the page posts {type: "adsnob:search"},
// this passes it to the background worker and posts the answer back.
document.documentElement.dataset.adsnobHelper = chrome.runtime.getManifest().version;

window.addEventListener("message", async (event) => {
  if (event.source !== window || event.data?.type !== "adsnob:search") return;
  const { id, query, country } = event.data;
  let result;
  try {
    result = await chrome.runtime.sendMessage({ type: "search", query, country });
    if (!result) throw new Error("The helper didn't answer. Reload it on chrome://extensions.");
  } catch (err) {
    result = { ok: false, error: String(err.message || err), diag: { steps: [] } };
  }
  window.postMessage({ type: "adsnob:result", id, result }, window.location.origin);
});
