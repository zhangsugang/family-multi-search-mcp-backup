#!/usr/bin/env python3
"""
multi-search MCP — 八源并行搜索集合

搜索源:
  1. 豆包桌面版   — CDP 驱动 Doubao 2.app，抖音/头条生态，免费
  2. Tavily       — 5 key 轮换，英文/全球网页强
  3. 元宝网页版   — 无头浏览器 + 微信登录态，微信公众号/视频号生态
  4. Exa          — Agent-Reach 渠道（mcporter 调 Exa 语义搜索，免 key）
  5. 文心助手     — 无头匿名可用，百度生态
  6. Gemini       — 隔离无头 Chrome，YouTube 社区定向发现
  7. Grok         — 隔离普通 Chrome + CDP，X/Twitter 原帖定向发现
  8. 千问         — 隔离普通 Chrome + CDP，公开阿里生态只读发现

工具:
  - doubao_search(query)    豆包（抖音/头条数据）
  - tavily_search(query)    Tavily
  - yuanbao_search(query)   元宝网页版（微信公众号源）
  - exa_search(query)       Exa 语义搜索（Agent-Reach）
  - wenxin_search(query)    百度文心（百度生态，匿名）
  - gemini_search(query)    Gemini（YouTube 社区定向）
  - grok_search(query)      Grok（X/Twitter 社区定向）
  - qianwen_search(query)   千问（公开阿里生态只读发现）
  - research_round(query)   一轮八源结构化证据与经营决策输入
  - search_all(query)       8 源并行，多源交叉验证
  - search_status()         各源健康检查

配置: private/search-mcp/config.json
登录态: private/yuanbao/storage-state.json（元宝，微信扫码一次长期有效）
"""
import asyncio
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import weakref
from contextlib import asynccontextmanager
from http.client import HTTPSConnection
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx
from mcp.server.fastmcp import FastMCP

from tools.browser_discovery import discover_browser
from tools.browser_pool import BrowserSessionPool
from tools.evidence_digest import build_evidence_digest
from tools.search_evidence import (
    build_research_round,
    has_platform_observation_signal,
    load_profiles,
    strip_platform_stream_status,
)
from tools.search_provider_registry import (
    AI_PROVIDER_IDS as _AI_PROVIDER_IDS,
    PROVIDER_NAMES as _PROVIDER_NAMES,
    PROVIDER_SPECS,
    provider_ids,
)
from tools.research_brief import build_research_brief, normalize_research_brief
from tools.search_queries import build_provider_query_lanes
from tools.search_service import SearchService


BASE = PROJECT_ROOT
EVIDENCE_PROFILE_FILE = BASE / "config" / "multi-search-evidence-profiles.json"

PROVIDER_PRESERVED_ANSWER_LIMITS = {
    "doubao": 12000,
    "yuanbao": 10000,
    "wenxin": 8000,
    "tavily": 8000,
    "exa": 8000,
    "gemini": 8000,
    "grok": 8000,
    "qianwen": 8000,
}


BROWSER_PROVIDER_CONFIG = {
    "doubao": {"capacity": 20, "active_limit": 10},
    "yuanbao": {"capacity": 20, "active_limit": 10},
    "wenxin": {"capacity": 20, "active_limit": 20},
    "grok": {"capacity": 20, "active_limit": 20},
    "gemini": {"capacity": 20, "active_limit": 10},
    "qianwen": {"capacity": 20, "active_limit": 5},
}
_BROWSER_PROVIDER_LIMITERS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_BROWSER_PROVIDER_STARTUP_LOCKS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_BROWSER_PROVIDER_POOLS: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _browser_provider_active_limit(source_id: str) -> int:
    default = BROWSER_PROVIDER_CONFIG[source_id]["active_limit"]
    configured = get_runtime_config().get("browser_provider_active_limits", {})
    candidate = configured.get(source_id) if isinstance(configured, dict) else None
    if isinstance(candidate, bool) or not isinstance(candidate, int):
        return default
    capacity = BROWSER_PROVIDER_CONFIG[source_id]["capacity"]
    return candidate if 1 <= candidate <= capacity else default


def _browser_provider_limiter(source_id: str) -> asyncio.Semaphore:
    active_limit = _browser_provider_active_limit(source_id)
    loop = asyncio.get_running_loop()
    per_loop = _BROWSER_PROVIDER_LIMITERS.setdefault(loop, {})
    cached = per_loop.get(source_id)
    if cached is None or cached[0] != active_limit:
        cached = (active_limit, asyncio.Semaphore(active_limit))
        per_loop[source_id] = cached
    return cached[1]


@asynccontextmanager
async def _browser_provider_slot(source_id: str):
    """Reserve one logical slot, then admit measured active browser work."""
    pool = await _browser_provider_pool(source_id)
    async with pool.session():
        async with _browser_provider_limiter(source_id):
            yield


