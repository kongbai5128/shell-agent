from __future__ import annotations

import re
import time
import urllib.request
import urllib.error
from html.parser import HTMLParser

from . import ToolSpec, register


# ── 修复版 HTML 转纯文本 ────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """
    把 HTML 转成可读纯文本。

    修复了原版 bug：
    原版用整数计数器 _skip 追踪嵌套，但豆瓣页面有
    <script type="text/x-jquery-tmpl"> 模板 script，
    HTMLParser 会把模板里的 </li>、</p> 当作真实结束标签
    触发 handle_endtag，导致 _skip 被错误减到 0，
    script 内容泄漏为正文，真正的电影列表被淹没。

    修复方案：改用「标签名栈」精确追踪嵌套，
    只有遇到栈顶对应的结束标签才出栈。
    """
    SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}
    BLOCK_TAGS = {"br", "p", "div", "h1", "h2", "h3", "h4", "h5",
                  "li", "tr", "td", "th", "section", "article", "header", "footer", "nav"}

    def __init__(self):
        super().__init__()
        self._skip_stack: list[str] = []   # 栈：追踪当前跳过的标签层级
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if self._skip_stack:
            # 已在跳过状态，只追踪同类嵌套
            if tag in self.SKIP_TAGS:
                self._skip_stack.append(tag)
            return
        if tag in self.SKIP_TAGS:
            self._skip_stack.append(tag)   # 开始跳过
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        # 只有栈顶匹配时才出栈，模板里的假结束标签不影响栈
        if self._skip_stack and tag == self._skip_stack[-1]:
            self._skip_stack.pop()

    def handle_data(self, data):
        if self._skip_stack:
            return
        text = data.strip()
        if text:
            self.parts.append(text + " ")

    def get_text(self) -> str:
        raw = "".join(self.parts)
        lines = [l.rstrip() for l in raw.splitlines()]
        cleaned = []
        blank = 0
        for line in lines:
            if not line:
                blank += 1
                if blank <= 1:
                    cleaned.append("")
            else:
                blank = 0
                cleaned.append(line)
        return "\n".join(cleaned).strip()


# ── 豆瓣专用结构化解析 ─────────────────────────────────────────

def _parse_douban_top(html: str, limit: int = 10) -> str:
    """
    专门解析豆瓣 Top250 页面，提取结构化电影信息。
    比通用 HTMLParser 更可靠，直接针对豆瓣 HTML 结构用正则提取。
    """
    pattern = re.compile(
        r'<em>(\d+)</em>.*?'
        r'<span class="title">([^<&]+)</span>.*?'
        r'<p>\s*(.*?)\s*</p>.*?'
        r'<span class="rating_num"[^>]*>([^<]+)</span>.*?'
        r'<span>(\d+人评价)</span>',
        re.DOTALL
    )
    results = []
    for m in pattern.finditer(html):
        if len(results) >= limit:
            break
        rank, title, info_raw, rating, votes = m.groups()
        info = re.sub(r'<[^>]+>', '', info_raw)
        info = re.sub(r'&nbsp;', ' ', info)
        info = re.sub(r'\s+', ' ', info).strip()
        results.append(
            f"{rank}. 《{title}》  ⭐{rating}  {votes}\n"
            f"   {info}"
        )
    return "\n\n".join(results) if results else ""


# ── URL 抓取 ───────────────────────────────────────────────────

def _fetch_url(url: str, timeout: int = 15) -> tuple[int, str, str]:
    """返回 (status_code, content_type, body_text)"""
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (shell-agent/1.0)",
            "Accept": "text/html,application/xhtml+xml,*/*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].split(";")[0].strip()
            body = raw.decode(charset, errors="replace")
            return code, content_type, body
    except urllib.error.HTTPError as e:
        return e.code, "", f"HTTP 错误: {e.reason}"
    except urllib.error.URLError as e:
        return 0, "", f"连接失败: {e.reason}"


def _web_fetch(inp: dict) -> str:
    url = inp["url"]
    max_chars = inp.get("max_chars", 8000)

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    started = time.time()
    code, content_type, body = _fetch_url(url)
    elapsed_ms = int((time.time() - started) * 1000)

    if code == 0:
        return body

    text = ""
    if "html" in content_type.lower():
        # 豆瓣 top250 优先用专用解析器
        if "douban.com" in url and "top250" in url:
            text = _parse_douban_top(body)
        # 通用修复版解析
        if not text:
            extractor = _TextExtractor()
            extractor.feed(body)
            text = extractor.get_text()
    else:
        text = body

    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True

    lines = [
        f"URL: {url}",
        f"状态: {code}  |  耗时: {elapsed_ms}ms  |  内容类型: {content_type}",
    ]
    if truncated:
        lines.append(f"[内容已截断至 {max_chars} 字符]")
    lines.append("")
    lines.append(text)
    return "\n".join(lines)


register(ToolSpec(
    name="web_fetch",
    description=(
        "抓取网页内容，返回可读的纯文本。"
        "支持豆瓣 Top250 等结构化页面的专用解析。"
        "对标 claw-code web_fetch 工具。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "要抓取的 URL，支持 http/https",
            },
            "max_chars": {
                "type": "integer",
                "description": "返回内容最大字符数，默认 8000",
                "default": 8000,
            },
        },
        "required": ["url"],
    },
    handler=_web_fetch,
    dangerous=False,
))
