"""
浏览器自动化工具 — 使用 Playwright 控制 Chrome/Chromium

工作方式：
  - 有界面模式运行浏览器（用户可见）
  - 遇到人机验证时自动暂停，由用户手动完成后按 Enter 继续
  - 会话内复用同一个浏览器实例（节省启动时间）

对标 Claude Code 的 claude-in-chrome 功能（src/skills/bundled/claudeInChrome.ts）
"""
from __future__ import annotations

import time
from typing import Callable

from . import ToolSpec, register

# ── 全局浏览器状态（会话内复用）─────────────────────────────────
_pw = None
_browser = None
_page = None

# 人机验证暂停回调，由 main.py 注入，以便停止 spinner 再提示
_pause_callback: Callable[[str], None] | None = None

# 人机验证 / 反爬页面特征（标题 + 正文中出现任一即暂停）
_CAPTCHA_SIGNS = [
    "just a moment",
    "checking your browser",
    "please wait",
    "please verify you are human",
    "captcha",
    "human verification",
    "cloudflare",
    "ddos-guard",
    "are you a human",
    "bot detection",
    "security check",
    "access denied",
    "please turn javascript on",
    "请完成安全验证",
    "人机验证",
    "滑动验证",
    "拖动滑块",
    "点击完成验证",
    "verify you are human",
    "unusual traffic",
    "automated queries",
    "not a robot",
    "i'm not a robot",
    "before you continue",
]

# URL 中出现以下特征时，直接判定为需要人工介入
_CAPTCHA_URL_PATTERNS = [
    "/sorry/",
    "google.com/sorry",
    "recaptcha",
    "/captcha",
    "challenge?",
    "bot-protection",
    "ddos-guard",
    "cf-chl-bypass",
    "blocked",
    "verifyagemobile",
    "security-check",
]


def set_pause_callback(cb: Callable[[str], None]) -> None:
    """由 main.py 在每轮对话前注入，遇到验证时暂停并通知用户"""
    global _pause_callback
    _pause_callback = cb


def _get_page():
    """懒初始化：按优先级尝试 Chrome → Edge → Playwright 内置 Chromium"""
    global _pw, _browser, _page
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "需要安装 Playwright：\n  pip install playwright\n  playwright install chromium"
        )

    if _pw is None:
        _pw = sync_playwright().start()

    if _browser is None or not _browser.is_connected():
        launched = False
        for channel in ("chrome", "msedge"):
            try:
                _browser = _pw.chromium.launch(
                    headless=False,
                    channel=channel,
                    args=["--start-maximized"],
                )
                launched = True
                break
            except Exception:
                continue
        if not launched:
            # 回退到 Playwright 内置 Chromium
            _browser = _pw.chromium.launch(
                headless=False,
                args=["--start-maximized"],
            )

    if _page is None or _page.is_closed():
        _page = _browser.new_page()

    return _page


def _extract_text(page) -> str:
    """提取页面正文纯文本（去除 script/style 节点）"""
    try:
        text = page.evaluate("""() => {
            document.querySelectorAll('script, style, noscript, svg').forEach(el => el.remove());
            return document.body?.innerText || document.body?.textContent || '';
        }""")
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return "\n".join(lines)
    except Exception as e:
        return f"(无法提取页面文本: {e})"


def _has_captcha(page) -> bool:
    """检测当前页面是否出现人机验证（URL 特征 + 文本特征双重检测）"""
    try:
        current_url = page.url.lower()
        # 1. URL 特征：最可靠，优先检测（如 google.com/sorry/index）
        if any(pattern in current_url for pattern in _CAPTCHA_URL_PATTERNS):
            return True
        # 2. 文本特征：检查标题 + 正文前 2000 字
        snippet = (page.title() + " " + page.evaluate(
            "() => document.body?.innerText?.slice(0, 2000) || ''"
        )).lower()
        return any(sign in snippet for sign in _CAPTCHA_SIGNS)
    except Exception:
        return False


def _wait_load(page, timeout: int = 1200) -> None:
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass


def _pause_for_verification(message: str) -> None:
    """停止 spinner 后提示用户，完成后按 Enter 继续"""
    if _pause_callback:
        _pause_callback(message)
    else:
        print(f"\n{message}\n  在浏览器中完成后按 Enter 继续...")
        input()


# ── 工具实现 ──────────────────────────────────────────────────

def _browser_navigate(inp: dict) -> str:
    url = inp["url"]
    wait_for_user = inp.get("wait_for_user", False)
    max_chars = inp.get("max_chars", 8000)

    if not url.startswith(("http://", "https://")):
        return "错误：URL 必须以 http:// 或 https:// 开头"

    try:
        page = _get_page()
    except RuntimeError as e:
        return str(e)

    try:
        page.goto(url, timeout=30000)
        _wait_load(page)
        time.sleep(1)

        # 主动暂停（如登录页）或检测到验证
        if wait_for_user or _has_captcha(page):
            reason = "需要人工操作（登录 / 验证）" if wait_for_user else f"检测到反爬 / 人机验证（当前 URL: {page.url}）"
            _pause_for_verification(f"⚠️  {reason}，请在浏览器完成后继续")
            _wait_load(page)
            time.sleep(1)

        title = page.title()
        current_url = page.url
        text = _extract_text(page)
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n... [已截断，共 {len(text)} 字符]"

        return f"页面标题: {title}\n当前 URL: {current_url}\n\n{text}"

    except Exception as e:
        return f"导航失败: {e}"


