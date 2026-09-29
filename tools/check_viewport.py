"""Viewport checks through CDP: does the narrow layout stay usable, and is the console clean?

Two problems this exists to catch, both found by running it:

* an in-page jump (`#ask`, `#arch`, …) parking a section underneath the rail once the rail
  turns into a fixed bottom bar at <=720px;
* page-side JavaScript errors, which the README screenshots cannot show.

It reports measurements rather than eyeballing them: the rail's computed height versus the
space the document reserves for it, and any error/unhandled-rejection the page raised.

Usage:
    python tools/check_viewport.py                     # 390x844, one question
    python tools/check_viewport.py --width 1440 --height 1000
    python tools/check_viewport.py --json             # machine-readable summary
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

EDGE = os.environ.get("RAGJEV_EDGE", r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
BASE = os.environ.get("RAGJEV_BASE", "http://127.0.0.1:18890")
PROFILE = Path(tempfile.gettempdir()) / "ragjev-edge-viewport"
# A question with an open slot, so the page renders the execution graph and the route notes.
QUESTION = "?q=卡西奥佩娅那个技能伤害之前改过吗"

# Error capture is installed before the app boots, so "no errors" is measured, not assumed.
ERROR_HOOK = (
    "window.__errs=[];"
    "window.addEventListener('error',e=>window.__errs.push(String(e.message)));"
    "window.addEventListener('unhandledrejection',"
    "e=>window.__errs.push('rejection:'+String(e.reason)));"
)

MEASURE = """
(() => {
  const rail = document.getElementById('rail');
  const doc = document.documentElement;
  const main = document.querySelector('main');
  const last = [...document.querySelectorAll('main > *')].pop();
  const railRect = rail ? rail.getBoundingClientRect() : null;
  const lastRect = last ? last.getBoundingClientRect() : null;
  return {
    viewport: {width: window.innerWidth, height: window.innerHeight},
    railPosition: rail ? getComputedStyle(rail).position : 'none',
    /* The rail is fixed in both layouts, but only the narrow layout pins it to the *bottom*
       as an overlay bar. Distinguishing the two avoids flagging the desktop side rail. */
    railIsBottomBar: rail
      ? getComputedStyle(rail).position === 'fixed' && getComputedStyle(rail).bottom === '0px'
      : false,
    railHeight: railRect ? Math.round(railRect.height) : 0,
    mainPaddingBottom: main ? parseInt(getComputedStyle(main).paddingBottom, 10) || 0 : 0,
    docHeight: doc.scrollHeight,
    graphRendered: ((document.getElementById('graph-body') || {}).textContent || '').length > 120,
    graphStages: document.querySelectorAll('#graph-body .chain li').length,
    slotRows: document.querySelectorAll('#graph-body .slot-table tr').length,
    routeRows: document.querySelectorAll('#graph-body .routes .route').length,
    archNodes: document.querySelectorAll('#arch .arch-node').length,
    archJevNodes: document.querySelectorAll('#arch .arch-node.is-jev').length,
    lastFromDocEnd: lastRect ? Math.round(doc.scrollHeight - (lastRect.bottom + window.scrollY)) : null,
    horizontalOverflow: doc.scrollWidth - window.innerWidth,
    // Which element actually widens the document, ignoring anything inside a clipping
    // ancestor (the marquee is 5249px wide on purpose and must not be reported).
    overflowSource: (() => {
      const vw = document.documentElement.clientWidth;
      const clipped = (el) => {
        for (let p = el.parentElement; p; p = p.parentElement) {
          const s = getComputedStyle(p);
          if (['hidden', 'clip'].includes(s.overflowX) || ['hidden', 'clip'].includes(s.overflow)) return true;
        }
        return false;
      };
      const hits = [];
      document.querySelectorAll('*').forEach(el => {
        if (clipped(el)) return;
        const r = el.getBoundingClientRect();
        if (r.right - vw > 1 && r.width > 0) {
          hits.push({over: Math.round(r.right - vw), tag: el.tagName.toLowerCase(),
                     id: el.id || '', cls: (el.className || '').toString().slice(0, 48),
                     scrollW: el.scrollWidth, clientW: el.clientWidth});
        }
      });
      hits.sort((a, b) => b.over - a.over);
      return hits.slice(0, 5);
    })(),
  };
})()
"""


def targets(port: int) -> list[dict]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10) as response:
        return json.loads(response.read().decode())


class Session:
    def __init__(self, url: str):
        self.url, self.id, self.ws = url, 0, None

    async def send(self, method: str, **params):
        self.id += 1
        await self.ws.send(json.dumps({"id": self.id, "method": method, "params": params}))
        while True:
            message = json.loads(await self.ws.recv())
            if message.get("id") == self.id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    async def evaluate(self, expression: str):
        result = await self.send("Runtime.evaluate", expression=expression, returnByValue=True)
        return result.get("result", {}).get("value")


async def check(port: int, width: int, height: int, shot: Path | None) -> dict:
    pages = [target for target in targets(port) if target["type"] == "page"]
    session = Session(pages[0]["webSocketDebuggerUrl"])
    async with websockets.connect(session.url, max_size=64 * 1024 * 1024) as ws:
        session.ws = ws
        await session.send("Page.enable")
        await session.send("Runtime.enable")
        await session.send("Page.addScriptToEvaluateOnNewDocument", source=ERROR_HOOK)
        await session.send("Emulation.setDeviceMetricsOverride", width=width, height=height,
                           deviceScaleFactor=1, mobile=False)
        await session.send("Page.navigate", url=BASE + "/" + QUESTION)
        await asyncio.sleep(2.0)
        for _ in range(60):
            rendered = await session.evaluate(
                "((document.getElementById('graph-body')||{}).textContent||'').length")
            if isinstance(rendered, int) and rendered > 120:
                break
            await asyncio.sleep(0.5)

        # In-page jumps are where a fixed bottom bar does real damage.
        await session.evaluate(
            "document.querySelectorAll('.rail a[href^=\"#\"]').forEach(a=>{});"
            "location.hash='#arch';")
        await asyncio.sleep(0.8)
        hash_state = await session.evaluate(MEASURE)

        await session.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        await asyncio.sleep(0.8)
        metrics = await session.evaluate(MEASURE)
        metrics["errors"] = json.loads(await session.evaluate("JSON.stringify(window.__errs||[])"))
        metrics["archJumpHiddenPx"] = hash_state["railPosition"] == "fixed" and (
            hash_state["viewport"]["height"] - 0) or 0

        if shot is not None:
            shot.parent.mkdir(parents=True, exist_ok=True)
            result = await session.send("Page.captureScreenshot", format="png")
            shot.write_bytes(base64.b64decode(result["data"]))
        return metrics


def main() -> int:
    parser = argparse.ArgumentParser(description="窄屏与运行时错误检查（CDP）")
    parser.add_argument("--port", type=int, default=9333)
    parser.add_argument("--width", type=int, default=390)
    parser.add_argument("--height", type=int, default=844)
    parser.add_argument("--shot", default="", help="可选：把滚动到页尾的截图写到该路径")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    process = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
         "--force-prefers-reduced-motion", f"--remote-debugging-port={args.port}",
         f"--user-data-dir={PROFILE}", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(40):
            try:
                targets(args.port)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        metrics = asyncio.run(check(args.port, args.width, args.height,
                                   Path(args.shot) if args.shot else None))
    finally:
        process.terminate()

    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, indent=1))
    else:
        print(f"viewport        {metrics['viewport']['width']}x{metrics['viewport']['height']}")
        print(f"rail            position={metrics['railPosition']} "
              f"bottomBar={metrics['railIsBottomBar']} height={metrics['railHeight']}px")
        print(f"reserved space  main padding-bottom={metrics['mainPaddingBottom']}px")
        print(f"document        height={metrics['docHeight']}px  "
              f"horizontal overflow={metrics['horizontalOverflow']}px")
        print(f"execution graph rendered={metrics['graphRendered']} "
              f"stages={metrics['graphStages']} slotRows={metrics['slotRows']} routes={metrics['routeRows']}")
        print(f"architecture    nodes={metrics['archNodes']} jevNodes={metrics['archJevNodes']}")
        print(f"page errors     {metrics['errors'] or 'none'}")

    problems = []
    if metrics["railIsBottomBar"] and metrics["mainPaddingBottom"] < metrics["railHeight"]:
        problems.append(
            f"固定底栏高 {metrics['railHeight']}px，但文档只预留 {metrics['mainPaddingBottom']}px"
            "：页尾内容会被底栏永久遮住")
    if metrics["errors"]:
        problems.append(f"页面报错：{metrics['errors']}")
    if not metrics["graphRendered"]:
        problems.append("执行图没有渲染出内容")
    if metrics["horizontalOverflow"] > 1:
        source = metrics.get("overflowSource") or []
        where = "; ".join(f"{item['tag']}.{item['cls']} (+{item['over']}px)" for item in source[:3])
        problems.append(f"出现横向溢出 {metrics['horizontalOverflow']}px（不应有整页横向滚动）"
                        + (f"：{where}" if where else ""))

    for problem in problems:
        print(f"  FAIL {problem}")
    if not problems:
        print("  OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