def _browser_provider_startup_lock(source_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    per_loop = _BROWSER_PROVIDER_STARTUP_LOCKS.setdefault(loop, {})
    return per_loop.setdefault(source_id, asyncio.Lock())


async def _create_browser_reservation() -> object:
    return object()


async def _destroy_browser_reservation(reservation: object) -> None:
    return None


async def _browser_provider_pool(source_id: str) -> BrowserSessionPool[object]:
    loop = asyncio.get_running_loop()
    per_loop = _BROWSER_PROVIDER_POOLS.setdefault(loop, {})
    pool = per_loop.get(source_id)
    if pool is not None:
        return pool

    async with _browser_provider_startup_lock(f"{source_id}:pool"):
        pool = per_loop.get(source_id)
        if pool is None:
            capacity = BROWSER_PROVIDER_CONFIG[source_id]["capacity"]
            pool = BrowserSessionPool(
                f"{source_id}-reservations",
                capacity,
                _create_browser_reservation,
                _destroy_browser_reservation,
            )
            await pool.start(prewarm=capacity)
            per_loop[source_id] = pool
    return pool


async def _ensure_browser_ready(source_id: str, ensure_function) -> None:
    """Serialize one provider's process startup without serializing its searches."""
    async with _browser_provider_startup_lock(source_id):
        await asyncio.to_thread(ensure_function)


def _private_root() -> Path:
    """Resolve the private state root selected for this MCP process."""
    configured_root = os.environ.get("MULTI_SEARCH_PRIVATE_ROOT", "").strip()
    if configured_root:
        return Path(configured_root).expanduser()
    return BASE / "private"


def _config_file() -> Path:
    return _private_root() / "search-mcp" / "config.json"


def _yuanbao_state_path() -> Path:
    return _private_root() / "yuanbao" / "storage-state.json"


def _wenxin_state_path() -> Path:
    return _private_root() / "wenxin" / "storage-state.json"


def _gemini_profile_path() -> Path:
    return _private_root() / "gemini" / "chrome-profile"


def _grok_profile_path() -> Path:
    return _private_root() / "grok" / "runtime-profile"


def _qianwen_profile_path() -> Path:
    return _private_root() / "qianwen" / "runtime-profile"


mcp = FastMCP("multi-search")

_CONFIG_CACHE: dict | None = None
_TAVILY_KEYS_FINGERPRINT: tuple[str, ...] | None = None
_TAVILY_KEY_CYCLE = None


def load_config() -> dict:
    config_file = _config_file()
    if config_file.exists():
        return json.loads(config_file.read_text())
    return {}


def get_runtime_config() -> dict:
    """Load private runtime settings only when a provider needs them."""
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        loaded = load_config()
        _CONFIG_CACHE = loaded if isinstance(loaded, dict) else {}
    return _CONFIG_CACHE


def _next_tavily_key(keys: list[str]) -> str:
    """Rotate keys while replacing the cycle when lazy configuration changes."""
    global _TAVILY_KEYS_FINGERPRINT, _TAVILY_KEY_CYCLE
    fingerprint = tuple(keys)
    if _TAVILY_KEYS_FINGERPRINT != fingerprint or _TAVILY_KEY_CYCLE is None:
        _TAVILY_KEYS_FINGERPRINT = fingerprint
        _TAVILY_KEY_CYCLE = itertools.cycle(fingerprint)
    return next(_TAVILY_KEY_CYCLE)

# ================================================================ 豆包
DOUBAO_CDP_URL = "http://127.0.0.1:9333"
DOUBAO_CDP_PORT = 9333
DOUBAO_CHAT_URL = "chrome://doubao-chat/chat/"
DOUBAO_EXTRACT_JS = """() => {
  const rows = [...document.querySelectorAll('.v_list_row')];
  const ui = rows.map((r,i)=>r.querySelector('[class*="bg-g-send-msg-bubble-bg"]')?i:-1).filter(i=>i>=0).pop();
  if (ui===undefined||!rows[ui+1]) return {answer:''};
  const ai = rows[ui+1];
  const c = ai.cloneNode(true);
  c.querySelectorAll('[class*="suggest"],button,svg').forEach(e=>e.remove());
  return {answer:(c.textContent||'').trim()};
}"""

DOUBAO_REFS_JS = """() => [...document.querySelectorAll('a[href^="http"]')]
  .map(a => ({title: (a.innerText||'').trim().replace(/\\n/g,' ').slice(0,100), url: a.href}))
  .filter(x => x.title && !x.url.includes('doubao.com'))
  .slice(0, 30)"""


def _browser_stream_wait_ms(timeout: float, waited: float) -> int:
    """Return the next browser polling delay without exceeding its budget."""
    remaining_ms = int(max(0.0, float(timeout) - waited) * 1000)
    return min(1000, remaining_ms)


def _browser_stream_deadline(timeout: float) -> float:
    """Return an absolute monotonic deadline for browser streaming work."""
    return time.monotonic() + max(0.0, float(timeout))


def _browser_deadline_remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _browser_deadline_wait_ms(deadline: float) -> int:
    remaining_ms = int(_browser_deadline_remaining(deadline) * 1000)
    return min(1000, remaining_ms)


async def _new_browser_task_page(browser, provider_name: str):
    """Create a clean page so one request cannot inherit another request's chat."""
    if not browser.contexts:
        raise RuntimeError(f"{provider_name} 浏览器上下文不可用")
    return await browser.contexts[0].new_page()


async def _browser_deadline_call(factory, deadline: float):
    """Run one browser operation only within the remaining wall-clock budget."""
    remaining = _browser_deadline_remaining(deadline)
    if remaining <= 0:
        raise asyncio.TimeoutError
    result = await asyncio.wait_for(factory(), timeout=remaining)
    if _browser_deadline_remaining(deadline) <= 0:
        raise asyncio.TimeoutError
    return result


def _cdp_alive(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _ensure_doubao_app() -> None:
    config = get_runtime_config()
    cdp = DOUBAO_CDP_URL
    if _cdp_alive(cdp):
        return
    app = config.get("doubao_app_path", "/Applications/Doubao 2.app")
    subprocess.run(["osascript", "-e", f'tell application "{Path(app).stem}" to quit'],
                   capture_output=True, timeout=10)
    time.sleep(3)
    # 防后台节流参数：窗口即使不激活，页面也正常渲染
    subprocess.Popen(["open", "-a", app, "--args",
                      f"--remote-debugging-port={DOUBAO_CDP_PORT}",
                      "--disable-backgrounding-occluded-windows",
                      "--disable-renderer-backgrounding"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(30):
        time.sleep(1)
        if _cdp_alive(cdp):
            return
    raise RuntimeError("豆包桌面版 CDP 端口 9333 启动超时")


async def _doubao_query(query: str, timeout: int = 90,
                        progress: dict | None = None) -> dict:
    from playwright.async_api import async_playwright

    config = get_runtime_config()
    await _ensure_browser_ready("doubao", _ensure_doubao_app)
    # 注意：不 activate、不 bring_to_front，全程后台运行不抢窗口焦点

    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(DOUBAO_CDP_URL)
        page = await _new_browser_task_page(browser, "豆包")
        try:
            await page.goto(
                config.get("doubao_chat_url", DOUBAO_CHAT_URL),
                wait_until="domcontentloaded",
            )
            # 焦点仿真：页面以为自己在前台（真实窗口不动）
            session = await page.context.new_cdp_session(page)
            await session.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
            await page.wait_for_timeout(1000)
            box = page.locator('textarea[placeholder*="发消息"]').first
            await box.wait_for(state="visible", timeout=15000)
            await box.click()
            await box.fill(query)
            await page.wait_for_timeout(300)
            await box.press("Enter")

            last, stable = "", 0
            done = False
            deadline = _browser_stream_deadline(timeout)
            while wait_ms := _browser_deadline_wait_ms(deadline):
                try:
                    await _browser_deadline_call(
                        lambda: page.wait_for_timeout(wait_ms), deadline
                    )
                    cur = (await _browser_deadline_call(
                        lambda: page.evaluate(DOUBAO_EXTRACT_JS), deadline
                    ))["answer"]
                except asyncio.TimeoutError:
                    break
                except Exception:
                    continue  # 流式渲染中页面可能短暂不可评估，不判失败
                stream_answer = strip_platform_stream_status(cur)
                is_status = not _is_substantive_stream_answer(stream_answer, ())
                if progress is not None and stream_answer and not is_status:
                    progress["answer"] = stream_answer
                if stream_answer and not is_status and stream_answer == last:
                    stable += 1
                    if stable >= 3:
                        done = True
                        break
                else:
                    stable = 0
                last = stream_answer

            refs = []
            try:
                before = {
                    r["url"] for r in await _browser_deadline_call(
                        lambda: page.evaluate(DOUBAO_REFS_JS), deadline
                    )
                }
                btns = page.locator('div.cursor-pointer:has-text("篇资料")')
                count = await _browser_deadline_call(btns.count, deadline)
                if count > 0:
                    await _browser_deadline_call(
                        lambda: btns.nth(count - 1).click(), deadline
                    )
                    await _browser_deadline_call(
                        lambda: page.wait_for_timeout(1500), deadline
                    )
                    extracted_refs = await _browser_deadline_call(
                        lambda: page.evaluate(DOUBAO_REFS_JS), deadline
                    )
                    for reference in extracted_refs:
                        if reference["url"] not in before:
                            before.add(reference["url"])
                            refs.append(reference)
                    await _browser_deadline_call(
                        lambda: page.keyboard.press("Escape"), deadline
                    )
            except Exception:
                pass
            return {
                "answer": last,
                "references": refs,
                "partial": not done,
                "conversation_url": getattr(page, "url", ""),
            }
        finally:
            await page.close()


# ================================================================ Tavily
async def _tavily_query_async(
    query: str,
    max_results: int = 8,
    timeout: float = 25,
    max_key_attempts: int = 2,
) -> dict:
    config = get_runtime_config()
    keys = [
        key for key in config.get("tavily_api_keys", [])
        if isinstance(key, str) and key
    ]
    if not keys:
        raise RuntimeError("未配置 Tavily API key")

    attempts = min(len(keys), max(1, max_key_attempts))
    last_error = "unknown"
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout)) as client:
        for _ in range(attempts):
            key = _next_tavily_key(keys)
            try:
                response = await client.post(
                    "https://api.tavily.com/search",
                    json={
                        "api_key": key,
                        "query": query,
                        "max_results": max_results,
                        "search_depth": "basic",
                        "include_answer": False,
                    },
                )
                if response.status_code in {401, 429} or response.status_code >= 500:
                    last_error = f"HTTP {response.status_code}"
                    continue
                response.raise_for_status()
                data = response.json()
                return {
                    "answer": data.get("answer", ""),
                    "results": [
                        {
                            "title": item.get("title", ""),
                            "url": item.get("url", ""),
                            "content": item.get("content", ""),
                        }
                        for item in data.get("results", [])
                    ],
                }
            except (httpx.TimeoutException, httpx.NetworkError) as error:
                last_error = type(error).__name__
                continue
    raise RuntimeError(f"Tavily 尝试失败: {last_error}")


# ================================================================ 元宝网页版
YB_DIALOGUE_JS = """() => {
  const d = document.querySelector('.agent-dialogue__content--common__content')
         || document.querySelector('.agent-dialogue');
  return d ? d.innerText : '';
}"""

YB_REFS_JS = """() => [...document.querySelectorAll('.agent-dialogue-references__item')]
  .map(e => {
    const anchor = e.querySelector('a[href]');
    return {
      title: (e.innerText || '').trim().replace(/\\n+/g, ' | ').slice(0, 200),
      url: anchor ? anchor.href : '',
      publisher: ''
    };
  })
  .filter(x => x.title)"""


async def _yuanbao_query(query: str, timeout: int = 90,
                         progress: dict | None = None) -> dict:
    from playwright.async_api import async_playwright

    state_path = _yuanbao_state_path()
    if not state_path.exists():
        raise RuntimeError("元宝登录态缺失")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(storage_state=str(state_path))
        page = await ctx.new_page()
        try:
            await page.goto("https://yuanbao.tencent.com/chat",
                            wait_until="domcontentloaded")
            await page.wait_for_timeout(4000)

            # 切到 Search 智能体（强制联网搜索）
            try:
                await page.locator("button.ybc-atomSelect-tools").first.click(force=True)
                await page.wait_for_timeout(700)
                await page.locator(
                    '.t-popup__content >> text=/^(Search|搜索)$/').first.click()
                await page.wait_for_timeout(500)
            except Exception:
                pass  # 菜单结构变化则用默认模式

            box = page.locator("[contenteditable=true]").first
            await box.click()
            await box.fill(query)
            await box.press("Enter")

            last, stable = "", 0
            done = False
            deadline = _browser_stream_deadline(timeout)
            while wait_ms := _browser_deadline_wait_ms(deadline):
                try:
                    await _browser_deadline_call(
                        lambda: page.wait_for_timeout(wait_ms), deadline
                    )
                    txt = await _browser_deadline_call(
                        lambda: page.evaluate(YB_DIALOGUE_JS), deadline
                    )
                except asyncio.TimeoutError:
                    break
                except Exception:
                    continue  # 流式渲染中页面可能短暂不可评估，不判失败
                # 取用户问题之后的部分
                cur = txt.split(query, 1)[-1] if query in txt else txt
                for marker in ("\nSources\n", "\n参考资料", "\nDownload the desktop"):
                    if marker in cur:
                        cur = cur.split(marker)[0]
                cur = cur.strip()
                # 状态文案不算完成（搜索/思考阶段的固定提示）
                stream_answer = strip_platform_stream_status(cur)
                is_status = not _is_substantive_stream_answer(stream_answer, ())
                if progress is not None and stream_answer and not is_status:
                    progress["answer"] = stream_answer  # 流式进度收割点：供 search_all 到点取部分内容
                if stream_answer and not is_status and stream_answer == last:
                    stable += 1
                    if stable >= 3:
                        done = True
                        break
                else:
                    stable = 0
                last = stream_answer

            # 展开引用面板，且所有操作继续受同一绝对截止时间约束。
            refs = []
            try:
                src = page.locator('[class*=ToolbarSearchGuid_source]').first
                if await _browser_deadline_call(src.count, deadline) > 0:
                    await _browser_deadline_call(src.click, deadline)
                    await _browser_deadline_call(
                        lambda: page.wait_for_timeout(1500), deadline
                    )
                    refs = await _browser_deadline_call(
                        lambda: page.evaluate(YB_REFS_JS), deadline
                    )
            except Exception:
                pass
            return {
                "answer": last,
                "references": refs,
                "partial": not done,
                "conversation_url": getattr(page, "url", ""),
            }
        finally:
            await ctx.close()
            await browser.close()


# ================================================================ Exa (Agent-Reach)
async def _exa_query_async(
    query: str,
    num_results: int = 8,
    timeout: float = 55,
) -> str:
    mcporter = shutil.which("mcporter")
    if not mcporter:
        raise RuntimeError("mcporter 未安装（npm i -g mcporter）")
    args = json.dumps({"query": query, "numResults": num_results})
    process = await asyncio.create_subprocess_exec(
        mcporter,
        "call",
        "exa",
        "web_search_exa",
        "--args",
        args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        if process.returncode is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(process.communicate(), timeout=2)
        except asyncio.TimeoutError:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            try:
                await asyncio.wait_for(process.communicate(), timeout=2)
            except asyncio.TimeoutError:
                pass
        raise
    if process.returncode != 0:
        raise RuntimeError("Exa 调用失败")
    output = stdout.decode("utf-8", errors="replace").strip()
    if not output:
        raise RuntimeError("Exa 返回空")
    return output


# ================================================================ 文心（百度）
WX_ANSWER_JS = """() => {
  // 文心回答气泡 = div.answer-box（最后一个），剔除追问区与参考列表
  const boxes = [...document.querySelectorAll('div[class*="answer-box"]')];
  if (!boxes.length) return '';
  const box = boxes[boxes.length - 1];
  const c = box.cloneNode(true);
  c.querySelectorAll('[class*=answer-ask], [class*=_reference_], button, svg').forEach(e => e.remove());
  return (c.textContent || '').trim();
}"""

WX_REFS_JS = """() => [...document.querySelectorAll('[class*=_reference-item_]')]
  .map(e => (e.innerText||'').trim().replace(/\\n+/g, ' ').slice(0, 150))"""


async def _wenxin_query(query: str, timeout: int = 90,
                        progress: dict | None = None) -> dict:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        # 有登录态用登录态（历史同步/额度更高），没有则匿名
        state_path = _wenxin_state_path()
        if state_path.exists():
            ctx = await browser.new_context(storage_state=str(state_path))
        else:
            ctx = await browser.new_context()
        page = await ctx.new_page()
        try:
            await page.goto("https://wenxin.baidu.com/", wait_until="domcontentloaded")
            await page.wait_for_timeout(3500)
            box = page.locator("textarea").first
            await box.fill(query)
            await box.press("Enter")

            last, stable, polls = "", 0, 0
            done = False
            deadline = _browser_stream_deadline(timeout)
            while wait_ms := _browser_deadline_wait_ms(deadline):
                try:
                    await _browser_deadline_call(
                        lambda: page.wait_for_timeout(wait_ms), deadline
                    )
                except asyncio.TimeoutError:
                    break
                polls += 1
                # 文档编写模式：点"改用对话直接回答"切回
                if polls % 3 == 0:
                    try:
                        fb = page.locator('text=改用对话直接回答').first
                        if await _browser_deadline_call(fb.count, deadline) > 0:
                            await _browser_deadline_call(
                                lambda: fb.click(timeout=1000), deadline
                            )
                            last, stable = "", 0
                            continue
                    except asyncio.TimeoutError:
                        break
                    except Exception:
                        pass
                try:
                    cur = await _browser_deadline_call(
                        lambda: page.evaluate(WX_ANSWER_JS), deadline
                    )
                except asyncio.TimeoutError:
                    break
                except Exception:
                    continue  # 流式渲染中页面可能短暂不可评估，不判失败
                stream_answer = strip_platform_stream_status(cur)
                is_substantive = _is_substantive_stream_answer(stream_answer, ())
                if progress is not None and is_substantive:
                    progress["answer"] = stream_answer  # 流式进度收割点：供 search_all 到点取部分内容
                if is_substantive and stream_answer == last:
                    stable += 1
                    if stable >= 3:
                        done = True
                        break
                else:
                    stable = 0
                last = stream_answer
            # 清洗：先逐行剥离流式状态，再只删除不含商业信号的噪音行。
            last = strip_platform_stream_status(last)
            noise = ("调用工具", "FinScope")
            lines = []
            for raw_line in last.split("\n"):
                line = strip_platform_stream_status(raw_line)
                if not line:
                    continue
                if any(marker in line for marker in noise) and not has_platform_observation_signal(line):
                    continue
                lines.append(line)
            # 去掉开头残留的短工具标签行（如重复的工具名）
            while lines and len(lines[0].strip()) <= 15 and not any(
                    c in lines[0] for c in "。，：:0123456789") and not has_platform_observation_signal(lines[0]):
                lines.pop(0)
            # 去掉"共参考N篇资料"编号列表块（引用已由 references 单独返回）
            text = "\n".join(lines)
            m = re.search(r"共参考\d+篇资料", text)
            if m:
                prefix = text[:m.start()].rstrip()
                rest = text[m.end():].split("\n")
                out_lines, n, expect_title = [], 1, False
                for line in rest:
                    s = line.strip()
                    if re.match(rf"^{n}\.?\s*$", s) or s.startswith(f"{n}. ") or s.startswith(f"{n}．"):
                        expect_title = s == str(n) or s == f"{n}."
                        n += 1
                        continue
                    if expect_title:
                        expect_title = False
                        continue  # 编号对应的标题行
                    out_lines.append(line)
                suffix = "\n".join(out_lines).strip()
                text = "\n".join(part for part in (prefix, suffix) if part)
            # 去掉尾部残留的编号来源行（"N. 标题-站点"）
            tail_lines = [l for l in text.split("\n")
                          if not re.match(r"^\s*\d+\.\s*\d*\.?\s*\S+(-|—|_)", l)]
            last = "\n".join(tail_lines).strip()
            refs = []
            try:
                refs = await _browser_deadline_call(
                    lambda: page.evaluate(WX_REFS_JS), deadline
                )
            except Exception:
                pass
            return {
                "answer": last,
                "references": refs,
                "partial": not done,
                "conversation_url": getattr(page, "url", ""),
            }
        finally:
            await ctx.close()
            await browser.close()


# ================================================================ Gemini / Grok 社区源
_COMMUNITY_TRACKING_PARAMETERS = {
    "referrer", "feature", "si", "pp", "s", "t", "utm_source", "utm_medium",
    "utm_campaign", "utm_term", "utm_content",
}
GROK_CDP_URL = "http://127.0.0.1:9555"
GROK_CDP_PORT = 9555
_GROK_LIFECYCLE_STATES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def _grok_lifecycle_state() -> dict:
    loop = asyncio.get_running_loop()
    state = _GROK_LIFECYCLE_STATES.get(loop)
    if state is None:
        state = {"lock": asyncio.Lock(), "active_requests": 0, "idle_task": None}
        _GROK_LIFECYCLE_STATES[loop] = state
    return state


def _chrome_executable() -> str:
    configured = get_runtime_config().get("chrome_executable", "")
    explicit = configured if isinstance(configured, str) else None
    selected = discover_browser(explicit)
    if selected is None:
        raise RuntimeError(
            "Chrome、Chromium 或 Edge 可执行文件缺失；请配置 chrome_executable "
            "或 MULTI_SEARCH_CHROME_PATH。"
        )
    return str(selected)


def _normalize_community_url(url: str) -> str:
    """Canonicalize direct community links and remove sharing parameters."""
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return url
    host = parsed.netloc.lower().split(":", 1)[0]
    if host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"}:
        post = re.search(r"^/([^/]+)/status/(\d+)", parsed.path)
        if post:
            return f"https://x.com/{post.group(1)}/status/{post.group(2)}"

    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            video_id = dict(parse_qsl(parsed.query, keep_blank_values=True)).get("v", "")
        else:
            short = re.match(r"^/shorts/([^/?#]+)", parsed.path)
            if short:
                video_id = short.group(1)
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"

    query = urlencode(
        [
            (name, value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            if name.lower() not in _COMMUNITY_TRACKING_PARAMETERS
        ],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _community_references(items: object, platform: str) -> list[dict]:
    """Keep only direct YouTube videos or X posts and deduplicate their URLs."""
    if not isinstance(items, list):
        return []
    references, seen = [], set()
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("href") or item.get("url")
        if not isinstance(raw_url, str) or not raw_url:
            continue
        url = _normalize_community_url(raw_url)
        try:
            parsed = urlsplit(url)
        except ValueError:
            continue
        host = parsed.netloc.lower().split(":", 1)[0]
        if platform == "youtube":
            valid = (
                host in {"youtube.com", "www.youtube.com", "m.youtube.com"}
                and (parsed.path == "/watch" or parsed.path.startswith("/shorts/"))
            ) or host == "youtu.be"
            publisher = "YouTube"
        else:
            valid = host in {"x.com", "www.x.com", "twitter.com", "www.twitter.com"} \
                and bool(re.search(r"/status/\d+", parsed.path))
            publisher = "X"
        if not valid or url in seen:
            continue
        seen.add(url)
        title = item.get("text") if isinstance(item.get("text"), str) else ""
        title = _clean_text(title).replace("\n", " ")[:160] or url
        references.append({"title": title, "url": url, "publisher": publisher})
    return references


async def _community_response_text(page, query: str, platform: str) -> str:
    selectors = (
        ("model-response", ".model-response-text", "message-content")
        if platform == "youtube"
        else ('[data-testid*="message"]', '[class*="message"]', ".prose")
    )
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = await locator.count()
            if count:
                text = (await locator.nth(count - 1).inner_text()).strip()
                if text:
                    return text
        except Exception:
            continue
    body = (await page.locator("body").inner_text()).strip()
    return body.split(query, 1)[-1].strip() if query in body else ""


async def _wait_for_community_answer(page, query: str, timeout: int,
                                     progress: dict | None = None,
                                     platform: str = "youtube") -> tuple[str, bool]:
    last, stable = "", 0
    for poll in range(max(int(timeout), 0)):
        await page.wait_for_timeout(1000)
        try:
            answer = await _community_response_text(page, query, platform)
        except Exception:
            continue
        answer = strip_platform_stream_status(answer)
        if progress is not None and _is_substantive_stream_answer(answer, ()):
            progress["answer"] = answer
        if progress is not None and (poll + 1) % 5 == 0:
            try:
                links = await page.locator("a[href]").evaluate_all(
                    "els => els.map(a => ({text:(a.innerText||a.getAttribute('aria-label')||'').trim(), href:a.href}))"
                )
                progress["references"] = _community_references(links, platform)
            except Exception:
                pass
        if answer and answer == last:
            stable += 1
            if stable >= 7 and len(answer) >= 200 \
                    and _is_substantive_stream_answer(answer, ()):
                return answer, True
        else:
            stable = 0
        last = answer
    return last, False


GEMINI_CDP_URL = "http://127.0.0.1:9556"
GEMINI_CDP_PORT = 9556


def _ensure_gemini_browser() -> None:
    """Start one isolated headless Gemini browser for independent task pages."""
    if _cdp_alive(GEMINI_CDP_URL):
        return
    profile = _gemini_profile_path()
    if not profile.exists():
        raise RuntimeError("Gemini 登录配置缺失")
    chrome = _chrome_executable()
    if not Path(chrome).exists():
        raise RuntimeError("Chrome 可执行文件缺失")
    try:
        subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={GEMINI_CDP_PORT}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--profile-directory=Default",
                "--headless=new",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "https://gemini.google.com/app",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        raise RuntimeError("Gemini 浏览器启动失败") from None
    for _ in range(30):
        time.sleep(1)
        if _cdp_alive(GEMINI_CDP_URL):
            return
    raise RuntimeError("Gemini 浏览器启动超时")


async def _gemini_query(query: str, timeout: int = 120,
                        progress: dict | None = None) -> dict:
    """Query Gemini on a fresh page in its isolated headless Chrome profile."""
    from playwright.async_api import async_playwright

    await _ensure_browser_ready("gemini", _ensure_gemini_browser)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(GEMINI_CDP_URL)
        page = await _new_browser_task_page(browser, "Gemini")
        try:
            await page.goto("https://gemini.google.com/app", wait_until="domcontentloaded")
            editor = page.locator(
                '[aria-label="为 Gemini 输入提示"][contenteditable="true"], '
                '[role="textbox"][aria-label*="Gemini"], '
                '[contenteditable="true"]'
            ).first
            await editor.wait_for(state="visible", timeout=30000)
            await editor.click()
            await editor.fill(query)
            await editor.press("Enter")
            answer, done = await _wait_for_community_answer(
                page, query, timeout, progress, platform="youtube"
            )
            links = await page.locator("a[href]").evaluate_all(
                "els => els.map(a => ({text:(a.innerText||a.getAttribute('aria-label')||'').trim(), href:a.href}))"
            )
            return {
                "answer": answer,
                "references": _community_references(links, "youtube"),
                "partial": not done,
                "conversation_url": getattr(page, "url", ""),
            }
        finally:
            await page.close()


def _ensure_grok_browser() -> None:
    """Start isolated normal Chrome for Grok when Cloudflare blocks headless mode."""
    if _cdp_alive(GROK_CDP_URL):
        return
    profile = _grok_profile_path()
    if not profile.exists():
        raise RuntimeError("Grok 登录配置缺失")
    chrome = _chrome_executable()
    if not Path(chrome).exists():
        raise RuntimeError("Chrome 可执行文件缺失")
    subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={GROK_CDP_PORT}",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={profile}",
            "--profile-directory=Default",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "https://grok.com/",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    for _ in range(30):
        time.sleep(1)
        if _cdp_alive(GROK_CDP_URL):
            return
    raise RuntimeError("Grok Chrome CDP 启动超时")


async def _close_grok_browser() -> None:
    """Close the isolated Grok browser through CDP without exposing runtime details."""
    from playwright.async_api import async_playwright

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.connect_over_cdp(GROK_CDP_URL)
            session = await browser.new_browser_cdp_session()
            await session.send("Browser.close")
    except Exception as error:
        message = str(error).lower()
        if any(marker in message for marker in ("closed", "connection", "target")):
            return
        raise


async def _grok_idle_shutdown(state: dict, delay_seconds: float = 600) -> None:
    """Close Grok after an idle interval under loop-local coordination."""
    await asyncio.sleep(max(0.0, delay_seconds))
    async with state["lock"]:
        if state["active_requests"] == 0:
            state["idle_task"] = None
            await _close_grok_browser()


def _consume_background_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except BaseException:
        pass


def _schedule_grok_idle_shutdown(state: dict, delay_seconds: float = 600) -> None:
    """Replace this loop's pending Grok idle timer with a new one."""
    pending = state["idle_task"]
    if pending is not None and not pending.done():
        pending.cancel()
    task = asyncio.create_task(_grok_idle_shutdown(state, delay_seconds))
    task.add_done_callback(_consume_background_task_exception)
    state["idle_task"] = task


def _cancel_grok_idle_shutdown(state: dict) -> None:
    """Cancel this loop's pending idle close before starting Grok work."""
    pending = state["idle_task"]
    if pending is not None and not pending.done():
        pending.cancel()
    state["idle_task"] = None


async def _run_grok_query(query: str, timeout: int = 120,
                          progress: dict | None = None) -> dict:
    """Run one Grok browser query and retain direct X post URLs."""
    from playwright.async_api import async_playwright

    await _ensure_browser_ready("grok", _ensure_grok_browser)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(GROK_CDP_URL)
        page = await _new_browser_task_page(browser, "Grok")
        try:
            await page.goto("https://grok.com/", wait_until="domcontentloaded")
            editor = page.locator(
                '[role="textbox"][aria-label="Ask Grok anything"], textarea'
            ).first
            await editor.wait_for(state="visible", timeout=30000)
            await editor.click()
            await editor.fill(query)
            await editor.press("Enter")
            answer, done = await _wait_for_community_answer(
                page, query, timeout, progress, platform="x"
            )
            links = await page.locator("a[href]").evaluate_all(
                "els => els.map(a => ({text:(a.innerText||a.getAttribute('aria-label')||'').trim(), href:a.href}))"
            )
            return {
                "answer": answer,
                "references": _community_references(links, "x"),
                "partial": not done,
                "conversation_url": getattr(page, "url", ""),
            }
        finally:
            await page.close()


async def _grok_query(query: str, timeout: int = 120,
                      progress: dict | None = None) -> dict:
    """Query Grok while maintaining loop-local idle-shutdown lifecycle."""
    state = _grok_lifecycle_state()
    async with state["lock"]:
        _cancel_grok_idle_shutdown(state)
        state["active_requests"] += 1
    try:
        return await _run_grok_query(query, timeout, progress)
    finally:
        async with state["lock"]:
            state["active_requests"] = max(0, state["active_requests"] - 1)
            if state["active_requests"] == 0:
                _schedule_grok_idle_shutdown(state)


# ================================================================ 千问 / 阿里生态
QIANWEN_CDP_URL = "http://127.0.0.1:9557"
QIANWEN_CDP_PORT = 9557
QIANWEN_EDITOR_SELECTORS = (
    '[contenteditable="true"][data-placeholder]',
    '[contenteditable="true"][role="textbox"]',
)
_QIANWEN_PERSONAL_OWNER = re.compile(
    r"(?:我的|本人|自己的|(?<![你他她它您])我(?!们)|\bmy\b|\bmine\b)", re.IGNORECASE
)
_QIANWEN_SENSITIVE_DATA = re.compile(
    r"(?:支付宝|社保|社会保险|公积金|个税|个人所得税|所得税|纳税|税务|医保|医疗保险|"
    r"身份证|身份证件|护照|居住证|出生证明|出生记录|婚姻登记|结婚登记|户籍|户口|"
    r"车辆|机动车|车牌|驾驶证|驾照|不动产|房产|政务|政府|"
    r"alipay|social security|provident fund|income tax|tax records?|medical insurance|"
    r"identity cards?|passports?|residence permits?|birth records?|marriage records?|"
    r"vehicle records?|driver(?:'s)? licenses?|property records?|government records?)",
    re.IGNORECASE,
)
_QIANWEN_SENSITIVE_RECORD = re.compile(
    r"(?:账单|流水|记录|明细|账户|余额|缴费记录|登记信息|档案|数据|个人信息|号码|编号|"
    r"statements?|transactions?|records?|accounts?|balances?|data|details?|numbers?)",
    re.IGNORECASE,
)
_QIANWEN_PERSONAL_RETRIEVAL = re.compile(
    r"(?:查询|查(?:看)?|获取|显示|导出|提供|告诉|看看|多少|交了|缴了|缴纳|号码|编号|"
    r"账单|流水|记录|明细|账户|余额|档案|"
    r"\b(?:query|look\s+up|retrieve|show|export|get|tell|how\s+much)\b)",
    re.IGNORECASE,
)
_QIANWEN_RECORD_RETRIEVAL_ACTION = re.compile(
    r"(?:查询|查(?:看)?|获取|显示|导出|提供|告诉|看看|"
    r"\b(?:query|look\s+up|retrieve|show|export|get|tell)\b)",
    re.IGNORECASE,
)
_QIANWEN_PUBLIC_RECORD_RESEARCH = re.compile(
    r"(?:公开|开放|政策|法规|规则|制度|流程|说明|研究|比较|分析|"
    r"\b(?:public|open|polic(?:y|ies)|law|rules?|system|process|research|compare|analy[sz]e)\b)",
    re.IGNORECASE,
)
_QIANWEN_TRANSACTION_ACTION = re.compile(
    r"(?:下单|提交订单|创建订单|代购|购买|订购|买(?!家|方|手)|预订|预定|订票|订酒店|预约|"
    r"支付|付款|代付|结账|付钱|结算|转账|充值|退款|退钱|提现|兑换|领取|领券|核销|"
    r"绑定|解绑|改绑|开户|销户|开通账户|关闭账户|修改密码|发红包|收款|"
    r"(?:订(?!阅)|(?<!确)定)[^，。！？!?；;\n]{0,10}(?:酒店|机票|房间|客房|民宿|住宿)|"
    r"\b(?:place|submit|create)\s+(?:an?\s+)?order\b|"
    r"\b(?:buy|purchase|order|book|reserve|pay|transfer|recharge|refund|withdraw|redeem)\b|"
    r"\bmake\s+(?:a\s+)?reservation\b|\bcheck\s*out\b)",
    re.IGNORECASE,
)
_QIANWEN_TRANSACTION_INFORMATION = re.compile(
    r"(?:研究|比较|查询|了解|分析|调查|介绍|说明|哪里|哪儿|何处|如何|怎么|"
    r"价格|报价|规则|政策|流程|手续费|攻略|信息|渠道|方式|评价|区别|公开|"
    r"\b(?:research|compare|analy[sz]e|information|price|quote|policy|rule|fee|"
    r"process|procedure|guide|option|channel|method|how|where)\b)",
    re.IGNORECASE,
)
_QIANWEN_TRANSACTION_INFORMATION_LEAD = re.compile(
    r"^(?:研究|比较|查询|了解|分析|调查|介绍|说明|哪里|哪儿|何处|在哪里|如何|怎么)"
)
_QIANWEN_TRANSACTION_EXECUTION = re.compile(
    r"(?:(?:帮我|替我|为我|给我|请|现在|马上|立即|直接|我要|我想要)\s*"
    r"(?:去\s*)?(?:申请\s*|办理\s*)?(?:下单|购买|订购|买|预订|预定|订票|订酒店|预约|"
    r"支付|付款|代付|结账|付钱|结算|转账|充值|退款|退钱|提现|兑换|领取|领券|核销|"
    r"绑定|解绑|改绑|开户|销户|开通账户|关闭账户|修改密码|发红包|收款)|"
    r"(?:下单|购买|订购|买|预订|预定|订票|订酒店|预约|支付|付款|代付|结账|付钱|"
    r"结算|转账|充值|退款|退钱|提现|兑换|领取|领券|核销|绑定|解绑|改绑|开户|销户|"
    r"开通账户|关闭账户|修改密码|发红包|收款).{0,8}"
    r"(?:这件|这个|一件|一个|一间|两张|给|向|收款人|商家|用支付宝|支付宝)|"
    r"(?:用|使用)支付宝(?:支付|付款)?|"
    r"\b(?:buy|purchase|order|book|reserve|pay|transfer|recharge|refund|withdraw|redeem)\s+"
    r"(?:(?:me|for\s+me)\s+)?(?:a|an|the|this|that|one|two|\d+)\b)",
    re.IGNORECASE,
)
_QIANWEN_CLAUSE_BOUNDARY = re.compile(
    r"[，。！？!?；;\n]+|(?:然后|之后|后(?=帮我|替我|请|再|马上|立即|现在)|并且|"
    r"并(?=帮我|替我|请|购买|预订|支付|付款|转账)|"
    r"再(?=查询|查|购买|预订|支付|付款|转账)|\bthen\b|\band\s+then\b)",
    re.IGNORECASE,
)

QIANWEN_REFS_JS = """() => {
  const assistantSelectors = [
    '[class*="message-select-wrapper-answer"]',
    '[data-message-role="assistant"]',
    '[data-role="assistant"]',
    '[data-testid*="assistant"]',
    'article[class*="assistant"]',
    '[class*="assistant-message"]'
  ];
  const assistants = [];
  const seenAssistants = new Set();
  for (const selector of assistantSelectors) {
    for (const element of document.querySelectorAll(selector)) {
      if (!seenAssistants.has(element)) {
        seenAssistants.add(element);
        assistants.push(element);
      }
    }
  }
  assistants.sort((left, right) => {
    if (left === right) return 0;
    return left.compareDocumentPosition(right) & Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1;
  });
  const current = assistants.at(-1);
  if (!current) return [];

  const message = current.closest(
    '[data-message-id], [data-testid*="message"], article, [class*="message"]'
  ) || current;
  const citationRegionSelector = [
    '[data-testid*="citation"]', '[data-testid*="source"]',
    '[class*="citation"]', '[class*="source-card"]', '[class*="reference"]'
  ].join(',');
  const messageBoundarySelector = [
    '[data-message-role]', '[data-role="assistant"]', '[data-role="user"]',
    '[data-message-id]', '[data-testid*="message"]', 'article[class*="assistant"]',
    '[class*="assistant-message"]', '[class*="user-message"]'
  ].join(',');
  const scopeRoots = [message];
  let sibling = message.nextElementSibling;
  while (sibling && !sibling.matches(messageBoundarySelector)) {
    if (sibling.matches(citationRegionSelector)) scopeRoots.push(sibling);
    for (const region of sibling.querySelectorAll(citationRegionSelector)) {
      scopeRoots.push(region);
    }
    sibling = sibling.nextElementSibling;
  }

  const interfaceSelector = [
    'nav', 'header', 'footer', '[role="navigation"]',
    '[class*="action"]', '[class*="toolbar"]', '[class*="share"]'
  ].join(',');
  const anchors = [];
  const seenAnchors = new Set();
  for (const root of scopeRoots) {
    for (const anchor of root.querySelectorAll('a[href]')) {
      if (!seenAnchors.has(anchor)) {
        seenAnchors.add(anchor);
        anchors.push(anchor);
      }
    }
  }
  return anchors
    .filter(anchor => !anchor.closest(interfaceSelector))
    .map(anchor => ({
      title: (anchor.innerText || anchor.getAttribute('aria-label') || '')
        .trim().replace(/\\s+/g, ' ').slice(0, 160),
      href: anchor.href
    }))
    .filter(item => item.title && item.href)
    .slice(0, 60);
}"""


def _guard_qianwen_read_only(intent: str) -> None:
    """Reject personal-data and executable transaction intent before browser work."""
    clauses = [
        clause.strip()
        for clause in _QIANWEN_CLAUSE_BOUNDARY.split(intent.strip())
        if clause.strip()
    ]
    for clause in clauses:
        personal_owner = _QIANWEN_PERSONAL_OWNER.search(clause)
        personal_retrieval = _QIANWEN_PERSONAL_RETRIEVAL.search(clause)
        sensitive_record = _QIANWEN_SENSITIVE_RECORD.search(clause)
        if personal_owner and personal_retrieval and sensitive_record:
            raise ValueError("千问仅支持公开信息只读搜索")

        sensitive_data = _QIANWEN_SENSITIVE_DATA.search(clause)
        if sensitive_data:
            record_retrieval = (
                sensitive_record
                and _QIANWEN_RECORD_RETRIEVAL_ACTION.search(clause)
            )
            if record_retrieval:
                raise ValueError("千问仅支持公开信息只读搜索")
            public_research = _QIANWEN_PUBLIC_RECORD_RESEARCH.search(clause)
            private_retrieval = personal_owner and personal_retrieval
            private_record = sensitive_record and not public_research
            if private_retrieval or private_record:
                raise ValueError("千问仅支持公开信息只读搜索")

        if _QIANWEN_TRANSACTION_ACTION.search(clause):
            execution = _QIANWEN_TRANSACTION_EXECUTION.search(clause)
            informational_lead = _QIANWEN_TRANSACTION_INFORMATION_LEAD.search(clause)
            if execution and not informational_lead:
                raise ValueError("千问仅支持公开信息只读搜索")
            if not _QIANWEN_TRANSACTION_INFORMATION.search(clause):
                raise ValueError("千问仅支持公开信息只读搜索")


async def _select_qianwen_editor(page, timeout_ms: int = 30000):
    """Probe editor selectors in priority order and return a usable candidate."""
    deadline = time.monotonic() + max(0, timeout_ms) / 1000
    while True:
        for selector in QIANWEN_EDITOR_SELECTORS:
            candidates = page.locator(selector)
            try:
                count = await candidates.count()
            except Exception:
                continue
            for index in range(count):
                candidate = candidates.nth(index)
                try:
                    if await candidate.is_visible() and await candidate.is_enabled():
                        return candidate
                except Exception:
                    continue
        if time.monotonic() >= deadline:
            raise RuntimeError("千问编辑器不可用")
        await page.wait_for_timeout(min(100, max(1, timeout_ms)))


def _normalize_qianwen_url(raw_url: str) -> str:
    """Normalize an external Qianwen citation URL for stable deduplication."""
    try:
        parsed = urlsplit(raw_url.strip())
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower().rstrip(".") if parsed.hostname else ""
        port = parsed.port
    except (AttributeError, ValueError):
        return ""
    if scheme not in {"http", "https"} or not host:
        return ""
    if host == "qianwen.com" or host.endswith(".qianwen.com"):
        return ""
    netloc = host
    if port and not (
        (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    tracking = _COMMUNITY_TRACKING_PARAMETERS | {
        "from", "source", "spm", "scm", "share_token", "sharetoken"
    }
    query = urlencode(
        [
            (name, value)
            for name, value in parse_qsl(parsed.query, keep_blank_values=True)
            if name.lower() not in tracking
        ],
        doseq=True,
    )
    return urlunsplit((scheme, netloc, parsed.path or "/", query, ""))


async def _extract_qianwen_references(page) -> list[dict]:
    """Extract normalized references only from the latest assistant message."""
    items = await page.evaluate(QIANWEN_REFS_JS)
    references, seen = [], set()
    if not isinstance(items, list):
        return references
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("href") or item.get("url")
        if not isinstance(raw_url, str):
            continue
        url = _normalize_qianwen_url(raw_url)
        if not url or url in seen:
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        seen.add(url)
        references.append({
            "title": title[:160],
            "url": url,
            "publisher": urlsplit(url).hostname.removeprefix("www."),
        })
        if len(references) >= 30:
            break
    return references


def _ensure_qianwen_browser() -> None:
    """Start the isolated Qianwen runtime without exposing private state details."""
    if _cdp_alive(QIANWEN_CDP_URL):
        return
    profile = _qianwen_profile_path()
    if not profile.exists():
        raise RuntimeError("千问登录配置缺失")
    chrome = _chrome_executable()
    if not Path(chrome).exists():
        raise RuntimeError("Chrome 可执行文件缺失")
    try:
        subprocess.Popen(
            [
                chrome,
                f"--remote-debugging-port={QIANWEN_CDP_PORT}",
                "--remote-debugging-address=127.0.0.1",
                f"--user-data-dir={profile}",
                "--profile-directory=Default",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "https://www.qianwen.com/",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        raise RuntimeError("千问浏览器启动失败") from None
    for _ in range(30):
        time.sleep(1)
        if _cdp_alive(QIANWEN_CDP_URL):
            return
    raise RuntimeError("千问浏览器启动超时")


async def _qianwen_response_text(page, query: str) -> str:
    answers = page.locator('[class*="message-select-wrapper-answer"]')
    try:
        count = await answers.count()
        if count:
            text = (await answers.nth(count - 1).inner_text()).strip()
            if text:
                return text
    except Exception:
        pass
    body = (await page.locator("body").inner_text()).strip()
    return body.rsplit(query, 1)[-1].strip() if query in body else ""


async def _qianwen_query(
    query: str,
    timeout: int = 90,
    progress: dict | None = None,
    *,
    policy_intent: str | None = None,
) -> dict:
    """Search public Alibaba ecosystem information through Qianwen, read-only."""
    _guard_qianwen_read_only(query if policy_intent is None else policy_intent)
    from playwright.async_api import async_playwright

    await _ensure_browser_ready("qianwen", _ensure_qianwen_browser)
    async with async_playwright() as pw:
        browser = await pw.chromium.connect_over_cdp(QIANWEN_CDP_URL)
        page = await _new_browser_task_page(browser, "千问")
        try:
            await page.goto("https://www.qianwen.com/", wait_until="commit")
            editor = await _select_qianwen_editor(page)
            await editor.click()
            await editor.fill(query)
            keyboard = getattr(page, "keyboard", None)
            if keyboard is None:
                await editor.press("Enter")
            else:
                await keyboard.press("Enter")

            last, stable = "", 0
            done = False
            for poll in range(max(int(timeout), 0)):
                await page.wait_for_timeout(1000)
                try:
                    current = await _qianwen_response_text(page, query)
                except Exception:
                    continue
                current = strip_platform_stream_status(current)
                substantive = _is_substantive_stream_answer(current, ())
                if progress is not None and substantive:
                    progress["answer"] = current
                if progress is not None and (poll + 1) % 5 == 0:
                    try:
                        progress["references"] = await _extract_qianwen_references(page)
                    except Exception:
                        pass
                if substantive and current == last:
                    stable += 1
                    if stable >= 7:
                        done = True
                        break
                else:
                    stable = 0
                last = current

            try:
                references = await _extract_qianwen_references(page)
            except Exception:
                references = []
            return {
                "answer": last,
                "references": references,
                "partial": not done,
                "conversation_url": getattr(page, "url", ""),
            }
        finally:
            await page.close()


# ================================================================ MCP 工具
# ---- 输出格式化层：清洗 / 截断（不动搜索逻辑）----
_NOISE_EXACT = {
    "深度思考", "全球搜", "海外官网", "对话支持收藏啦", "Download the desktop",
    "阅读排行榜", "科学探索", "官方微博", "APP", "打开APP", "新浪财经APP",
    "缩小字体", "放大字体", "收藏", "微信", "分享",
    "联网搜索", "搜索全网", "全网搜索", "深度搜索",
    "打开百度APP", "APP内打开", "下载APP", "查看全部",
    "展开全部", "收起", "复制",
}
_NOISE_PATTERNS = [
    re.compile(r"^搜索全球\s*\d+\s*篇资料$"),
    re.compile(r"^共参考\s*\d+\s*篇资料$"),
    re.compile(r"^打开百度APP"),
    re.compile(r"APP内打开"),
    re.compile(r"^下载.*APP$"),
    re.compile(r"^搜索全球?\s*\d+\s*篇资料$"),
    re.compile(r"^搜索\s*\d+\s*个关键词.*$"),
    re.compile(r"^百度APP$"),
    re.compile(r"^扫码下载.*$"),
]


def _clean_text(text: str) -> str:
    """按行清洗网页抽取文本：UI 噪音整行剔除（整行精确匹配，不伤正文）、
    纯符号行剔除、连续重复行去重、连续空行压成单个、去首尾空行。"""
    out, prev = [], None
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            if out and out[-1] != "":
                out.append("")
            prev = None
            continue
        if line in _NOISE_EXACT:
            continue
        if any(p.search(line) for p in _NOISE_PATTERNS):
            continue
        if not re.search(r"[0-9A-Za-z\u4e00-\u9fff]", line):
            continue  # 纯符号行
        if line == prev:
            continue  # 连续重复行
        out.append(line)
        prev = line
    return "\n".join(out).strip()


def _truncate(text: str, limit: int) -> str:
    """智能截断：优先在换行/句号处切断（位置需超过限量 60%，否则硬切），
    截断后标注原文真实长度。"""
    if len(text) <= limit:
        return text
    cut = max(text.rfind(sep, 0, limit + 1) for sep in ("\n", "。"))
    if cut < limit * 0.6:
        cut = limit
    return text[:cut].rstrip() + f"…（已截断，原文共 {len(text)} 字符）"


def _preserve_provider_answer(
    source_id: str,
    raw_answer: object,
    display_limit: int,
) -> dict:
    clean = _clean_text(_text(raw_answer))
    display_limit = max(0, display_limit)
    preserved_limit = PROVIDER_PRESERVED_ANSWER_LIMITS[source_id]
    return {
        "answer": _truncate(clean, display_limit),
        "answer_original_chars": len(clean),
        "answer_truncated": len(clean) > display_limit,
        "preserved_answer": clean[:preserved_limit],
        "preserved_answer_chars": min(len(clean), preserved_limit),
        "preserved_answer_limit": preserved_limit,
        "preserved_answer_truncated": len(clean) > preserved_limit,
    }


def _fmt_ai_source(name: str, r: dict,
                   max_answer: int = 3000, max_refs: int = 15) -> str:
    answer = _truncate(_clean_text(str(r.get("answer") or "")), max_answer)
    out = f"【{name} AI 整合回答】（模型生成，事实以来源为准）\n{answer}"
    refs = r.get("references") or []
    seen, lines = set(), []
    for x in refs:
        if isinstance(x, dict):
            url = x.get("url") if isinstance(x.get("url"), str) else ""
            title = _clean_text(str(x.get("title") or "")).replace("\n", " ")[:100]
            if not title:
                continue
            key = url or f"unresolved:{title}"
            if key in seen:
                continue
            seen.add(key)
            if url:
                lines.append(f"{len(lines)+1}. [{title}]({url})")
            else:
                lines.append(f"{len(lines)+1}. 待解析来源：{title}")
        else:
            s = _clean_text(str(x)).replace("\n", " ")
            if not s or s in seen:
                continue
            seen.add(s)
            lines.append(f"{len(lines)+1}. 待解析来源：{s}")
        if len(lines) >= max_refs:
            break
    if lines:
        omitted = len(refs) - len(lines)
        tag = f"【{name}搜索来源 {len(lines)} 条"
        if omitted > 0:
            tag += f"，另 {omitted} 条略"
        out += f"\n\n{tag}】\n" + "\n".join(lines)
    return out


def _fmt_community_source(name: str, r: dict,
                          max_answer: int = 5000, max_refs: int = 30) -> str:
    """Render provider prose explicitly as platform-observed, never verified fact."""
    rendered = _fmt_ai_source(name, r, max_answer=max_answer, max_refs=max_refs)
    heading = f"【{name} AI 整合回答】"
    return rendered.replace(
        heading,
        f"【[PO] {name} 平台观察】",
        1,
    )


def _fmt_tavily(r: dict) -> str:
    lines = []
    if r["answer"]:
        lines.append(f"摘要: {r['answer']}\n")
    for i, x in enumerate(r["results"], 1):
        content = _truncate(_clean_text(x["content"]), 300)
        lines.append(f"{i}. [{x['title']}]({x['url']})\n   {content}")
    return "\n".join(lines) or "无结果"


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _is_substantive_stream_answer(answer: str, status_hints: tuple[str, ...]) -> bool:
    cleaned = strip_platform_stream_status(answer, status_hints)
    return bool(cleaned) and (
        len(cleaned) >= 30 or has_platform_observation_signal(cleaned)
    )


def _reference_list(value: object) -> list[dict | str]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, (dict, str))]
    if isinstance(value, (dict, str)):
        return [value]
    return []


def _adapt_provider_result(source_id: str, value: object) -> dict:
    """Keep provider output raw enough for evidence normalization, with a state."""
    raw = value if isinstance(value, dict) else {}
    if source_id == "exa" and not isinstance(value, dict):
        answer = _text(value)
        references: list[dict | str] = []
    elif source_id == "tavily":
        answer = _text(raw.get("answer"))
        references = _reference_list(raw.get("results", raw.get("references", [])))
    else:
        answer = _text(raw.get("answer"))
        references = _reference_list(raw.get("references", raw.get("results", [])))

    supplied_status = raw.get("status") if isinstance(raw.get("status"), str) else ""
    supplied_status = supplied_status.lower()
    partial = bool(raw.get("partial")) or supplied_status == "partial"
    if partial:
        status = "partial"
    elif supplied_status in {"failed", "timeout"}:
        status = supplied_status
    else:
        status = "complete"
    return {
        "status": status,
        "partial": partial,
        "answer": answer,
        "references": references,
    }


def _failed_provider_result() -> dict:
    return {"status": "failed", "partial": False, "answer": "", "references": []}


def _timeout_provider_result(progress: dict) -> dict:
    answer = strip_platform_stream_status(_text(progress.get("answer")))
    references = _reference_list(progress.get("references", []))
    if _clean_text(answer) or references:
        return {
            "status": "partial",
            "partial": True,
            "answer": answer,
            "references": references,
        }
    return {"status": "timeout", "partial": False, "answer": "", "references": []}


def _provider_budget(round_timeout: float) -> float:
    timeout = max(0.0, float(round_timeout))
    cleanup = min(8.0, timeout * 0.2)
    return max(0.0, min(82.0, timeout - cleanup))


_PROVIDER_CANCELLATION_CLEANUP_TIMEOUT = 8.0


def _consume_provider_task_exception(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        task.exception()
    except BaseException:
        pass


async def _cancel_and_reap_provider_tasks(tasks: list[asyncio.Task]) -> None:
    unfinished = [task for task in tasks if not task.done()]
    for task in unfinished:
        if task.cancelling() == 0:
            task.cancel()

    if unfinished:
        await asyncio.wait(
            unfinished,
            timeout=_PROVIDER_CANCELLATION_CLEANUP_TIMEOUT,
        )

    for task in tasks:
        if task.done():
            _consume_provider_task_exception(task)
        else:
            task.add_done_callback(_consume_provider_task_exception)


async def _finish_provider_cancellation(tasks: list[asyncio.Task]) -> None:
    """Shield provider cleanup even if the caller repeats cancellation."""
    cleanup = asyncio.create_task(_cancel_and_reap_provider_tasks(tasks))
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
    cleanup.result()


async def _collect_provider_results(
    queries: dict[str, str],
    timeout: float,
    original_query: str,
    lane_name: str = "single",
) -> dict:
    """Run all registered adapters once within one absolute round deadline."""
    started = time.monotonic()
    provider_budget = _provider_budget(timeout)
    progress = {
        source_id: {}
        for source_id in provider_ids()
        if next(
            spec for spec in PROVIDER_SPECS if spec.source_id == source_id
        ).progress_enabled
    }

    async def run_doubao() -> dict:
        async with _browser_provider_slot("doubao"):
            return await _doubao_query(
                queries["doubao"],
                timeout=provider_budget,
                progress=progress["doubao"],
            )

    async def run_yuanbao() -> dict:
        async with _browser_provider_slot("yuanbao"):
            return await _yuanbao_query(
                queries["yuanbao"],
                timeout=provider_budget,
                progress=progress["yuanbao"],
            )

    async def run_wenxin() -> dict:
        async with _browser_provider_slot("wenxin"):
            return await _wenxin_query(
                queries["wenxin"],
                timeout=provider_budget,
                progress=progress["wenxin"],
            )

    async def run_tavily() -> dict:
        return await _tavily_query_async(queries["tavily"])

    async def run_exa() -> str:
        return await _exa_query_async(queries["exa"], timeout=provider_budget)

    async def run_gemini() -> dict:
        async with _browser_provider_slot("gemini"):
            return await _gemini_query(
                queries["gemini"],
                timeout=provider_budget,
                progress=progress["gemini"],
            )

    async def run_grok() -> dict:
        async with _browser_provider_slot("grok"):
            return await _grok_query(
                queries["grok"],
                timeout=provider_budget,
                progress=progress["grok"],
            )

    async def run_qianwen() -> dict:
        async with _browser_provider_slot("qianwen"):
            return await _qianwen_query(
                queries["qianwen"],
                timeout=provider_budget,
                progress=progress["qianwen"],
                policy_intent=original_query,
            )

    runners = {
        "doubao": run_doubao,
        "yuanbao": run_yuanbao,
        "wenxin": run_wenxin,
        "tavily": run_tavily,
        "exa": run_exa,
        "gemini": run_gemini,
        "grok": run_grok,
        "qianwen": run_qianwen,
    }
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, float(timeout))
    work_deadline = min(deadline, loop.time() + provider_budget)
    tasks = {
        source_id: asyncio.create_task(
            runner(), name=f"multi-search:{source_id}:{lane_name}"
        )
        for source_id, runner in runners.items()
    }
    task_list = list(tasks.values())
    try:
        await asyncio.wait(
            task_list,
            timeout=max(0.0, work_deadline - loop.time()),
        )
    except asyncio.CancelledError:
        await _finish_provider_cancellation(task_list)
        raise

    pending = [task for task in task_list if not task.done()]
    timed_out = set(pending)
    for task in pending:
        task.cancel()
    cleanup_remaining = max(0.0, deadline - loop.time())
    try:
        if pending and cleanup_remaining:
            await asyncio.wait(pending, timeout=cleanup_remaining)
    except asyncio.CancelledError:
        await _finish_provider_cancellation(task_list)
        raise
    for task in pending:
        if task.done():
            _consume_provider_task_exception(task)
        else:
            task.add_done_callback(_consume_provider_task_exception)

    records: dict[str, dict] = {}
    for source_id in provider_ids():
        task = tasks[source_id]
        if task in timed_out:
            records[source_id] = _timeout_provider_result(progress.get(source_id, {}))
        elif task.cancelled():
            records[source_id] = _failed_provider_result()
        else:
            try:
                records[source_id] = _adapt_provider_result(source_id, task.result())
            except Exception:
                # Do not expose provider exceptions because they can contain runtime details.
                records[source_id] = _failed_provider_result()

    return {
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "results": records,
    }


def _merge_provider_lane_results(general: dict, specialized: dict) -> dict:
    """Merge two normalized provider lanes without treating either as verified fact."""
    answers = []
    for label, result in (("general", general), ("specialized", specialized)):
        answer = _text(result.get("answer")).strip()
        if answer:
            answers.append(f"【{label}】\n{answer}")

    references: list[dict | str] = []
    seen: dict[tuple, int] = {}
    for lane, result in (("general", general), ("specialized", specialized)):
        lane_status = _text(result.get("status")) or "failed"
        eligible = lane_status == "complete"
        for reference in _reference_list(result.get("references", [])):
            if isinstance(reference, dict):
                candidate = {
                    **reference,
                    "lane": lane,
                    "source_status": lane_status,
                    "eligible_for_corroboration": eligible,
                }
                key = (
                    _text(reference.get("url")).strip(),
                    _text(reference.get("title")).strip(),
                    _text(reference.get("publisher")).strip(),
                )
            else:
                candidate = {
                    "title": reference,
                    "url": reference if reference.startswith(("http://", "https://")) else "",
                    "lane": lane,
                    "source_status": lane_status,
                    "eligible_for_corroboration": eligible,
                }
                key = (reference.strip(),)
            existing_index = seen.get(key)
            if existing_index is None:
                seen[key] = len(references)
                references.append(candidate)
            elif eligible and not bool(
                references[existing_index].get("eligible_for_corroboration")
            ):
                references[existing_index] = candidate

    statuses = (general.get("status"), specialized.get("status"))
    if statuses == ("complete", "complete"):
        status = "complete"
    elif answers or references:
        status = "partial"
    elif statuses == ("timeout", "timeout"):
        status = "timeout"
    else:
        status = "failed"
    return {
        "status": status,
        "partial": status == "partial",
        "answer": "\n\n".join(answers),
        "references": references,
        "lane_status": {
            "general": general.get("status", "failed"),
            "specialized": specialized.get("status", "failed"),
        },
    }


async def _collect_provider_lanes(
    lanes: dict[str, dict[str, str]],
    timeout: float,
    original_query: str,
) -> dict:
    """Run general and specialized provider lanes concurrently, then merge them."""
    started = time.monotonic()
    general_queries = {
        source_id: provider_lanes["general"]
        for source_id, provider_lanes in lanes.items()
    }
    specialized_queries = {
        source_id: provider_lanes["specialized"]
        for source_id, provider_lanes in lanes.items()
    }
    general_task = asyncio.create_task(
        _collect_provider_results(
            general_queries, timeout, original_query, lane_name="general"
        ),
        name="multi-search-lane:general",
    )
    specialized_task = asyncio.create_task(
        _collect_provider_results(
            specialized_queries, timeout, original_query, lane_name="specialized"
        ),
        name="multi-search-lane:specialized",
    )
    general, specialized = await asyncio.gather(general_task, specialized_task)
    merged = {
        source_id: _merge_provider_lane_results(
            general["results"][source_id],
            specialized["results"][source_id],
        )
        for source_id in provider_ids()
    }
    return {
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "results": merged,
    }


def _citation_sort_key(citation: dict, providers: dict[str, dict]) -> tuple:
    provider = providers.get(citation.get("provider"), {})
    priority = provider.get("retention_priority", 0)
    if not isinstance(priority, int) or isinstance(priority, bool):
        priority = 0
    source_order = citation.get("source_order", 0)
    if not isinstance(source_order, int) or isinstance(source_order, bool):
        source_order = 0
    preserved_direct = bool(provider.get("preserve_references")) and bool(
        citation.get("is_direct_url")
    )
    return (
        0 if preserved_direct else 1,
        -priority,
        0 if citation.get("is_direct_url") else 1,
        0 if citation.get("is_resolved") else 1,
        source_order,
    )


def _ordered_citations(citations: list[dict], providers: dict[str, dict]) -> list[dict]:
    return sorted(citations, key=lambda citation: _citation_sort_key(citation, providers))


def _verification_queue(research: dict) -> list[dict]:
    """List evidence gaps without evaluating factual truth or consensus."""
    queue: list[dict] = []
    providers = research.get("providers", {})
    for source_id, provider in providers.items():
        citations = provider.get("citations", [])
        for citation in sorted(
            (item for item in citations if isinstance(item, dict)),
            key=lambda item: item.get("source_order", 0),
        ):
            if not citation.get("is_resolved"):
                queue.append(
                    {
                        "kind": "unresolved_citation",
                        "source_id": source_id,
                        "title": _text(citation.get("title")),
                        "url": citation.get("url"),
                    }
                )
    for source_id, provider in providers.items():
        status = provider.get("status")
        if status in {"partial", "failed", "timeout"}:
            queue.append(
                {
                    "kind": "provider_gap",
                    "source_id": source_id,
                    "status": status,
                }
            )
    return queue


async def _run_research_round(
    query: str,
    profile: str = "general",
    research_brief: dict | None = None,
    max_answer: int = 2200,
    max_refs: int = 20,
    timeout: int = 130,
) -> dict:
    """Build one structured, source-attributed research round from dual lanes."""
    if research_brief is None:
        brief = build_research_brief(query, depth="deep")
    else:
        supplied = dict(research_brief)
        supplied.setdefault("query", query)
        brief = normalize_research_brief(supplied, profile)
    lanes = build_provider_query_lanes(brief, provider_ids())
    collected = await _collect_provider_lanes(lanes, timeout, query)
    raw_results = collected["results"]
    research = build_research_round(
        query=query,
        profile_name=profile,
        profiles=load_profiles(EVIDENCE_PROFILE_FILE),
        elapsed_ms=collected["elapsed_ms"],
        results=raw_results,
        research_brief=brief,
    )

    answer_limit = max(0, max_answer)
    for source_id, provider in research["providers"].items():
        raw = raw_results.get(source_id, {})
        provider_answer_limit = 5000 if source_id == "exa" else answer_limit
        provider.update(
            _preserve_provider_answer(
                source_id,
                raw.get("answer"),
                provider_answer_limit,
            )
        )
        provider["truncated"] = provider["answer_truncated"]
        provider["source_status"] = provider["status"]
        provider["lane_status"] = dict(raw.get("lane_status", {}))

    research["query_plan"] = {
        "mode": "dual_lane",
        "lanes": ["general", "specialized"],
        "depth": brief["depth"],
        "required_dimensions": list(brief["required_dimensions"]),
        "optional_dimensions": list(brief["optional_dimensions"][:8]),
    }

    reference_limit = max(0, max_refs)
    ordered_references = _ordered_citations(
        research["unique_citations"], research["providers"]
    )
    references_truncated = len(ordered_references) > reference_limit
    # Keep the complete ordered ledger canonical. ``max_refs`` limits only the
    # explicit display view and the backward-compatible Markdown renderer.
    research["unique_citations"] = ordered_references
    research["display_citations"] = ordered_references[:reference_limit]
    research["display_reference_limit"] = reference_limit
    research["references_truncated"] = references_truncated
    research["truncated"] = references_truncated or any(
        provider["truncated"] for provider in research["providers"].values()
    )
    research["verification_queue"] = _verification_queue(research)
    research["evidence_digest"] = build_evidence_digest(research)
    return research


def _legacy_reference_lines(
    provider: dict,
    providers: dict[str, dict],
    max_refs: int,
    include_snippets: bool = False,
) -> tuple[list[str], int]:
    citations = [
        citation
        for citation in provider.get("citations", [])
        if isinstance(citation, dict)
    ]
    deduplicated: list[dict] = []
    seen = set()
    for citation in _ordered_citations(citations, providers):
        if citation.get("is_resolved"):
            dedupe_key = ("resolved", citation.get("dedupe_key"))
        else:
            dedupe_key = (
                "unresolved",
                _text(citation.get("title")).strip(),
                _text(citation.get("publisher")).strip(),
            )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        deduplicated.append(citation)

    selected = deduplicated[:max(0, max_refs)]
    lines: list[str] = []
    reference_count = 0
    for citation in selected:
        title = _clean_text(_text(citation.get("title"))).replace("\n", " ")[:100]
        if not title:
            continue
        reference_count += 1
        url = citation.get("url")
        if citation.get("is_resolved") and isinstance(url, str) and url:
            lines.append(f"{reference_count}. [{title}]({url})")
        else:
            lines.append(f"{reference_count}. 待解析来源：{title}")
        if include_snippets:
            snippet = _truncate(_clean_text(_text(citation.get("snippet"))), 300)
            if snippet:
                lines.append(f"   {snippet}")
    return lines, reference_count


def _render_legacy_provider(
    source_id: str,
    provider: dict,
    providers: dict[str, dict],
    max_refs: int,
) -> str:
    name = _PROVIDER_NAMES[source_id]
    status = provider.get("source_status", provider.get("status"))
    if status == "timeout":
        return "❌ 超时未返回有效内容"
    if status == "failed":
        return "❌ 请求失败（请使用单源工具重试）"

    answer = _text(provider.get("answer"))
    lines, reference_count = _legacy_reference_lines(
        provider,
        providers,
        max_refs,
        include_snippets=source_id == "tavily",
    )
    if source_id in {"gemini", "grok"}:
        body = f"【[PO] {name} 平台观察】（模型生成，事实以直接来源为准）\n{answer or '无结果'}"
    elif source_id in {"doubao", "yuanbao", "wenxin", "qianwen"}:
        body = f"【{name} AI 整合回答】（模型生成，事实以来源为准）\n{answer or '无结果'}"
    elif source_id == "tavily":
        body = "【Tavily 搜索结果】"
        if answer:
            body += f"\n摘要（模型生成，事实以来源为准）：{answer}"
        if not lines:
            body += "\n无结果"
    else:
        body = "【Exa 语义搜索结果】（模型生成或检索摘要，事实以来源为准）"
        body += f"\n{answer or '无结果'}"

    if lines:
        body += f"\n\n【{name}搜索来源 {reference_count} 条】\n" + "\n".join(lines)
    if status == "partial":
        body = f"⏳ {name}回答仍在生成中，以下为当前部分内容（非失败，可重试获取完整版）\n\n{body}"
    return body


def _render_legacy_search_all(research: dict, max_refs: int) -> str:
    providers = research.get("providers", {})
    source_ids = provider_ids()
    ok, fail, blocks = [], [], []
    for source_id in source_ids:
        name = _PROVIDER_NAMES[source_id]
        provider = providers.get(source_id)
        if not isinstance(provider, dict):
            body = "❌ 未收集结果"
        else:
            body = _render_legacy_provider(source_id, provider, providers, max_refs)
        (fail if body.startswith("❌") else ok).append(name)
        blocks.append(f"{'=' * 22} {name} {'=' * 22}\n{body}")

    elapsed_seconds = research.get("elapsed_ms", 0)
    elapsed_seconds = elapsed_seconds // 1000 if isinstance(elapsed_seconds, int) else 0
    header = f"📊 多源搜索完成 ｜ ✅ 成功 {len(ok)}/{len(source_ids)}"
    if ok:
        header += f": {'、'.join(ok)}"
    if fail:
        header += f" ｜ ❌ 失败 {len(fail)}: {'、'.join(fail)}"
    header += f" ｜ 耗时 {elapsed_seconds}s"
    return header + "\n\n" + "\n\n".join(blocks)


_SEARCH_SERVICE = SearchService(sys.modules[__name__])


@mcp.tool()
async def doubao_search(query: str, timeout: int = 90) -> str:
    """豆包联网搜索（桌面版 CDP 驱动）。抖音/头条生态数据强，中文生活消费类首选。
    返回 AI 整合回答 + 真实来源链接。"""
    async with _browser_provider_slot("doubao"):
        try:
            r = await _doubao_query(query, timeout)
            if not r["answer"]:
                return "豆包未返回内容（可能需人工介入）。"
            out = _fmt_ai_source("豆包", r)
            if r.get("partial"):
                out = "⏳ 豆包回答仍在生成中，以下为当前已抓取的部分内容（非失败，可重试获取完整版）\n\n" + out
            return out
        except Exception:
            return "豆包搜索失败（请稍后重试）。"


@mcp.tool()
async def tavily_search(query: str, max_results: int = 8) -> str:
    """Tavily 实时网页搜索（5 key 轮换）。英文/全球网页覆盖好，返回结构化结果。"""
    try:
        r = await _tavily_query_async(query, max_results)
        return _fmt_tavily(r)
    except Exception:
        return "Tavily 搜索失败（请稍后重试）。"


@mcp.tool()
async def yuanbao_search(query: str, timeout: int = 90) -> str:
    """腾讯元宝网页版搜索（微信登录态 + Search 智能体）。
    核心优势：微信公众号/视频号内容源，腾讯生态独家数据。
    返回 AI 整合回答 + 参考来源（含公众号文章）。"""
    async with _browser_provider_slot("yuanbao"):
        try:
            r = await _yuanbao_query(query, timeout)
            if not r["answer"]:
                return "元宝未返回内容。"
            out = _fmt_ai_source("元宝", r)
            if r.get("partial"):
                out = "⏳ 元宝回答仍在生成中，以下为当前已抓取的部分内容（非失败，可重试获取完整版）\n\n" + out
            return out
        except Exception:
            return "元宝搜索失败（请稍后重试）。"


@mcp.tool()
async def exa_search(query: str, num_results: int = 8) -> str:
    """Exa AI 语义搜索（Agent-Reach 渠道，免 key）。语义理解强，适合找官方/权威页面。"""
    try:
        out = await _exa_query_async(query, num_results)
        return "【Exa 语义搜索结果】\n" + _truncate(_clean_text(out), 5000)
    except Exception:
        return "Exa 搜索失败（请稍后重试）。"


@mcp.tool()
async def wenxin_search(query: str, timeout: int = 90) -> str:
    """百度文心助手搜索（匿名免登录）。百度生态数据，中文网页覆盖全。
    返回 AI 整合回答 + 参考来源列表。"""
    async with _browser_provider_slot("wenxin"):
        try:
            r = await _wenxin_query(query, timeout)
            if not r["answer"]:
                return "文心未返回内容。"
            out = _fmt_ai_source("文心", r)
            if r.get("partial"):
                out = "⏳ 文心回答仍在生成中，以下为当前已抓取的部分内容（非失败，可重试获取完整版）\n\n" + out
            return out
        except Exception:
            return "文心搜索失败（请稍后重试）。"


@mcp.tool()
async def gemini_search(query: str, timeout: int = 120) -> str:
    """Gemini 社区定向搜索。优先用于 YouTube 视频发现，返回 Gemini 观察和直接视频链接。
    Gemini 生成文本属于平台观察，不能自动作为已核验事实。"""
    async with _browser_provider_slot("gemini"):
        try:
            r = await _gemini_query(query, timeout)
            if not r["answer"]:
                return "Gemini 未返回内容。"
            out = _fmt_community_source("Gemini / YouTube", r)
            if r.get("partial"):
                out = "⏳ Gemini 回答仍在生成中，以下为当前部分内容（非失败，可重试获取完整版）\n\n" + out
            return out
        except Exception:
            return "Gemini 搜索失败（请检查隔离登录态后重试）。"


@mcp.tool()
async def grok_search(query: str, timeout: int = 120) -> str:
    """Grok 社区定向搜索。优先用于 X/Twitter 原帖发现，返回 Grok 观察和直接原帖链接。
    Grok 生成文本属于平台观察，不能自动作为已核验事实。"""
    async with _browser_provider_slot("grok"):
        try:
            r = await _grok_query(query, timeout)
            if not r["answer"]:
                return "Grok 未返回内容。"
            out = _fmt_community_source("Grok / X", r)
            if r.get("partial"):
                out = "⏳ Grok 回答仍在生成中，以下为当前部分内容（非失败，可重试获取完整版）\n\n" + out
            return out
        except Exception:
            return "Grok 搜索失败（请检查隔离登录态或安全验证后重试）。"


@mcp.tool()
async def qianwen_search(query: str, timeout: int = 90) -> str:
    """Search public Alibaba ecosystem information through Qianwen; read-only."""
    async with _browser_provider_slot("qianwen"):
        try:
            result = await _qianwen_query(query, timeout)
            if not result["answer"]:
                return "千问未返回内容。"
            output = _fmt_ai_source("千问 / 阿里生态", result)
            if result.get("partial"):
                output = (
                    "⏳ 千问回答仍在生成中，以下为当前部分内容（非失败，可重试获取完整版）\n\n"
                    + output
                )
            return output
        except Exception:
            return "千问搜索失败（请检查隔离登录配置后重试）。"


@mcp.tool()
async def research_round(
    query: str,
    profile: str = "general",
    research_brief: dict | None = None,
    max_answer: int = 2200,
    max_refs: int = 30,
    timeout: int = 90,
    mode: str = "balanced",
) -> dict:
    """Return source-attributed evidence gaps and citations from one eight-source round."""
    return await _SEARCH_SERVICE.research_round(
        query=query,
        profile=profile,
        research_brief=research_brief,
        max_answer=max_answer,
        max_refs=max_refs,
        timeout=timeout,
        mode=mode,
    )


@mcp.tool()
async def search_all(
    query: str,
    max_answer: int = 2200,
    max_refs: int = 10,
    timeout: int = 90,
    profile: str = "general",
) -> str:
    """Return the backward-compatible Markdown view of one shared collection round."""
    return await _SEARCH_SERVICE.legacy_search_all(
        query=query,
        profile=profile,
        max_answer=max_answer,
        max_refs=max_refs,
        timeout=timeout,
    )


@mcp.tool()
async def search_status() -> str:
    """检查各搜索源健康状态"""
    config = get_runtime_config()
    lines = []
    cdp_ok = await asyncio.to_thread(
        _cdp_alive, DOUBAO_CDP_URL)
    lines.append(f"豆包桌面版 CDP: {'✅ 在线' if cdp_ok else '⚠️ 未启动（调用时自动拉起）'}")
    tavily_keys = config.get("tavily_api_keys", [])
    lines.append(f"Tavily: {'✅ ' + str(len(tavily_keys)) + ' keys' if tavily_keys else '❌ 无 key'}")
    lines.append(
        f"元宝网页版: {'✅ 登录态就绪' if _yuanbao_state_path().exists() else '❌ 登录态缺失'}"
    )
    lines.append(f"Exa(Agent-Reach): {'✅' if shutil.which('mcporter') else '❌ mcporter 未装'}")
    lines.append(
        f"文心助手: {'✅ 登录态' if _wenxin_state_path().exists() else '⚠️ 匿名模式'}"
    )
    gemini_online = await asyncio.to_thread(_cdp_alive, GEMINI_CDP_URL)
    if gemini_online:
        gemini_status = "✅ 隔离无头浏览器在线"
    elif _gemini_profile_path().exists():
        gemini_status = "⚠️ 登录配置就绪（调用时启动）"
    else:
        gemini_status = "❌ 登录配置缺失"
    lines.append(f"Gemini / YouTube: {gemini_status}")
    grok_online = await asyncio.to_thread(_cdp_alive, GROK_CDP_URL)
    if grok_online:
        grok_status = "✅ 隔离浏览器在线"
    elif _grok_profile_path().exists():
        grok_status = "⚠️ 登录配置就绪（调用时启动）"
    else:
        grok_status = "❌ 登录配置缺失"
    lines.append(f"Grok / X: {grok_status}")
    qianwen_online = await asyncio.to_thread(_cdp_alive, QIANWEN_CDP_URL)
    if qianwen_online:
        qianwen_status = "✅ 隔离浏览器在线"
    elif _qianwen_profile_path().exists():
        qianwen_status = "⚠️ 登录配置就绪（调用时启动）"
    else:
        qianwen_status = "❌ 登录配置缺失"
    lines.append(f"千问 / 阿里生态: {qianwen_status}")
    lines.append("结构化首轮: 八源并发 / balanced / 90秒")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
