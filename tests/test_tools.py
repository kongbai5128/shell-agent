from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from src import tools  # 触发注册


# ── file_ops 测试 ────────────────────────────────────────────

def test_read_file_exists(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("line1\nline2\nline3")
    result = tools.execute("read_file", {"path": str(f)})
    assert "line1" in result
    assert "1 |" in result  # 行号


def test_read_file_not_found(tmp_path):
    result = tools.execute("read_file", {"path": str(tmp_path / "nope.txt")})
    assert "不存在" in result


def test_write_file(tmp_path):
    f = tmp_path / "out.txt"
    result = tools.execute("write_file", {"path": str(f), "content": "hello"})
    assert "已写入" in result
    assert f.read_text() == "hello"


def test_edit_file_success(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def foo():\n    return 1\n")
    result = tools.execute("edit_file", {
        "path": str(f),
        "old_str": "return 1",
        "new_str": "return 42",
    })
    assert "替换成功" in result
    assert "return 42" in f.read_text()


def test_edit_file_not_found_str(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def foo(): pass\n")
    result = tools.execute("edit_file", {
        "path": str(f),
        "old_str": "not_here",
        "new_str": "x",
    })
    assert "未找到" in result


def test_list_files(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.txt").write_text("")
    result = tools.execute("list_files", {"path": str(tmp_path)})
    assert "a.py" in result
    assert "b.txt" in result


# ── search 测试 ──────────────────────────────────────────────

def test_glob_search(tmp_path):
    (tmp_path / "main.py").write_text("")
    (tmp_path / "utils.py").write_text("")
    (tmp_path / "readme.md").write_text("")
    result = tools.execute("glob_search", {
        "pattern": "*.py",
        "base_path": str(tmp_path),
    })
    assert "main.py" in result
    assert "utils.py" in result
    assert "readme.md" not in result


def test_grep_search(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("def hello():\n    print('world')\n")
    result = tools.execute("grep_search", {
        "pattern": "def hello",
        "base_path": str(tmp_path),
        "file_pattern": "*.py",
    })
    assert "def hello" in result
    assert "code.py" in result


def test_grep_search_no_match(tmp_path):
    f = tmp_path / "code.py"
    f.write_text("x = 1\n")
    result = tools.execute("grep_search", {
        "pattern": "nonexistent_pattern_xyz",
        "base_path": str(tmp_path),
    })
    assert "未" in result


# ── permissions 测试 ─────────────────────────────────────────

def test_permission_read_only_blocks_write():
    from src.permissions import PermissionMode, ToolPermissionContext
    ctx = ToolPermissionContext.from_mode(PermissionMode.READ_ONLY)
    assert ctx.blocks("write_file")
    assert ctx.blocks("bash")
    assert not ctx.blocks("read_file")
    assert not ctx.blocks("glob_search")


def test_permission_full_access_requires_confirm():
    from src.permissions import PermissionMode, ToolPermissionContext
    ctx = ToolPermissionContext.from_mode(PermissionMode.FULL_ACCESS)
    assert ctx.requires_confirm("bash")
    assert ctx.requires_confirm("write_file")
    assert not ctx.requires_confirm("read_file")


# ── sessions 测试 ────────────────────────────────────────────

def test_save_and_load_session(tmp_path):
    from src.sessions import StoredSession, save_session, load_session
    s = StoredSession(
        session_id="test123",
        messages=[{"role": "user", "content": "hello"}],
        input_tokens=100,
        output_tokens=50,
        model="claude-opus-4-6",
    )
    save_session(s, directory=tmp_path)
    loaded = load_session("test123", directory=tmp_path)
    assert loaded.session_id == "test123"
    assert loaded.input_tokens == 100
    assert loaded.messages[0]["content"] == "hello"
