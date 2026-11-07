"""Headless Chrome driven over the DevTools protocol, standard library only.

Used by the layout tests. Importing this module does not need Chrome; only launching a Browser does.
"""

import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
import urllib.parse
import urllib.request

from support import name_mini, own, seed_catalog

from batchall import db, server

STARTUP_SECONDS = 10
RENDER_SECONDS = 10
CALL_TIMEOUT = 30

DEFAULT_READY_JS = """
(() => {
  const status = document.querySelector('#catalog-status');
  if (!status || status.textContent.trim() === '') return false;
  if (document.querySelector('#roster tbody tr, .mini-card, #card-grid .empty')) return true;
  const empty = document.querySelector('#roster-empty');
  return !!empty && !empty.hidden;
})()
"""

# A function expression (el) => boolean: true when the element is really on screen to the eye.
# Unlike offsetParent, this is true for position:fixed elements.
IS_VISIBLE_JS = """
(el) => {
  if (!el || el.getClientRects().length === 0) return false;
  const style = getComputedStyle(el);
  if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return false;
  const box = el.getBoundingClientRect();
  return box.width > 0 && box.height > 0;
}
"""


def chrome_path():
    """The first Chrome or Chromium binary found on PATH, or None."""
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class WebSocket:
    """A minimal client: text frames only, enough for the DevTools protocol."""

    def __init__(self, url):
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=CALL_TIMEOUT)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            (
                f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
            ).encode()
        )
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(1)
            if not chunk:
                raise ConnectionError("Chrome closed the DevTools connection during the handshake")
            buf += chunk
        self.next_id = 0
        self.events = []

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def _read(self, n):
        out = b""
        while len(out) < n:
            chunk = self.sock.recv(n - len(out))
            if not chunk:
                raise ConnectionError("Chrome closed the DevTools connection")
            out += chunk
        return out

    def _frame(self):
        data = b""
        while True:
            b1, b2 = self._read(2)
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack(">H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self._read(8))[0]
            data += self._read(length)
            if b1 & 0x80:
                return data

    def _write(self, payload):
        mask = os.urandom(4)
        n = len(payload)
        head = bytes([0x81])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            head += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def send(self, method, **params):
        """Call a protocol method and return its result. Events that arrive meanwhile are kept in self.events."""
        self.next_id += 1
        self._write(json.dumps({"id": self.next_id, "method": method, "params": params}).encode())
        while True:
            msg = json.loads(self._frame())
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise RuntimeError(f"{method} failed: {msg['error']}")
                return msg.get("result", {})
            if "method" in msg:
                self.events.append(msg)

    def wait_event(self, method, seconds):
        """Return the first event named method, reading more from the socket until the time runs out."""
        deadline = time.monotonic() + seconds
        while True:
            for i, event in enumerate(self.events):
                if event["method"] == method:
                    return self.events.pop(i)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                msg = json.loads(self._frame())
            except socket.timeout:
                return None
            finally:
                self.sock.settimeout(CALL_TIMEOUT)
            if "method" in msg:
                self.events.append(msg)


class Browser:
    """Headless Chrome with device emulation. Use as a context manager."""

    def __init__(self, width, height, mobile=True, scale=2):
        self.width = width
        self.height = height
        self.mobile = mobile
        self.scale = scale
        self.chrome = None
        self.ws = None
        self.profile = None

    def __enter__(self):
        path = chrome_path()
        if not path:
            raise RuntimeError("Chrome is not installed")
        self.profile = tempfile.TemporaryDirectory()
        port = _free_port()
        try:
            self.chrome = subprocess.Popen(
                [
                    path, "--headless=new", "--no-sandbox", "--disable-gpu",
                    f"--remote-debugging-port={port}", f"--user-data-dir={self.profile.name}", "about:blank",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            tabs = self._wait_for_tabs(port)
            self.ws = WebSocket(tabs[0]["webSocketDebuggerUrl"])
            for domain in ("Page", "Runtime", "Log"):
                self.ws.send(f"{domain}.enable")
            self._emulate()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.ws:
            self.ws.close()
            self.ws = None
        if self.chrome:
            self.chrome.terminate()
            try:
                self.chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.chrome.kill()
                self.chrome.wait()
            self.chrome = None
        if self.profile:
            try:
                self.profile.cleanup()
            except OSError:
                pass  # Chrome can still be flushing files into the profile
            self.profile = None
        return False

    def _wait_for_tabs(self, port):
        deadline = time.monotonic() + STARTUP_SECONDS
        while time.monotonic() < deadline:
            if self.chrome.poll() is not None:
                raise RuntimeError(f"Chrome exited with status {self.chrome.returncode} before it was ready")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1) as resp:
                    tabs = [t for t in json.load(resp) if t["type"] == "page"]
                if tabs:
                    return tabs
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        raise RuntimeError(f"Chrome's DevTools endpoint did not come up within {STARTUP_SECONDS} seconds")

    def _emulate(self):
        self.ws.send(
            "Emulation.setDeviceMetricsOverride",
            width=self.width, height=self.height, deviceScaleFactor=self.scale, mobile=self.mobile,
        )
        self.ws.send("Emulation.setTouchEmulationEnabled", enabled=self.mobile)

    def open(self, url, ready=None):
        """Navigate to url and wait until the page has loaded and the app has rendered.

        By default the app counts as rendered when #catalog-status has text and the collection has been
        drawn: table rows, cards, or the empty-collection message (in the table or the cards view). Pass ready (a JavaScript expression)
        to wait for something else, such as the sign-in page.
        """
        # Console output from the previous page does not count against this one.
        quiet = ("Runtime.consoleAPICalled", "Log.entryAdded", "Page.loadEventFired")
        self.ws.events = [e for e in self.ws.events if e["method"] not in quiet]
        self.ws.send("Page.navigate", url=url)
        if self.ws.wait_event("Page.loadEventFired", RENDER_SECONDS) is None:
            raise RuntimeError(f"{url} did not finish loading within {RENDER_SECONDS} seconds")
        condition = ready or DEFAULT_READY_JS
        deadline = time.monotonic() + RENDER_SECONDS
        while time.monotonic() < deadline:
            if self.js(condition):
                return
            time.sleep(0.1)
        raise RuntimeError(f"{url} loaded but the app did not render within {RENDER_SECONDS} seconds")

    def js(self, expression):
        result = self.ws.send(
            "Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = details.get("exception", {}).get("description") or details.get("text")
            raise RuntimeError(f"Script error in {expression!r}: {text}")
        return result.get("result", {}).get("value")

    def click(self, selector):
        found = self.js(
            f"(() => {{ const e = document.querySelector({json.dumps(selector)}); "
            "if (!e) return false; e.click(); return true; })()"
        )
        if not found:
            raise RuntimeError(f"No element matches {selector!r}")

    def type_into(self, selector, text):
        found = self.js(
            f"(() => {{ const e = document.querySelector({json.dumps(selector)}); if (!e) return false; "
            f"e.focus(); e.value = {json.dumps(text)}; "
            "e.dispatchEvent(new Event('input', {bubbles: true})); "
            "e.dispatchEvent(new Event('change', {bubbles: true})); return true; })()"
        )
        if not found:
            raise RuntimeError(f"No element matches {selector!r}")

    def resize(self, width, height):
        self.width = width
        self.height = height
        self._emulate()

    def screenshot(self, path, full=False):
        params = {"format": "png"}
        if full:
            size = self.js("[document.documentElement.scrollWidth, document.documentElement.scrollHeight]")
            params.update(
                captureBeyondViewport=True,
                clip={"x": 0, "y": 0, "width": size[0], "height": min(size[1], 8000), "scale": 1},
            )
        data = self.ws.send("Page.captureScreenshot", **params)["data"]
        with open(path, "wb") as f:
            f.write(base64.b64decode(data))

    def console_errors(self):
        """Error messages logged to the console or the browser log since the page was opened."""
        # Pick up anything Chrome has sent but the client has not read yet.
        self.js("0")
        found = []
        for event in self.ws.events:
            params = event["params"]
            if event["method"] == "Runtime.consoleAPICalled" and params.get("type") == "error":
                found.append(" ".join(str(a.get("value", a.get("description", ""))) for a in params["args"]))
            elif event["method"] == "Log.entryAdded" and params["entry"].get("level") == "error":
                found.append(params["entry"].get("text", ""))
        return found


def _seed_fixture(path):
    conn = db.connect(path)
    try:
        seed_catalog(conn)
        # (unit, quantity, painted): 14 owned minis across 6 units.
        for uuid, qty, painted in (
            ("u-atlas", 3, 1), ("u-locust", 2, 0), ("u-wolfhound", 2, 2),
            ("u-timber", 3, 0), ("u-condor", 2, 1), ("u-elemental", 2, 0),
        ):
            own(conn, uuid, qty=qty, painted=painted)
        entries = {row["unit_uuid"]: row["id"] for row in conn.execute("SELECT id, unit_uuid FROM collection")}
        name_mini(conn, entries["u-atlas"], note="Command lance leader", faction="Federated Suns",
                  company="Alpha Company", material="Resin")
        name_mini(conn, entries["u-timber"], note="Needs a new base", faction="Clan Wolf",
                  company="Delta Galaxy", material="Plastic")
        name_mini(conn, entries["u-locust"], faction="Federated Suns", company="Alpha Company", material="Metal")
        name_mini(conn, entries["u-condor"], note="Bought at the convention", material="Metal")
    finally:
        conn.close()


def fixture_server():
    """Serve a seeded temporary database on a free port. Returns (base_url, close)."""
    tmp = tempfile.TemporaryDirectory()
    art = tempfile.TemporaryDirectory()
    try:
        _seed_fixture(os.path.join(tmp.name, "fixture.db"))
        httpd = server.make_server(port=0, db_path=os.path.join(tmp.name, "fixture.db"), art_dir=art.name)
    except BaseException:
        tmp.cleanup()
        art.cleanup()
        raise
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()

    def close():
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)
        tmp.cleanup()
        art.cleanup()

    return f"http://127.0.0.1:{httpd.server_address[1]}", close


def overflow(browser):
    """Pixels the page is wider than the window; 0 means no sideways scroll."""
    return browser.js("document.documentElement.scrollWidth - document.documentElement.clientWidth")


_SMALL_TARGETS_JS = """
(minimum) => {
  const out = [];
  const isVisible = __IS_VISIBLE__;
  const nodes = document.querySelectorAll('button, a[href], input, select, textarea, [role=button]');
  for (const el of nodes) {
    if (!isVisible(el)) continue;
    if (el.closest('.table-wrap')) continue;
    if (el.tagName === 'INPUT' && el.type === 'hidden') continue;
    let r = el.getBoundingClientRect();
    if (r.height >= minimum && r.width >= minimum) continue;
    if (el.tagName === 'INPUT' && (el.type === 'checkbox' || el.type === 'radio')) {
      const label = el.closest('label');
      if (label && label.getBoundingClientRect().height >= minimum) continue;
    }
    const text = (el.innerText || el.value || el.getAttribute('aria-label') || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
    const cls = typeof el.className === 'string' && el.className.trim()
      ? '.' + el.className.trim().split(/\\s+/).join('.') : '';
    out.push(`${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}${cls} "${text}" ${Math.round(r.width)}x${Math.round(r.height)}`);
  }
  return out;
}
"""


def small_targets(browser, minimum=44):
    """Visible controls (outside .table-wrap) whose box is shorter or narrower than minimum pixels.

    This measures rendered pixels, which html { zoom } scales: at the desktop zoom of 0.8 a 60px
    control measures 48. The phone layout resets the zoom to 1.
    """
    script = _SMALL_TARGETS_JS.replace("__IS_VISIBLE__", IS_VISIBLE_JS)
    return browser.js(f"({script})({int(minimum)})")


def is_visible(browser, selector):
    """True when the first element matching selector exists and is really visible."""
    return bool(
        browser.js(
            f"({IS_VISIBLE_JS})(document.querySelector({json.dumps(selector)}))"
        )
    )
