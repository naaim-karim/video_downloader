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
const progressSection = document.querySelector("#download-progress");
const progressFill = document.querySelector("#progress-fill");
const progressLabel = document.querySelector("#progress-label");

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
  if (!bytes || bytes < 0) return "Size not provided";
  return `${(bytes / 1024 / 1024).toFixed(bytes > 100 * 1024 * 1024 ? 0 : 1)} MB`;
}

function isYouTubeUrl(value) {
  try {
    const hostname = new URL(value).hostname.toLowerCase().replace(/\.$/, "");
    return hostname === "youtu.be" || hostname === "youtube.com" || hostname.endsWith(".youtube.com");
  } catch {
    return false;
  }
}

function formatDuration(seconds) {
  if (!seconds) return "";
  const minutes = Math.floor(seconds / 60);
  const remaining = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `${minutes}:${remaining}`;
}

function formatSpeed(bytesPerSecond) {
  if (!bytesPerSecond) return "";
  return `${formatBytes(bytesPerSecond)}/s`;
}

function setProgress(job) {
  progressSection.hidden = false;
  progressFill.style.width = `${job.progress || 0}%`;
  if (job.state === "preparing") {
    progressLabel.textContent = "Preparing download...";
  } else if (job.state === "processing") {
    progressLabel.textContent = "Merging video and audio...";
  } else if (job.state === "downloading") {
    const transferred = formatBytes(job.downloaded_bytes);
    const total = job.total_bytes ? ` of ${formatBytes(job.total_bytes)}` : "";
    const speed = formatSpeed(job.speed);
    const eta = job.eta ? ` · about ${job.eta}s left` : "";
    progressLabel.textContent = `${Math.round(job.progress)}% · ${transferred}${total}${speed ? ` · ${speed}` : ""}${eta}`;
  } else if (job.state === "ready") {
    progressLabel.textContent = "Ready. Starting browser download...";
  }
}

function wait(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
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
    const sizeText = format.filesize_estimated ? `about ${formatBytes(format.filesize)}` : formatBytes(format.filesize);
    button.innerHTML = `<strong>${format.label}</strong><span>${format.width} × ${format.height} · ${format.orientation} · ${format.ext.toUpperCase()}</span><small>${audioText} · ${sizeText}</small>`;
    button.addEventListener("click", () => downloadFormat(format, button));
    formats.append(button);
  }
  result.hidden = false;
}

async function downloadFormat(format, button) {
  document.querySelectorAll(".format-button").forEach((item) => (item.disabled = true));
  button.classList.add("loading");
  progressSection.hidden = true;
  setMessage("");

  try {
    const response = await fetch(apiUrl("/api/jobs"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: currentUrl, format_id: format.id }),
    });
    const createdJob = await response.json();
    if (!response.ok) throw new Error(createdJob.detail || "Could not start the download.");

    let job = createdJob;
    while (job.state !== "ready" && job.state !== "failed") {
      setProgress(job);
      await wait(900);
      const jobResponse = await fetch(apiUrl(`/api/jobs/${job.id}`));
      job = await jobResponse.json();
      if (!jobResponse.ok) throw new Error(job.detail || "The download expired.");
    }
    if (job.state === "failed") throw new Error(job.error || "Download failed. Try another format.");

    setProgress(job);
    const link = document.createElement("a");
    link.href = apiUrl(`/api/jobs/${job.id}/file`);
    document.body.append(link);
    link.click();
    link.remove();
    setMessage("Your browser download has started.");
  } finally {
    button.classList.remove("loading");
    document.querySelectorAll(".format-button").forEach((item) => (item.disabled = false));
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const url = input.value.trim();
  if (!url) return;

  if (isYouTubeUrl(url)) {
    result.hidden = true;
    progressSection.hidden = true;
    setMessage("YouTube downloads are unavailable because YouTube's Terms do not allow this type of download.", true);
    return;
  }

  currentUrl = url;
  inspectButton.disabled = true;
  result.hidden = true;
  progressSection.hidden = true;
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
