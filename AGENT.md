# shell-agent 项目说明

## 项目结构
- `src/main.py` — REPL 主入口和 CLI 参数解析
- `src/query/engine.py` — Anthropic API 调用和 tool use 循环
- `src/commands/handlers.py` — 斜杠命令处理（/help /status /cost 等）
- `src/permissions/modes.py` — 权限模式（read-only / workspace-write / full-access）
- `src/context/workspace.py` — 工作区上下文，读取 AGENT.md 注入 system prompt
- `src/sessions/store.py` — 会话持久化（保存/恢复）
- `src/memory/store.py` — 记忆系统
- `src/skills/registry.py` — Skill 加载、保存和内置 skill 初始化
- `src/coordinator/runtime.py` — 多 Agent 编排
- `src/tools/` — 工具实现（file_ops / shell / search）
- `src/mcp/` — MCP 客户端和配置加载
- `tests/` — pytest 单元测试

## 验证命令
- 运行测试: `pytest tests/ -v`
- 启动 REPL: `python -m src.main`
- 只读模式: `python -m src.main --mode read-only`
- 单次执行: `python -m src.main "帮我列出当前目录的文件"`
