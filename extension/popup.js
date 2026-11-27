const $ = (id) => document.getElementById(id);

function show(kind, text) {
  $("status").className = kind;
  $("status").textContent = text;
}

if (!globalThis.chrome?.runtime?.id) {
  $("go").disabled = true;
  show("bad", "This page only works inside Chrome as the add-on's pop-up (click the add-on's icon in the toolbar).");
}

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("go").disabled = true;
  $("grid").innerHTML = "";
  show("", "Searching… (up to a minute)");
  const started = Date.now();
  let result;
  try {
    result = await chrome.runtime.sendMessage({ type: "search", query: $("q").value.trim() || "skincare" });
    if (!result) throw new Error("The add-on didn't answer. Reload it on chrome://extensions and try again.");
  } catch (err) {
    result = { ok: false, error: String(err.message || err), diag: { steps: [] } };
  } finally {
    $("go").disabled = false;
  }
  const seconds = Math.round((Date.now() - started) / 1000);
  $("diag").textContent = [...((result.diag || {}).steps || []), `took ${seconds} s`].join("\n");
  if (!result.ok) {
    show("bad", `Didn't work: ${result.error}`);
    return;
  }
  const withImages = result.ads.filter((ad) => ad.images.length);
  show(result.ads.length ? "ok" : "bad", result.ads.length
    ? `Meta answered: ${result.ads.length} ads, ${withImages.length} with pictures.`
    : "Meta answered, but with 0 ads.");
  for (const ad of withImages.slice(0, 9)) {
    const img = document.createElement("img");
    img.src = ad.images[0];
    img.alt = ad.page || "Ad";
    img.title = ad.page;
    $("grid").append(img);
  }
});
