# shell-agent - Python 编程助手 CLI

<p align="center">
  <strong>一个功能强大的本地编程助手，支持文件操作、Shell命令、代码搜索和任务管理</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-blue.svg" alt="Python Version">
  <img src="https://img.shields.io/badge/License-MIT-green.svg" alt="License">
  <img src="https://img.shields.io/badge/Status-Active-brightgreen.svg" alt="Status">
</p>

## 📑 目录

- [✨ 特性](#-特性)
- [📋 工具详细说明](#-工具详细说明)
- [🚀 快速开始](#-快速开始)
- [📖 详细使用指南](#-详细使用指南)
- [🏗️ 项目结构](#️-项目结构)
- [🔧 开发与测试](#-开发与测试)
- [⚙️ 配置选项](#️-配置选项)
- [🛡️ 安全特性](#️-安全特性)
- [📊 Token 成本管理](#-token-成本管理)
- [🤝 贡献指南](#-贡献指南)
- [📄 许可证](#-许可证)
- [🙏 致谢](#-致谢)
- [❓ 常见问题解答](#-常见问题解答)
- [📞 支持与反馈](#-支持与反馈)

## ✨ 特性

- **多模型支持**: 自动检测并支持 OpenAI GPT、Claude、DeepSeek 等主流模型
- **完整的工具集**: 文件操作、Shell命令、代码搜索、网页抓取、任务管理
- **权限控制**: 三种安全模式（只读、工作区写入、完全访问）
- **会话持久化**: 保存和恢复对话历史
- **交互式 REPL**: 类似终端的交互界面，支持斜杠命令
- **安全检查**: 危险操作前需要用户确认
- **成本统计**: 实时显示Token使用情况

## 📋 工具详细说明

shell-agent 提供了一套完整的工具集，帮助您高效完成编程任务。以下是每个工具的详细说明：

### 📁 文件操作工具

#### `read_file`
- **功能**: 读取文件内容，并以带行号的格式显示
- **参数**: 
  - `path` (string): 文件路径（相对或绝对）
- **示例**: `read_file({"path": "src/main.py"})`
- **输出**: 显示文件内容，每行前有行号，便于代码定位

#### `write_file`
- **功能**: 将内容写入文件，若文件不存在则创建，若存在则覆盖
- **参数**:
  - `path` (string): 文件路径
  - `content` (string): 要写入的完整内容
- **安全**: 危险操作，执行前需要用户确认
- **示例**: `write_file({"path": "test.txt", "content": "Hello World"})`

#### `edit_file`
- **功能**: 精准替换文件中的某段文字（不整体覆盖文件）
- **参数**:
  - `path` (string): 文件路径
  - `old_str` (string): 要被替换的原始内容（必须精确匹配）
  - `new_str` (string): 替换后的新内容
- **要求**: `old_str` 必须在文件中唯一出现
- **安全**: 危险操作，执行前需要用户确认
- **示例**: `edit_file({"path": "main.py", "old_str": "def old_func():", "new_str": "def new_func():"})`

#### `list_files`
- **功能**: 列出目录下的文件和子目录
- **参数**:
  - `path` (string, 可选): 目录路径，默认为当前目录
- **输出**: 显示目录结构，区分文件和文件夹
- **示例**: `list_files({"path": "."})`

### 🖥️ Shell 命令工具

#### `bash`
- **功能**: 执行Shell命令，内置安全检查
- **参数**:
  - `command` (string): 要执行的shell命令
  - `timeout` (integer, 可选): 超时秒数，默认30
  - `description` (string): 命令用途说明（便于用户理解意图）
- **安全**: 自动拦截危险命令（如 `rm -rf /`、fork bomb等）
- **输出**: 返回stdout、stderr和退出码
- **示例**: `bash({"command": "ls -la", "description": "列出详细文件信息"})`

### 🔍 搜索工具

#### `glob_search`
- **功能**: 按通配符模式递归搜索文件
- **参数**:
  - `pattern` (string): glob模式，如 `**/*.py`
  - `base_path` (string, 可选): 搜索起始目录，默认当前目录
- **示例**: 
  - `glob_search({"pattern": "**/*.py"})` - 搜索所有Python文件
  - `glob_search({"pattern": "*.md", "base_path": "docs/"})` - 在docs目录搜索Markdown文件

#### `grep_search`
- **功能**: 在文件内容中用正则表达式搜索文本
- **参数**:
  - `pattern` (string): 正则表达式
  - `base_path` (string, 可选): 搜索起始目录，默认当前目录
  - `file_pattern` (string, 可选): 文件名模式，默认 `*.py`
  - `case_sensitive` (boolean, 可选): 是否区分大小写，默认true
- **示例**: 
  - `grep_search({"pattern": "def.*test"})` - 搜索所有以def开头包含test的函数
  - `grep_search({"pattern": "TODO", "file_pattern": "*.py", "case_sensitive": false})` - 不区分大小写搜索TODO标记

### 🌐 网络工具

#### `web_fetch`
- **功能**: 抓取网页内容，返回可读的纯文本
- **参数**:
  - `url` (string): 要抓取的URL，支持http/https
  - `max_chars` (integer, 可选): 返回内容最大字符数，默认8000
- **适用场景**: 查看文档、README、API说明等
- **示例**: 
  - `web_fetch({"url": "https://github.com/ultraworkers/claw-code-parity"})`
  - `web_fetch({"url": "https://docs.python.org/3/library/os.html", "max_chars": 5000})`

### 📝 任务管理工具

#### `todo_write`
- **功能**: 写入/更新任务列表（全量替换当前列表）
- **参数**:
  - `todos` (array): 完整的任务列表（全量替换当前列表）
    - `id` (string): 任务ID
    - `content` (string): 任务描述
    - `status` (string): 任务状态（pending / in_progress / completed）
    - `priority` (string, 可选): 优先级（low / medium / high），默认medium
- **适用场景**: 分解复杂任务、追踪多步骤进度、让助手记住未完成的工作
- **示例**: 
  ```python
  todo_write({
    "todos": [
      {"id": "1", "content": "分析项目结构", "status": "pending", "priority": "high"},
      {"id": "2", "content": "编写README", "status": "in_progress", "priority": "medium"},
      {"id": "3", "content": "添加测试", "status": "pending", "priority": "low"}
    ]
  })
  ```

#### `todo_read`
- **功能**: 读取当前任务列表，查看所有任务的状态和进度
- **参数**: 无
- **输出**: 返回当前所有任务的状态列表
- **示例**: `todo_read({})`

## 🚀 快速开始

### 安装

1. **克隆仓库**
```bash
git clone https://github.com/yourusername/shell-agent.git
cd shell-agent
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **设置API密钥**
```bash
# OpenAI (GPT模型)
export OPENAI_API_KEY="your-openai-api-key"

# Anthropic (Claude模型)
export ANTHROPIC_API_KEY="your-anthropic-api-key"

# DeepSeek
export DEEPSEEK_API_KEY="your-deepseek-api-key"
```

### 基本使用

**启动交互式REPL：**
```bash
python -m src.main
```

**只读模式（安全模式）：**
```bash
python -m src.main --mode read-only
```

**单次执行命令：**
```bash
python -m src.main "帮我列出当前目录的文件"
```

**恢复之前的会话：**
```bash
python -m src.main --resume 会话ID
```

### 快速演示

下面是一个简单的使用示例，展示shell-agent如何帮助您完成编程任务：

```bash
# 1. 启动shell-agent
python -m src.main

# 2. 在REL中询问：
> 帮我分析这个项目的结构

# 3. shell-agent会：
#    - 使用 list_files 查看目录结构
#    - 使用 read_file 读取关键文件
#    - 分析项目依赖和架构
#    - 提供项目结构分析报告

# 4. 继续询问：
> 为这个项目创建一个简单的测试文件

# 5. shell-agent会：
#    - 使用 todo_write 分解测试创建任务
#    - 使用 write_file 创建测试文件
#    - 使用 edit_file 添加测试用例
#    - 使用 bash 运行测试验证
```

## 📖 详细使用指南

### 交互式REPL模式

启动后，您将看到一个类似终端的界面：
```
shell-agent — Python 编程助手 CLI
输入 /help 查看命令，/exit 退出

Provider: openai  |  模型: gpt-4o  |  权限: full-access  |  会话: abc123def456

> 
```

### 斜杠命令

在REPL中输入斜杠命令可以执行特定操作：

- `/help` - 显示所有可用命令
- `/status` - 显示当前状态（模型、权限、会话等）
- `/cost` - 显示Token使用统计
- `/save` - 保存当前会话
- `/list` - 列出所有已保存的会话
- `/clear` - 清屏
- `/exit` - 退出程序

### 实际使用场景示例

#### 场景1：代码重构
```
> 帮我重构这个Python文件，提取重复的逻辑到函数中

助手会：
1. 使用 read_file 读取文件内容
2. 分析代码结构
3. 使用 edit_file 进行精准修改
4. 使用 todo_write 记录重构步骤
```

#### 场景2：项目文档编写
```
> 为这个项目编写完整的README文档

助手会：
1. 使用 list_files 查看项目结构
2. 使用 read_file 读取关键文件
3. 使用 web_fetch 参考其他项目的README
4. 使用 write_file 创建README.md
5. 使用 todo_write 分解文档编写任务
```

#### 场景3：调试和问题排查
```
> 帮我找出为什么这个Python脚本运行失败

助手会：
1. 使用 read_file 查看脚本内容
2. 使用 bash 运行脚本查看错误信息
3. 使用 grep_search 搜索相关错误模式
4. 使用 edit_file 修复问题
```

#### 场景4：学习新技术
```
> 我想学习FastAPI，帮我创建一个简单的示例项目

助手会：
1. 使用 web_fetch 获取FastAPI官方文档
2. 使用 bash 安装依赖
3. 使用 write_file 创建示例代码
4. 使用 todo_write 制定学习计划
```

### 权限模式

shell-agent 提供三种权限模式，确保操作安全：

1. **read-only** - 只读模式：只能读取文件，不能修改或执行命令
2. **workspace-write** - 工作区写入模式：可以读写工作区文件，但不能执行Shell命令
3. **full-access** - 完全访问模式：可以使用所有工具（默认）

### 任务管理示例

shell-agent 内置了强大的任务管理功能，特别适合处理复杂任务：

```python
# 分解复杂任务
todo_write([
    {"id": "1", "content": "分析项目结构", "status": "pending"},
    {"id": "2", "content": "编写README文档", "status": "pending"},
    {"id": "3", "content": "添加测试用例", "status": "pending"}
])

# 查看任务进度
todo_read()

# 更新任务状态
todo_write([
    {"id": "1", "content": "分析项目结构", "status": "completed"},
    {"id": "2", "content": "编写README文档", "status": "in_progress"},
    {"id": "3", "content": "添加测试用例", "status": "pending"}
])
```

### 网页抓取示例

```python
# 获取项目文档
web_fetch(url="https://github.com/ultraworkers/claw-code-parity", max_chars=5000)

# 查看API文档
web_fetch(url="https://docs.python.org/3/library/os.html", max_chars=3000)
```

### 文件操作示例

```python
# 读取文件内容
read_file(path="src/main.py")

# 列出目录内容
list_files(path=".")

# 搜索Python文件
glob_search(pattern="**/*.py")

# 在文件中搜索特定内容
grep_search(pattern="def.*test", file_pattern="*.py")
```

### Shell命令示例

```python
# 查看当前目录
bash(command="pwd", description="查看当前工作目录")

# 查看Python版本
bash(command="python --version", description="检查Python版本")

# 列出文件详细信息
bash(command="ls -la", description="列出详细文件信息")
```

## 🏗️ 项目结构

```
shell-agent/
├── src/                 # 应用源码包
│   ├── main.py          # REPL主入口和CLI参数解析
│   ├── query/           # API调用和工具使用循环
│   ├── commands/        # 斜杠命令处理
│   ├── permissions/     # 权限模式管理
│   ├── context/         # 工作区上下文管理
│   ├── sessions/        # 会话持久化
│   ├── memory/          # 记忆系统
│   ├── skills/          # Skill加载与内置skill
│   ├── coordinator/     # 多Agent编排
│   ├── mcp/             # MCP客户端与配置
│   └── tools/           # 工具实现
│       ├── __init__.py
│       ├── file_ops.py  # 文件操作工具
│       ├── shell.py     # Shell命令工具
│       ├── search.py    # 搜索工具
│       ├── web.py       # 网页抓取工具
│       └── todo.py      # 任务管理工具
├── requirements.txt     # Python依赖
├── AGENT.md             # 项目说明文档
├── README.md            # 项目文档
└── tests/               # 单元测试
    └── test_tools.py
```

## 🔧 开发与测试

### 运行测试
```bash
pytest tests/ -v
```

### 代码结构说明

- **src/main.py**: 程序入口，处理命令行参数和REPL循环
- **src/query/engine.py**: 核心引擎，管理API调用和工具调度
- **src/tools/**: 所有工具的实现，每个工具都有独立的模块
- **src/permissions/modes.py**: 权限控制系统，确保操作安全

### 添加新工具

要添加新工具，需要在以下位置进行修改：

1. 在 `src/tools/` 目录下创建新的工具模块
2. 在 `src/tools/__init__.py` 中注册工具
3. 在 `src/main.py` 的 `SYSTEM_PROMPT_TEMPLATE` 中更新工具列表
4. 添加相应的测试用例

## ⚙️ 配置选项

### 本项目生成目录

项目内运行时数据统一保存在 `using/`：

- `using/article/`：本地论文 PDF/TXT/MD
- `using/research/`：论文切片、阅读笔记、代码候选、复现计划和 clone 的代码
- `using/memory/`、`using/sessions/`、`using/skills/`、`using/agents/`、`using/file_history/`：记忆、会话、用户 skill、多 Agent 状态和文件备份
- `using/todos.json`：todo 工具状态

`.shell-agent/` 仅作为旧版本运行时目录保留在 `.gitignore` 中；当前项目级生成文件不应再写入该目录。

### Research 复现流程

当用户提出“复现 hnsw 代码”这类请求时，优先使用 `research_prepare_reproduction`。流程会先从 `using/article/` 让模型选择最相关论文；没有合适本地论文时再联网搜索并获取论文；随后生成 `using/research/papers/<paper_id>/chunks.jsonl`，在切片和联网结果中查找 Git 仓库地址，找到则 clone 到该论文目录下，找不到则返回 `no_repository_found`。

### 命令行参数

```bash
python -m src.main [OPTIONS] [PROMPT]

选项:
  --model TEXT     使用的模型（默认自动检测）
  --mode TEXT      权限模式（read-only / workspace-write / full-access）
  --resume TEXT    恢复已保存的会话
  --help           显示帮助信息
```

### 环境变量

```bash
# API密钥
OPENAI_API_KEY="sk-..."
ANTHROPIC_API_KEY="sk-ant-..."
DEEPSEEK_API_KEY="sk-..."

# 可选配置
PY_AGENT_WORKSPACE="/path/to/workspace"  # 工作区目录
PY_AGENT_LOG_LEVEL="INFO"                # 日志级别
```

## 🛡️ 安全特性

1. **危险命令拦截**: 自动拦截 `rm -rf /`、`fork bomb` 等危险命令
2. **操作确认**: 执行写文件、编辑文件等操作前需要用户确认
3. **权限分级**: 三种权限模式，适应不同安全需求
4. **会话隔离**: 每个会话独立，避免操作冲突

## 📊 Token 成本管理

shell-agent 会实时统计Token使用情况：

```
本轮: ↑1,234 ↓5,678 tokens  | 累计: 12,345,678
```

您可以使用 `/cost` 命令查看详细统计，使用 `/save` 命令保存当前会话以便后续恢复。

## 🤝 贡献指南

欢迎贡献代码！请遵循以下步骤：

1. Fork 本仓库
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 开启 Pull Request

### 开发规范

- 遵循 PEP 8 代码风格
- 为新功能添加测试用例
- 更新相关文档
- 确保向后兼容性

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

- 灵感来源于 [claw-code](https://github.com/ultraworkers/claw-code-parity) 项目
- 使用 [Rich](https://github.com/Textualize/rich) 库提供漂亮的终端输出
- 感谢所有贡献者和用户的支持

## ❓ 常见问题解答

### Q: 如何设置API密钥？
**A**: 设置环境变量即可：
```bash
# OpenAI
export OPENAI_API_KEY="your-key"

# Claude (Anthropic)
export ANTHROPIC_API_KEY="your-key"

# DeepSeek
export DEEPSEEK_API_KEY="your-key"
```

### Q: 如何选择不同的AI模型？
**A**: shell-agent会自动检测您设置的API密钥。如果您设置了多个密钥，优先级为：DeepSeek > Anthropic > OpenAI。您也可以通过 `--model` 参数指定具体模型。

### Q: 如何确保操作安全？
**A**: 有三种方式：
1. 使用 `--mode read-only` 只读模式
2. 使用 `--mode workspace-write` 限制写入范围
3. 在完全访问模式下，危险操作前会询问确认

### Q: 会话数据存储在哪里？
**A**: 会话数据保存在 `using/sessions/` 目录下，每个会话一个JSON文件。您可以使用 `/save` 保存当前会话，使用 `--resume` 恢复会话。

### Q: 如何扩展shell-agent的功能？
**A**: 您可以：
1. 在 `src/tools/` 目录下添加新的工具模块
2. 在 `src/tools/__init__.py` 中注册新工具
3. 更新 `src/main.py` 中的工具列表
4. 添加相应的测试用例

### Q: 遇到"权限拒绝"错误怎么办？
**A**: 检查当前权限模式：
- 只读模式：只能读取文件
- 工作区写入模式：可以读写文件，但不能执行命令
- 完全访问模式：可以使用所有工具

### Q: 如何查看Token使用成本？
**A**: 使用 `/cost` 命令查看当前会话的Token统计，或查看每轮对话后的Token计数。

## 📞 支持与反馈

如果您遇到问题或有建议：

1. 查看项目的 Issues 页面
2. 提交新的 Issue 或 Pull Request
3. 确保提供足够的信息以便复现问题

---

<p align="center">
  <strong>Happy Coding! 🚀</strong>
</p>

<p align="center">
  <em>让编程助手成为您的高效伙伴</em>
</p>
