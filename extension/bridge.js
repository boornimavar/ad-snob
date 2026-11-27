// Runs only on the Ad Snob website (see manifest content_scripts). Lets the
// page run searches through the helper:
//   page -> {type: "adsnob:search", id, query, country}
//   page <- {type: "adsnob:progress", id, ads, total} after every page
//   page <- {type: "adsnob:result", id, result} at the end
//   page -> {type: "adsnob:stop", id} stops the search.
document.documentElement.dataset.adsnobHelper = chrome.runtime.getManifest().version;

const ports = new Map();
const send = (message) => window.postMessage(message, window.location.origin);

window.addEventListener("message", (event) => {
  if (event.source !== window) return;
  const { type, id } = event.data || {};
  if (type === "adsnob:stop") {
    ports.get(id)?.disconnect();
    ports.delete(id);
    send({ type: "adsnob:result", id, result: { ok: true, stoppedBecause: "stopped", diag: { steps: ["stopped by you"] } } });
    return;
  }
  if (type !== "adsnob:search") return;
  let port;
  try {
    port = chrome.runtime.connect({ name: "search" });
  } catch (err) {
    send({ type: "adsnob:result", id, result: { ok: false, error: "The helper was updated. Reload this page.", diag: { steps: [] } } });
    return;
  }
  ports.set(id, port);
  port.onMessage.addListener((message) => {
    if (message.type === "progress") send({ type: "adsnob:progress", id, ads: message.ads, total: message.total });
    if (message.type === "done") {
      send({ type: "adsnob:result", id, result: message });
      port.disconnect();
      ports.delete(id);
    }
  });
  port.onDisconnect.addListener(() => {
    if (ports.has(id)) {
      ports.delete(id);
      send({ type: "adsnob:result", id, result: { ok: false, error: "The helper stopped answering. Reload this page and try again.", diag: { steps: [] } } });
    }
  });
  port.postMessage({ query: event.data.query, country: event.data.country });
});
