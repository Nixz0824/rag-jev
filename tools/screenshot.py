"""Capture README screenshots through the Chrome DevTools Protocol.

Headless Edge's `--screenshot` flag ignores query strings and cannot wait for a real
answer, so this drives the page properly: navigate, wait until the answer is rendered,
optionally scroll to a section, then capture.

Usage:
    python tools/screenshot.py --port 9222 --out docs/screenshots
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import time
import urllib.request
from pathlib import Path

import websockets

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
BASE = "http://127.0.0.1:18890"
PROFILE = Path(r"C:\Users\USER\AppData\Local\Temp\opencode\edge-cdp")

SHOTS = (
    {"name": "ask", "query": "?q=26.17 薇恩 W 真实伤害是多少", "section": "ask", "wait_answer": True},
    {"name": "jev", "query": "?q=26.17 薇恩 W 真实伤害是多少", "section": "jev", "wait_answer": True},
    {"name": "data", "query": "?view=data", "section": "data", "wait_answer": False},
    {"name": "flow", "query": "?view=flow", "section": "flow", "wait_answer": False},
    {"name": "compare", "query": "?view=compare", "section": "compare", "wait_answer": False},
)


def targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10) as response:
        return json.loads(response.read().decode())


class Session:
    def __init__(self, url: str, message_id: int = 0):
        self.url = url
        self.id = message_id

    async def send(self, method: str, **params):
        self.id += 1
        await self.ws.send(json.dumps({"id": self.id, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == self.id:
                return message.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.send("Runtime.evaluate", expression=expression, returnByValue=True)
        return result.get("result", {}).get("value")


async def capture(port: int, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    pages = [target for target in targets(port) if target["type"] == "page"]
    ws_url = pages[0]["webSocketDebuggerUrl"]
    session = Session(ws_url)
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        session.ws = ws
        await session.send("Page.enable")
        await session.send("Runtime.enable")
        await session.send("Emulation.setDeviceMetricsOverride", width=1440, height=1000, deviceScaleFactor=1, mobile=False)
        for shot in SHOTS:
            url = BASE + "/" + shot["query"]
            await session.send("Page.navigate", url=url)
            await asyncio.sleep(1.5)
            # Charts are fetched and rendered after load; they change page height, so
            # scrolling before they exist would land on the wrong section.
            for _ in range(40):
                count = await session.evaluate("document.querySelectorAll('.chart-body svg').length")
                if isinstance(count, int) and count >= 6:
                    break
                await asyncio.sleep(0.5)
            if shot["wait_answer"]:
                for _ in range(60):
                    length = await session.evaluate(
                        "(document.querySelector('#answer-body')||{}).textContent?.length || 0"
                    )
                    if isinstance(length, int) and length > 40:
                        break
                    await asyncio.sleep(0.5)
                await asyncio.sleep(1.0)
            # Scroll deterministically and verify. offsetTop is relative to the nearest
            # positioned ancestor (main), so the document coordinate must come from the
            # bounding rect. The answer path scrolls on its own, hence the retries.
            for _ in range(4):
                position = await session.evaluate(
                    "(() => { const el = document.getElementById('%s');"
                    " if (!el) return null; const top = el.getBoundingClientRect().top + window.scrollY;"
                    " window.scrollTo(0, Math.max(top - 12, 0));"
                    " return Math.round(window.scrollY); })()" % shot["section"]
                )
                await asyncio.sleep(0.6)
                if isinstance(position, int):
                    break
            result = await session.send("Page.captureScreenshot", format="png")
            path = out_dir / f"{shot['name']}.png"
            path.write_bytes(base64.b64decode(result["data"]))
            print(f"{shot['name']}.png  {path.stat().st_size // 1024} KB  scrollY={position}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="通过 CDP 截图")
    parser.add_argument("--port", type=int, default=9222)
    parser.add_argument("--out", default="docs/screenshots")
    args = parser.parse_args()

    process = subprocess.Popen(
        [
            EDGE,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-first-run",
            "--force-prefers-reduced-motion",
            f"--remote-debugging-port={args.port}",
            f"--user-data-dir={PROFILE}",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(40):
            try:
                targets(args.port)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        asyncio.run(capture(args.port, Path(args.out)))
    finally:
        process.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
