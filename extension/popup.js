const $ = (id) => document.getElementById(id);

$("form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("go").disabled = true;
  $("status").className = "";
  $("status").textContent = "Searching… (up to a minute)";
  $("grid").innerHTML = "";
  const started = Date.now();
  const result = await chrome.runtime.sendMessage({ type: "search", query: $("q").value.trim() || "skincare" });
  const seconds = Math.round((Date.now() - started) / 1000);
  $("go").disabled = false;
  $("diag").textContent = [...((result.diag || {}).steps || []), `took ${seconds} s`].join("\n");
  if (!result.ok) {
    $("status").className = "bad";
    $("status").textContent = `Didn't work: ${result.error}`;
    return;
  }
  const withImages = result.ads.filter((ad) => ad.images.length);
  $("status").className = result.ads.length ? "ok" : "bad";
  $("status").textContent = result.ads.length
    ? `Meta answered: ${result.ads.length} ads, ${withImages.length} with pictures.`
    : "Meta answered, but with 0 ads.";
  for (const ad of withImages.slice(0, 9)) {
    const img = document.createElement("img");
    img.src = ad.images[0];
    img.alt = ad.page || "Ad";
    img.title = ad.page;
    $("grid").append(img);
  }
});
