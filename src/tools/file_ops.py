from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from . import ToolSpec, register
from ..paths import FILE_HISTORY_DIR

# ── 文件历史备份（对标 Claude Code utils/fileHistory.ts）─────────
# 备份目录：using/file_history/{16位路径哈希}/
# 每个文件最多保留 10 份备份，自动淘汰最旧的
HISTORY_DIR = FILE_HISTORY_DIR
MAX_BACKUPS_PER_FILE = 10

# ── mtime 追踪（对标 Claude Code FileEditTool validateInput）─────
# key = str(path.resolve())，value = 上次 read_file 时的 mtime
_read_mtimes: dict[str, float] = {}


def _path_key(path: Path) -> str:
    """把绝对路径转成 16 位哈希，用作备份子目录名"""
    import hashlib
    return hashlib.md5(str(path.resolve()).encode()).hexdigest()[:16]


def _backup_file(path: Path) -> Path | None:
    """
    在写入/编辑前备份文件当前内容（对标 fileHistoryTrackEdit）。
    返回备份文件路径；文件不存在时返回 None。
    """
    if not path.exists():
        return None

    key = _path_key(path)
    backup_dir = HISTORY_DIR / key
    backup_dir.mkdir(parents=True, exist_ok=True)

    # 存储原始路径的元数据（方便 /undo 显示）
    meta_file = backup_dir / "meta.json"
    if not meta_file.exists():
        meta_file.write_text(
            json.dumps({"original_path": str(path.resolve())}, ensure_ascii=False)
        )

    # 备份文件名：毫秒时间戳（保证唯一且有序）
    ts = int(time.time() * 1000)
    backup_path = backup_dir / f"{ts}.bak"
    shutil.copy2(path, backup_path)

    # 淘汰最旧备份，只保留 MAX_BACKUPS_PER_FILE 份
    backups = sorted(backup_dir.glob("*.bak"), key=lambda p: p.name)
    while len(backups) > MAX_BACKUPS_PER_FILE:
        backups.pop(0).unlink()

    return backup_path


