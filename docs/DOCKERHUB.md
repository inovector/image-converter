# Image Converter to WebP

A tiny self-hosted HTTP service that converts images to **WebP** on demand.
Send it a URL or raw image bytes, get back a one-time download link.

Built on Python's standard library + [Pillow](https://pillow.readthedocs.io/).
Ships as a small, non-root, healthcheck-enabled image.

## Pull

```bash
docker pull inovector/image-converter:latest
```

## Run

```bash
docker run -d --rm \
  -p 8000:8000 \
  -e API_SECRET_KEY=change-me \
  --name image-converter \
  inovector/image-converter:latest
```

The service listens on port `8000` inside the container. `API_SECRET_KEY` is
**required** — the container will exit immediately if it's not set.

### docker-compose

```yaml
services:
  image-converter:
    image: inovector/image-converter:latest
    container_name: image-converter
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      API_SECRET_KEY: change-me
      # PUBLIC_BASE_URL: https://convert.example.com   # if behind a proxy
      # TOKEN_TTL_SECONDS: "600"
      # MAX_BYTES: "26214400"
      # WEBP_QUALITY: "85"
```

## Usage

### Convert from raw bytes

```bash
curl -X POST http://localhost:8000/convert \
  -H "X-API-Key: change-me" \
  -H "Content-Type: image/png" \
  --data-binary @cat.png
# -> {"download_url":"http://localhost:8000/download/<token>","expires_in":600}
```

> ⚠️ Use `--data-binary`, not `-d` — `-d` strips newlines and corrupts binary
> data. Form-data (`-F`) is **not** supported; send raw bytes.

### Convert from a URL

```bash
curl -X POST http://localhost:8000/convert \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/cat.png"}'
```

### Download

The returned `download_url` works in any browser — the 256-bit token in the
URL is the credential, no header needed:

```bash
curl -o out.webp http://localhost:8000/download/<token>
```

The file is **deleted on first successful download**. Subsequent requests for
the same token return `404`. Unclaimed files are also swept away when their
TTL expires.

### Health check

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

## Endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/convert` | `X-API-Key` header | Convert by URL or raw bytes; returns one-time `download_url`. |
| `GET` | `/download/<token>` | Token in URL | Streams the WebP, then deletes it. |
| `GET` | `/health` | None | Liveness probe (also wired into Docker `HEALTHCHECK`). |

## Configuration

All configuration is via environment variables:

| Env var | Default | Purpose |
|---|---|---|
| `API_SECRET_KEY` | *(required)* | Shared secret clients send in `X-API-Key`. |
| `PORT` | `8000` | Listen port inside the container. |
| `HOST` | `0.0.0.0` | Bind address. |
| `STORAGE_DIR` | `/tmp/webp` | Where converted files are stored inside the container. |
| `TOKEN_TTL_SECONDS` | `600` | How long an unclaimed download URL stays valid. |
| `MAX_BYTES` | `26214400` (25 MiB) | Hard cap on input size (binary upload + URL fetch). |
| `WEBP_QUALITY` | `85` | Pillow quality (1–100). |
| `URL_FETCH_TIMEOUT` | `15` | Seconds for outbound URL fetch. |
| `PUBLIC_BASE_URL` | *(empty)* | If set, used as the host portion of returned download URLs (e.g. `https://convert.example.com`). Set this when running behind a reverse proxy. |

## Behind a reverse proxy

Set `PUBLIC_BASE_URL` so download URLs use your public hostname/scheme:

```bash
docker run -d --rm \
  -p 8000:8000 \
  -e API_SECRET_KEY=change-me \
  -e PUBLIC_BASE_URL=https://convert.example.com \
  --name image-converter \
  inovector/image-converter:latest
```

## Errors

| Status | When |
|---|---|
| `400` | Invalid JSON, missing `url`, undecodable image, empty body |
| `401` | Missing or wrong `X-API-Key` on `/convert` |
| `404` | Unknown / expired / already-consumed download token |
| `411` | No `Content-Length` header |
| `413` | Body or fetched URL exceeds `MAX_BYTES` |
| `502` | Upstream URL fetch failed (timeout, non-2xx, etc.) |

## Image details

- Base: `python:3.12-slim`
- Runs as a non-root `app` user
- Built-in `HEALTHCHECK` hitting `/health` every 30s
- Exposes port `8000`

## Tags

| Tag | Description |
|---|---|
| `latest` | Latest stable build |

## License

MIT

---

🖼️ Crafted by the team at [inovector.com](https://inovector.com) ⚡
