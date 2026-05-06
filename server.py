"""Image-to-WebP HTTP service.

Stdlib-only HTTP server (plus Pillow) that accepts an image by URL or as raw
binary in the request body, converts it to WebP, and returns a one-time
download URL. Files are deleted after the first successful download or when
their TTL expires.

All endpoints except /health require the X-API-Key header to match the
API_SECRET_KEY environment variable.
"""

import hmac
import io
import json
import logging
import os
import secrets
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, UnidentifiedImageError


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        logging.warning("Invalid integer for %s=%r, using default %d", name, raw, default)
        return default


API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "").strip()
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = _env_int("PORT", 8000)
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/tmp/webp")
TOKEN_TTL_SECONDS = _env_int("TOKEN_TTL_SECONDS", 600)
MAX_BYTES = _env_int("MAX_BYTES", 25 * 1024 * 1024)
WEBP_QUALITY = _env_int("WEBP_QUALITY", 85)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
URL_FETCH_TIMEOUT = _env_int("URL_FETCH_TIMEOUT", 15)
SWEEP_INTERVAL_SECONDS = 60

_registry_lock = threading.Lock()
_registry: dict[str, tuple[str, float]] = {}


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _check_auth(handler: BaseHTTPRequestHandler) -> bool:
    provided = handler.headers.get("X-API-Key", "")
    if not provided or not hmac.compare_digest(provided, API_SECRET_KEY):
        _send_json(handler, 401, {"error": "unauthorized"})
        return False
    return True


def _read_body(handler: BaseHTTPRequestHandler) -> bytes | None:
    length_header = handler.headers.get("Content-Length")
    if length_header is None:
        _send_json(handler, 411, {"error": "Content-Length required"})
        return None
    try:
        length = int(length_header)
    except ValueError:
        _send_json(handler, 400, {"error": "invalid Content-Length"})
        return None
    if length < 0:
        _send_json(handler, 400, {"error": "invalid Content-Length"})
        return None
    if length > MAX_BYTES:
        _send_json(handler, 413, {"error": f"payload too large (max {MAX_BYTES} bytes)"})
        return None
    if length == 0:
        return b""
    return handler.rfile.read(length)


def _fetch_url(url: str) -> tuple[bytes | None, tuple[int, str] | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None, (400, "url must use http or https")
    req = urllib.request.Request(url, headers={"User-Agent": "image-convertor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=URL_FETCH_TIMEOUT) as resp:
            if resp.status < 200 or resp.status >= 300:
                return None, (502, f"upstream returned status {resp.status}")
            buf = bytearray()
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    return None, (413, f"upstream body exceeds {MAX_BYTES} bytes")
            return bytes(buf), None
    except urllib.error.HTTPError as e:
        return None, (502, f"upstream HTTP error: {e.code}")
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        return None, (502, f"upstream fetch failed: {e}")


def _convert_to_webp(data: bytes) -> bytes | None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            if img.mode in ("P", "CMYK"):
                img = img.convert("RGBA" if "transparency" in img.info else "RGB")
            elif img.mode not in ("RGB", "RGBA", "L", "LA"):
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)
            return out.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return None


def _store_webp(data: bytes) -> str:
    token = secrets.token_urlsafe(32)
    path = os.path.join(STORAGE_DIR, f"{token}.webp")
    tmp_path = path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(data)
    os.replace(tmp_path, path)
    expires_at = time.time() + TOKEN_TTL_SECONDS
    with _registry_lock:
        _registry[token] = (path, expires_at)
    return token


def _take_token(token: str) -> str | None:
    """Remove and return the file path for `token` if valid and unexpired."""
    now = time.time()
    with _registry_lock:
        entry = _registry.pop(token, None)
    if entry is None:
        return None
    path, expires_at = entry
    if expires_at < now:
        _try_unlink(path)
        return None
    return path


def _try_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        logging.warning("Failed to unlink %s: %s", path, e)


def _sweep_expired() -> None:
    while True:
        time.sleep(SWEEP_INTERVAL_SECONDS)
        now = time.time()
        expired: list[tuple[str, str]] = []
        with _registry_lock:
            for token, (path, expires_at) in list(_registry.items()):
                if expires_at < now:
                    expired.append((token, path))
                    del _registry[token]
        for _, path in expired:
            _try_unlink(path)


def _build_download_url(handler: BaseHTTPRequestHandler, token: str) -> str:
    if PUBLIC_BASE_URL:
        base = PUBLIC_BASE_URL
    else:
        host = handler.headers.get("Host") or f"{HOST}:{PORT}"
        base = f"http://{host}"
    return f"{base}/download/{token}"


class Handler(BaseHTTPRequestHandler):
    server_version = "ImageConvertor/1.0"

    def log_message(self, format: str, *args) -> None:
        logging.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/health":
                _send_json(self, 200, {"status": "ok"})
                return
            if path.startswith("/download/"):
                token = path[len("/download/"):]
                if not token or "/" in token:
                    _send_json(self, 404, {"error": "not found"})
                    return
                self._handle_download(token)
                return
            _send_json(self, 404, {"error": "not found"})
        except Exception:
            logging.error("Unhandled error in GET %s\n%s", self.path, traceback.format_exc())
            try:
                _send_json(self, 500, {"error": "internal server error"})
            except Exception:
                pass

    def do_POST(self) -> None:
        try:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/convert":
                if not _check_auth(self):
                    return
                self._handle_convert()
                return
            _send_json(self, 404, {"error": "not found"})
        except Exception:
            logging.error("Unhandled error in POST %s\n%s", self.path, traceback.format_exc())
            try:
                _send_json(self, 500, {"error": "internal server error"})
            except Exception:
                pass

    def _handle_convert(self) -> None:
        content_type = (self.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
        body = _read_body(self)
        if body is None:
            return

        if content_type == "application/json":
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                _send_json(self, 400, {"error": "invalid JSON body"})
                return
            if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
                _send_json(self, 400, {"error": "JSON body must include a 'url' string"})
                return
            image_bytes, err = _fetch_url(payload["url"].strip())
            if err is not None:
                _send_json(self, err[0], {"error": err[1]})
                return
            assert image_bytes is not None
        else:
            if not body:
                _send_json(self, 400, {"error": "empty body"})
                return
            image_bytes = body

        webp = _convert_to_webp(image_bytes)
        if webp is None:
            _send_json(self, 400, {"error": "could not decode input as an image"})
            return

        token = _store_webp(webp)
        _send_json(
            self,
            200,
            {
                "download_url": _build_download_url(self, token),
                "expires_in": TOKEN_TTL_SECONDS,
            },
        )

    def _handle_download(self, token: str) -> None:
        path = _take_token(token)
        if path is None:
            _send_json(self, 404, {"error": "not found"})
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            _send_json(self, 404, {"error": "not found"})
            return

        try:
            with open(path, "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", "image/webp")
                self.send_header("Content-Length", str(size))
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{token}.webp"',
                )
                self.end_headers()
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        finally:
            _try_unlink(path)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not API_SECRET_KEY:
        logging.error("API_SECRET_KEY environment variable is required")
        sys.exit(1)
    os.makedirs(STORAGE_DIR, exist_ok=True)

    threading.Thread(target=_sweep_expired, daemon=True).start()

    server = ThreadingHTTPServer((HOST, PORT), Handler)
    logging.info("Listening on %s:%d (storage=%s, ttl=%ds)", HOST, PORT, STORAGE_DIR, TOKEN_TTL_SECONDS)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
