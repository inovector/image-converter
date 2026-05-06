# Image Converter to WebP

A tiny self-hosted HTTP service that converts images to **WebP** on demand.
Send it a URL or raw image bytes, get back a one-time download link. Ships as
a single Docker image and depends on nothing but Python's standard library
plus [Pillow](https://pillow.readthedocs.io/).

## Features

- **Two input modes** — fetch from a remote URL, or POST raw image bytes directly.
- **Wide format support** — anything Pillow can decode (PNG, JPEG, GIF, BMP, TIFF, WebP, HEIC via plugin, etc.) → WebP.
- **One-time download URLs** — files are deleted after the first successful download.
- **Automatic cleanup** — unclaimed files are swept away when their TTL expires.
- **API-key auth** — `POST /convert` is gated by a shared secret in an env var.
- **Token-based downloads** — the unguessable 256-bit token in the URL is the credential, so download links can be opened in a browser.
- **No web framework** — single Python file, ~250 lines, stdlib `http.server`.
- **Production-ready container** — runs as a non-root user, has a `HEALTHCHECK`, configurable via env vars.

## Quick start

```bash
docker build -t image-converter .

docker run -d --rm \
  -p 8000:8000 \
  -e API_SECRET_KEY=change-me \
  --name image-converter \
  image-converter
```

Convert an image:

```bash
curl -X POST http://localhost:8000/convert \
  -H "X-API-Key: change-me" \
  -H "Content-Type: image/png" \
  --data-binary @cat.png
# -> {"download_url":"http://localhost:8000/download/abc...","expires_in":600}
```

Then open the `download_url` in your browser, or `curl -O` it.

## API

### `POST /convert`

Requires `X-API-Key: <API_SECRET_KEY>`.

Two ways to send the image:

**1. By URL** — `Content-Type: application/json`, body `{"url": "..."}`:

```bash
curl -X POST http://localhost:8000/convert \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://upload.wikimedia.org/wikipedia/commons/4/47/PNG_transparency_demonstration_1.png"}'
```

**2. By raw bytes** — any non-JSON `Content-Type` (e.g. `image/png`,
`application/octet-stream`); the request body **is** the image:

```bash
curl -X POST http://localhost:8000/convert \
  -H "X-API-Key: change-me" \
  -H "Content-Type: image/png" \
  --data-binary @cat.png
```

> ⚠️ Use `--data-binary`, not `-d` — `-d` strips newlines and corrupts binary
> data. Form-data (`-F`) is **not** supported; send raw bytes.

**Response** (`200 OK`):

```json
{
  "download_url": "http://localhost:8000/download/<token>",
  "expires_in": 600
}
```

**Errors**:

| Status | When |
|---|---|
| `400` | Invalid JSON, missing `url`, undecodable image, empty body |
| `401` | Missing or wrong `X-API-Key` |
| `411` | No `Content-Length` header |
| `413` | Body or fetched URL exceeds `MAX_BYTES` |
| `502` | Upstream URL fetch failed (timeout, non-2xx, etc.) |

### `GET /download/<token>`

Streams the WebP file with `Content-Type: image/webp`. **No header auth** —
the 256-bit token is the credential (same pattern as S3 signed URLs), so the
URL can be opened directly in a browser or shared.

The file is deleted on successful download. Subsequent requests for the same
token return `404`.

```bash
curl -o out.webp http://localhost:8000/download/<token>
```

### `GET /health`

Liveness probe. Returns `{"status":"ok"}`. No auth.

## Configuration

All configuration is via environment variables:

| Env var | Default | Purpose |
|---|---|---|
| `API_SECRET_KEY` | *(required, server exits if unset)* | Shared secret clients send in `X-API-Key`. |
| `PORT` | `8000` | Listen port. |
| `HOST` | `0.0.0.0` | Bind address. |
| `STORAGE_DIR` | `/tmp/webp` | Where converted files are stored. |
| `TOKEN_TTL_SECONDS` | `600` | How long an unclaimed download URL stays valid. |
| `MAX_BYTES` | `26214400` (25 MiB) | Hard cap on input size (binary upload + URL fetch). |
| `WEBP_QUALITY` | `85` | Pillow quality (1–100). |
| `URL_FETCH_TIMEOUT` | `15` | Seconds for outbound URL fetch. |
| `PUBLIC_BASE_URL` | *(empty)* | If set, used as the host portion of returned download URLs (e.g. `https://convert.example.com`). Otherwise built from the request `Host` header. Set this when running behind a reverse proxy. |

## Authentication model

| Endpoint | Auth |
|---|---|
| `POST /convert` | `X-API-Key` header must equal `API_SECRET_KEY`. Compared in constant time. |
| `GET /download/<token>` | The 256-bit `secrets.token_urlsafe(32)` token in the URL is the credential. |
| `GET /health` | None. |

## Operations

### Stop, rebuild, and restart

After editing `server.py` or the `Dockerfile`, a running container will not
pick up the changes — you have to stop it, rebuild the image, and start a
fresh container:

```bash
# 1. Stop the running container (also removes it because of --rm)
docker stop image-converter

# 2. If you didn't use --rm, also delete the stopped container
docker rm image-converter 2>/dev/null

# 3. Rebuild the image (add --no-cache if you suspect stale layers)
docker build -t image-converter .

# 4. Run again
docker run -d --rm \
  -p 8000:8000 \
  -e API_SECRET_KEY=change-me \
  --name image-converter \
  image-converter
```

One-liner for the common case:

```bash
docker stop image-converter 2>/dev/null; \
docker build -t image-converter . && \
docker run -d --rm -p 8000:8000 -e API_SECRET_KEY=change-me \
  --name image-converter image-converter
```

### Diagnostics

```bash
docker ps                          # is it running?
docker logs image-converter        # what did it print?
docker logs -f image-converter     # tail logs live
docker exec image-converter sh     # shell inside the container
docker rmi image-converter         # remove the image entirely
```

### Behind a reverse proxy

Set `PUBLIC_BASE_URL` so download URLs use the public hostname/scheme rather
than whatever the container sees in the `Host` header:

```bash
docker run -d --rm \
  -p 8000:8000 \
  -e API_SECRET_KEY=change-me \
  -e PUBLIC_BASE_URL=https://convert.example.com \
  --name image-converter \
  image-converter
```

## Run locally (no Docker)

For development or quick experiments:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
API_SECRET_KEY=change-me python server.py
```

The server listens on `0.0.0.0:8000` by default — override with `PORT` and
`HOST` env vars.

## Credits

🖼️ Crafted by the team at [inovector.com](https://inovector.com) ⚡

## License

Released under the [MIT License](LICENSE).