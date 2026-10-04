# Video Downloader

A small website that inspects a public video URL, shows the available video sizes, and downloads the selected format. The API uses `yt-dlp` and FFmpeg; files are saved only in the server's temporary directory and removed after they are sent to the browser.

## Run locally

1. Start the API with Docker:

   ```sh
   cd backend
   docker build -t video-downloader-api .
   docker run --rm -p 10000:10000 -e FRONTEND_ORIGINS=http://localhost:8788,http://127.0.0.1:8788 video-downloader-api
   ```

   Or run it directly if Python and FFmpeg are installed:

   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -r backend/requirements.txt
   cd backend
   FRONTEND_ORIGINS=http://localhost:8788,http://127.0.0.1:8788 ../.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 10000
   ```

2. In a second terminal, serve `frontend/` on port 8788:

   ```sh
   cd frontend
   python3 -m http.server 8788 --bind 127.0.0.1
   ```

   Open `http://127.0.0.1:8788`. The frontend automatically uses the local API.

## Deploy

### Backend: Render

1. Push this repository to GitHub.
2. In Render, create a new Blueprint from the repository. It reads `render.yaml` and builds `backend/Dockerfile`.
3. Set `FRONTEND_ORIGINS` to the exact Cloudflare Pages URL, such as `https://your-site.pages.dev`.
4. Copy the deployed `https://...onrender.com` API URL.

### Frontend: Cloudflare Pages

1. Create a Pages project from the same repository.
2. Set the build output directory to `frontend`; no build command is required.
3. In `frontend/config.js`, set `VIDEO_DOWNLOADER_API_URL` to your Render API URL and redeploy.

The free Render service may take about a minute to wake after 15 minutes without traffic. It also has a 500 MB per-download limit in the API to stay within the small free instance.

## Notes

- A given site may block automated access, require login, or have no downloadable formats.
- High quality formats commonly need FFmpeg to combine separate video and audio streams.
- YouTube URLs are not supported because YouTube's Terms restrict this type of download.
- Use this only for videos you own or are permitted to download.