def _atomic_write(path: Path, content: str) -> None:
    """
    原子写入：先写临时文件，再 rename 替换目标（对标 Claude Code utils/file.ts writeTextContent）。
    POSIX 上 rename 是原子操作，AI 写到一半崩溃不会损坏原文件。
    失败时清理临时文件，回退为直接写入。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(f"{path}.tmp.{os.getpid()}.{int(time.time() * 1000)}")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.rename(tmp, path)   # POSIX 原子替换
    except Exception:
        # 清理临时文件，回退为直接写入
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        path.write_text(content, encoding="utf-8")


def get_latest_backup(file_path: str) -> Path | None:
    """返回某文件最新一份备份的路径，供 /undo 使用"""
    path = Path(file_path).resolve()
    backup_dir = HISTORY_DIR / _path_key(path)
    if not backup_dir.exists():
        return None
    backups = sorted(backup_dir.glob("*.bak"), key=lambda p: p.name)
    return backups[-1] if backups else None


def undo_last_edit(file_path: str) -> str:
    """
    撤销最近一次对 file_path 的编辑，恢复为备份内容。
    供 /undo 命令调用。
    """
    path = Path(file_path).resolve()
    backup = get_latest_backup(file_path)
    if backup is None:
        return f"没有找到 '{file_path}' 的备份记录"

    content = backup.read_text(encoding="utf-8")
    _atomic_write(path, content)

    # 删除刚用过的备份（避免重复 undo 到同一版本）
    backup.unlink()

    # 更新 mtime 缓存，防止 undo 后立刻 edit_file 被 mtime 检查误拦
    _read_mtimes[str(path)] = path.stat().st_mtime

    return f"已撤销：'{path}' 恢复到上一个备份版本（备份时间戳：{backup.stem}）"


# ── 工具实现 ──────────────────────────────────────────────────

def _read_file(inp: dict) -> str:
    path = Path(inp["path"])
    if not path.exists():
        return f"错误：文件不存在 '{path}'"
    if not path.is_file():
        return f"错误：'{path}' 不是文件"
    try:
        # 记录 mtime，供 edit_file 的 mtime 检查使用
        _read_mtimes[str(path.resolve())] = path.stat().st_mtime

        content = path.read_text(encoding="utf-8")
        # 加行号，方便 Claude 定位
        lines = content.splitlines()
        numbered = "\n".join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
        return f"文件: {path}\n总行数: {len(lines)}\n\n{numbered}"
    except Exception as e:
        return f"读取失败: {e}"


def _write_file(inp: dict) -> str:
    path = Path(inp["path"])
    content = inp["content"]
    try:
        # 备份后原子写入
        _backup_file(path)
        _atomic_write(path, content)
        # 注意：write_file 不更新 _read_mtimes
        # edit_file 要求先 read_file 才能编辑，write_file 不算"读取"
        lines = content.count("\n") + 1
        return f"已写入 '{path}'，共 {lines} 行"
    except Exception as e:
        return f"写入失败: {e}"


def _edit_file(inp: dict) -> str:
    """
    精准替换文件中的某段文字，不整体覆盖（对标 claw-code edit_file）。

    安全机制（对标 Claude Code FileEditTool）：
    1. mtime 检查：必须先 read_file 才能 edit_file；若文件在读取后被外部修改则拒绝
    2. 备份：编辑前先备份，支持 /undo 撤销
    3. 原子写入：写临时文件再 rename，AI 中途崩溃不损坏原文件
    """
    path = Path(inp["path"])
    old_str = inp["old_str"]
    new_str = inp["new_str"]

    if not path.exists():
        return f"错误：文件不存在 '{path}'"

    # ── mtime 检查（对标 validateInput 中的 readFileState 检查）──
    abs_path = str(path.resolve())
    current_mtime = path.stat().st_mtime
    if abs_path not in _read_mtimes:
        return (
            f"错误：编辑前必须先用 read_file 读取 '{path}'，"
            "以确认你看到的是最新内容"
        )
    if current_mtime != _read_mtimes[abs_path]:
        return (
            f"错误：'{path}' 在你上次读取后已被修改（可能被其他进程更新），"
            "请重新 read_file 再编辑"
        )

    content = path.read_text(encoding="utf-8")
    count = content.count(old_str)

    if count == 0:
        return f"错误：在 '{path}' 中未找到目标内容，请检查是否完全匹配（包括空格和换行）"
    if count > 1:
        return f"错误：在 '{path}' 中找到 {count} 处匹配，需要唯一匹配才能安全替换"

    # ── 备份 + 原子写入 ────────────────────────────────────────
    _backup_file(path)
    new_content = content.replace(old_str, new_str, 1)
    _atomic_write(path, new_content)

    # 更新 mtime 缓存，允许连续编辑
    _read_mtimes[abs_path] = path.stat().st_mtime

    return f"已编辑 '{path}'：替换成功（已备份，可用 /undo 撤销）"


def _list_files(inp: dict) -> str:
    path = Path(inp.get("path", "."))
    if not path.exists():
        return f"错误：路径不存在 '{path}'"
    try:
        entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        lines = []
        for e in entries:
            if e.is_dir():
                lines.append(f"  📁 {e.name}/")
            else:
                size = e.stat().st_size
                lines.append(f"  📄 {e.name}  ({size} bytes)")
        return f"目录: {path}\n" + "\n".join(lines) if lines else f"目录 '{path}' 为空"
    except Exception as e:
        return f"列举失败: {e}"


# 注册工具
register(ToolSpec(
    name="read_file",
    description="读取文件内容，显示带行号的内容。",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径（相对或绝对）"},
        },
        "required": ["path"],
    },
    handler=_read_file,
))

register(ToolSpec(
    name="write_file",
    description="将内容写入文件，若文件不存在则创建，若存在则覆盖。",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "content": {"type": "string", "description": "要写入的完整内容"},
        },
        "required": ["path", "content"],
    },
    handler=_write_file,
    dangerous=True,
))

register(ToolSpec(
    name="edit_file",
    description="精准替换文件中的某段文字。old_str 必须在文件中唯一出现。",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件路径"},
            "old_str": {"type": "string", "description": "要被替换的原始内容（必须精确匹配）"},
            "new_str": {"type": "string", "description": "替换后的新内容"},
        },
        "required": ["path", "old_str", "new_str"],
    },
    handler=_edit_file,
    dangerous=True,
))

register(ToolSpec(
    name="list_files",
    description="列出目录下的文件和子目录。",
    input_schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "目录路径，默认为当前目录"},
        },
    },
    handler=_list_files,
))