def _browser_click(inp: dict) -> str:
    text_hint = inp.get("text", "")
    selector = inp.get("selector", "")

    try:
        page = _get_page()
    except RuntimeError as e:
        return str(e)

    try:
        if text_hint:
            page.get_by_text(text_hint, exact=False).first.click(timeout=5000)
        elif selector:
            page.click(selector, timeout=5000)
        else:
            return "错误：需要提供 text 或 selector"

        _wait_load(page)
        time.sleep(0.5)

        if _has_captcha(page):
            _pause_for_verification("⚠️  点击后出现人机验证，请在浏览器完成后继续")
            _wait_load(page)

        return f"已点击，当前页面: {page.title()} ({page.url})"

    except Exception as e:
        return f"点击失败: {e}"


def _browser_fill(inp: dict) -> str:
    selector = inp["selector"]
    value = inp["value"]

    try:
        page = _get_page()
    except RuntimeError as e:
        return str(e)

    try:
        page.fill(selector, value, timeout=5000)
        return f"已填写 '{selector}'"
    except Exception as e:
        return f"填写失败: {e}"


def _browser_get_content(inp: dict) -> str:
    max_chars = inp.get("max_chars", 8000)

    try:
        page = _get_page()
    except RuntimeError as e:
        return str(e)

    if _has_captcha(page):
        _pause_for_verification("⚠️  当前页面有人机验证，请在浏览器完成后继续")
        _wait_load(page)

    title = page.title()
    url = page.url
    text = _extract_text(page)
    if len(text) > max_chars:
        text = text[:max_chars] + f"\n... [共 {len(text)} 字符]"

    return f"页面标题: {title}\nURL: {url}\n\n{text}"


def _browser_close(inp: dict) -> str:
    global _browser, _page, _pw
    try:
        if _page and not _page.is_closed():
            _page.close()
        if _browser and _browser.is_connected():
            _browser.close()
        if _pw:
            _pw.stop()
        _page = _browser = _pw = None
        return "浏览器已关闭"
    except Exception as e:
        return f"关闭失败: {e}"


# ── 注册工具 ──────────────────────────────────────────────────

register(ToolSpec(
    name="browser_navigate",
    description=(
        "用 Chrome 浏览器打开网页，返回页面文本内容。"
        "比 web_fetch 更强：支持 JavaScript 渲染、登录态、动态内容。"
        "注意遇到 Cloudflare / reCAPTCHA 等人机验证或者反爬虫机制的情况时要自动暂停，等用户在浏览器完成后继续。"
        "set wait_for_user=true 可主动要求用户介入（如需要登录的页面）。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要访问的 URL（http/https）"},
            "max_chars": {
                "type": "integer",
                "description": "返回内容最大字符数，默认 8000",
                "default": 8000,
            },
            "wait_for_user": {
                "type": "boolean",
                "description": "是否主动暂停让用户操作（登录/验证），默认 false",
                "default": False,
            },
        },
        "required": ["url"],
    },
    handler=_browser_navigate,
    dangerous=False,
))

register(ToolSpec(
    name="browser_click",
    description="点击当前浏览器页面上的元素。优先用 text 参数按文本内容定位，或用 selector 按 CSS 选择器定位。",
    input_schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "按元素文本内容点击（推荐，模糊匹配）"},
            "selector": {"type": "string", "description": "CSS 选择器（text 不可用时使用）"},
        },
    },
    handler=_browser_click,
    dangerous=False,
))

register(ToolSpec(
    name="browser_fill",
    description="在浏览器页面的输入框中填写内容（登录表单、搜索框等）。",
    input_schema={
        "type": "object",
        "properties": {
            "selector": {"type": "string", "description": "输入框的 CSS 选择器，如 'input[name=username]'"},
            "value": {"type": "string", "description": "要填写的内容"},
        },
        "required": ["selector", "value"],
    },
    handler=_browser_fill,
    dangerous=False,
))

register(ToolSpec(
    name="browser_get_content",
    description="获取当前浏览器页面的文本内容（不导航，读取已加载的页面）。",
    input_schema={
        "type": "object",
        "properties": {
            "max_chars": {"type": "integer", "description": "最大字符数，默认 8000"},
        },
    },
    handler=_browser_get_content,
    dangerous=False,
))

register(ToolSpec(
    name="browser_close",
    description="关闭浏览器，释放资源。任务完成后调用。",
    input_schema={"type": "object", "properties": {}},
    handler=_browser_close,
    dangerous=False,
))
