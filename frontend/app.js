const localApiUrl = "http://127.0.0.1:10000";
const isLocal = ["127.0.0.1", "localhost"].includes(window.location.hostname);
const API_BASE_URL = window.VIDEO_DOWNLOADER_API_URL || (isLocal ? localApiUrl : "");

const form = document.querySelector("#inspect-form");
const input = document.querySelector("#video-url");
const inspectButton = document.querySelector("#inspect-button");
const message = document.querySelector("#message");
const result = document.querySelector("#result");
const title = document.querySelector("#title");
const thumbnail = document.querySelector("#thumbnail");
const details = document.querySelector("#details");
const formats = document.querySelector("#formats");

let currentUrl = "";

function apiUrl(path) {
  if (!API_BASE_URL) {
    throw new Error("The downloader API has not been configured yet.");
  }
  return `${API_BASE_URL}${path}`;
}

function setMessage(text, isError = false) {
  message.textContent = text;
  message.classList.toggle("error", isError);
}

function formatBytes(bytes) {
  if (!bytes) return "Size unknown";
  return `${(bytes / 1024 / 1024).toFixed(bytes > 100 * 1024 * 1024 ? 0 : 1)} MB`;
}

function formatDuration(seconds) {
  if (!seconds) return "";
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${remaining}`;
}

function showFormats(video) {
  title.textContent = video.title;
  details.textContent = formatDuration(video.duration) || "Video ready";
  formats.replaceChildren();

  if (video.thumbnail) {
    thumbnail.src = video.thumbnail;
    thumbnail.hidden = false;
  } else {
    thumbnail.hidden = true;
  }

  for (const format of video.formats) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "format-button";
    const audioText = format.has_audio ? "audio included" : "audio merged";
    button.innerHTML = `<strong>${format.label}</strong><span>${format.width} × ${format.height} · ${format.orientation} · ${format.ext.toUpperCase()}</span><small>${audioText} · ${formatBytes(format.filesize)}</small>`;
    button.addEventListener("click", () => downloadFormat(format, button));
    formats.append(button);
  }
  result.hidden = false;
}

async function downloadFormat(format, button) {
  document.querySelectorAll(".format-button").forEach((item) => (item.disabled = true));
  button.classList.add("loading");
  setMessage("Preparing your download. This can take a moment.");

  try {
    const link = document.createElement("a");
    const params = new URLSearchParams({ url: currentUrl, format_id: format.id });
    link.href = apiUrl(`/api/download?${params.toString()}`);
    document.body.append(link);
    link.click();
    link.remove();
    setMessage("Your browser will start the download when it is ready.");
  } finally {
    button.classList.remove("loading");
    document.querySelectorAll(".format-button").forEach((item) => (item.disabled = false));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = input.value.trim();
  if (!url) return;

  currentUrl = url;
  inspectButton.disabled = true;
  result.hidden = true;
  setMessage("Looking for available formats...");

  try {
    const response = await fetch(apiUrl("/api/inspect"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url }),
    });
    const video = await response.json();
    if (!response.ok) throw new Error(video.detail || "Could not inspect this URL.");
    showFormats(video);
    setMessage("");
  } catch (error) {
    setMessage(error.message || "Could not inspect this URL.", true);
  } finally {
    inspectButton.disabled = false;
  }
});
