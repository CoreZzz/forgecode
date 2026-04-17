# Forge 源码学习指南：通过代码理解 AI Agent

> 面向 Java 开发者，零 AI 基础，跟着代码走完一次完整的 Agent 请求流程。

---

## 第零章：你需要先知道的概念

在开始看代码之前，只需要理解三件事：

1. **LLM（大语言模型）** 就是一个 HTTP API：你发一段文字过去，它回一段文字回来。跟调 REST 接口没有本质区别。
2. **Agent（智能体）** 和普通聊天的区别在于：Agent 能**调用工具**。它会自己决定用哪个工具、传什么参数、拿到结果后继续思考。
3. **Agent Loop（智能体循环）**：用户提问 → LLM 思考 → 决定调用工具 → 执行工具 → 结果喂回 LLM → 继续思考 → 直到给出最终回答。这就是所有 AI Agent 的核心模式。

**Rust 对 Java 的速查：**

| Rust 概念 | Java 类比 |
|-----------|----------|
| `struct` | class（只有字段没有方法的部分） |
| `impl` | 在 class 里写方法 |
| `trait` | interface |
| `impl Trait for Struct` | `implements Interface` |
| `enum` | 比 Java enum 强大得多，类似 sealed class + pattern matching |
| `Result<T, E>` | 类似 checked exception，但用返回值表示 |
| `Option<T>` | `Optional<T>` |
| `Arc<T>` | 共享引用计数，类似手动管理的 `@Autowired` |
| `async/await` | `CompletableFuture` 的语法糖 |
| `pub` | `public` |
| `mod` | package |
| Cargo workspace | Maven multi-module project |

---

## 第一章：程序入口 — 从 `main` 函数开始

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_main/src/main.rs` | 程序入口。解析命令行参数（clap）、读取配置、初始化并启动 UI |

### 你会看到什么

```
main() → run() → UI::init() → ui.run()
```

- `Cli::parse()` 解析命令行参数（类似 args4j/picocli）
- `ForgeConfig::read()` 读配置文件（类似读 application.yml）
- `UI::init()` 初始化整个应用，把所有组件组装起来
- `ui.run()` 进入交互循环

**重点看什么**：不需要细看每一行，只需要知道：程序从 `main.rs` 启动，最终把控制权交给 `UI`。

---

## 第二章：消息 — Agent 世界的通用语言

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_domain/src/message.rs` | 消息模型：系统消息、用户消息、助手消息、工具调用、工具结果 |
| `crates/forge_domain/src/chat_request.rs` | 发给 LLM 的请求结构体 |
| `crates/forge_domain/src/chat_response.rs` | LLM 返回的响应结构体 |

### 为什么先看这里

Agent 系统里所有东西都是**消息**。你发给 LLM 的是消息，LLM 回你的也是消息，工具调用是消息的一种，工具结果也是消息。理解了消息模型，后面的代码才能看懂。

### 消息的类型

```
System      → "你是一个代码助手，你有以下工具..."
User        → "帮我重构 auth 模块"
Assistant   → "好的，我先读一下文件"  +  [ToolCall { name: "fs_read", args: {path: "src/auth.rs"} }]
ToolResult  → "文件内容：pub fn login() { ... }"
Assistant   → "我看到代码结构如下，建议这样重构..."
```

**类比 Java**：这就是一套 DTO。`Message` 是基类（用 enum 实现），不同的变体就是不同的消息类型。跟发 JSON 给 REST API 是一样的。

---

## 第三章：工具定义 — Agent 能做什么

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_domain/src/tools/definition/tool_definition.rs` | 工具定义：名字、描述、参数 schema（JSON Schema 格式） |
| `crates/forge_domain/src/tools/definition/name.rs` | 工具名称的类型封装 |
| `crates/forge_domain/src/tools/call/tool_call.rs` | 一次工具调用的具体内容：工具名 + 参数 |
| `crates/forge_domain/src/tools/call/context.rs` | 工具调用时的上下文信息 |
| `crates/forge_domain/src/tools/result.rs` | 工具执行后的返回结果 |

### 你会看到什么

`ToolDefinition` 就三个字段：
```rust
pub struct ToolDefinition {
    pub name: ToolName,        // "fs_read"
    pub description: String,   // "读取文件内容"
    pub input_schema: Schema,  // JSON Schema: { "path": "string", "offset": "number" }
}
```

**这就是 LLM 能"看到"的工具信息**。LLM 看到这个名字、描述和参数格式，就能决定要不要调用它、传什么参数。

**类比 Java**：就像 Swagger/OpenAPI 文档描述了一个接口的参数格式。LLM 读这个"文档"来决定怎么调用。

---

## 第四章：Agent Loop — 最核心的循环

### 要读的文件（按顺序）

| 文件 | 作用 |
|------|------|
| `crates/forge_app/src/app.rs` | `ForgeApp`：整个 Agent 应用的入口。组装所有依赖，调用编排器 |
| `crates/forge_app/src/orch.rs` | `Orchestrator`：**Agent Loop 的主循环**。这是整个项目最核心的文件 |

### `app.rs` 的流程

```
chat() 被调用
  → 从数据库加载 conversation
  → 获取 agent 配置
  → 获取工具列表
  → 拼装 system prompt
  → 拼装 user prompt
  → 创建 Orchestrator
  → orch.run() 开始循环
  → 保存 conversation
```

### `orch.rs` — Agent Loop 核心

`Orchestrator.run()` 就是 Agent Loop 的实现。你会看到：

```
loop {
    1. 把消息发给 LLM
    2. 拿到响应
    3. 响应里有工具调用吗？
       - 有 → 执行工具 → 把结果追加到消息历史 → 回到第 1 步
       - 没有 → 循环结束，返回最终回答
}
```

`execute_tool_calls()` 方法处理工具调用的执行：
- Task 类工具（子 Agent 调用）并行执行
- 其他工具顺序执行
- 每个工具执行前后触发生命周期事件（Hook）

**这是整个项目最重要的文件**。读懂了这个文件，你就理解了 AI Agent 的核心模式。

---

## 第五章：工具的具体实现

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_app/src/tool_registry.rs` | 工具注册表：收集所有可用工具 |
| `crates/forge_app/src/tool_resolver.rs` | 工具解析器：根据 Agent 配置决定它能用哪些工具 |
| `crates/forge_app/src/tool_executor.rs` | 工具执行器：路由到具体的工具服务 |
| `crates/forge_app/src/operation.rs` | 工具操作的输入输出格式化 |
| `crates/forge_services/src/tool_services/mod.rs` | 所有工具服务的入口 |
| `crates/forge_services/src/tool_services/fs_read.rs` | 文件读取工具 |
| `crates/forge_services/src/tool_services/fs_write.rs` | 文件写入工具 |
| `crates/forge_services/src/tool_services/fs_patch.rs` | 文件补丁（局部修改） |
| `crates/forge_services/src/tool_services/shell.rs` | Shell 命令执行工具 |
| `crates/forge_services/src/tool_services/fs_search.rs` | 文件内容搜索（grep） |
| `crates/forge_services/src/tool_services/fetch.rs` | HTTP 请求工具 |
| `crates/forge_services/src/tool_services/fs_remove.rs` | 文件删除工具 |
| `crates/forge_services/src/tool_services/fs_undo.rs` | 撤销文件操作 |
| `crates/forge_services/src/tool_services/skill.rs` | 技能调用工具 |
| `crates/forge_services/src/tool_services/followup.rs` | 向用户追问的工具 |
| `crates/forge_services/src/tool_services/plan_create.rs` | 创建计划文件的工具 |
| `crates/forge_services/src/tool_services/image_read.rs` | 图片读取工具 |

### 每个工具都遵循同样的模式

```
1. 解析参数（LLM 传来的 JSON）
2. 执行操作（读文件 / 执行命令 / ...）
3. 格式化结果成文本
4. 返回给 LLM
```

**建议**：先看 `fs_read.rs`（最简单），理解模式后，再看 `shell.rs`（更有代表性）。

---

## 第六章：System Prompt — Agent 的"人设"

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_app/src/system_prompt.rs` | System Prompt 的组装逻辑 |
| `templates/forge-system-prompt-title-generation.md` | 标题生成用的 prompt 模板 |
| `templates/forge-commit-message-prompt.md` | 提交信息生成用的 prompt 模板 |
| `templates/forge-tool-retry-message.md` | 工具重试时的 prompt 模板 |

### 你会看到什么

System Prompt = 多个部分拼在一起：
```
你是一个代码助手           ← 基础人设
你有以下工具可用：...       ← 工具列表和用法说明
当前项目目录结构：...       ← 上下文信息
用户自定义规则：...         ← 来自 AGENTS.md
```

**类比 Java**：就像拼装一个复杂的 SQL 查询，不同条件拼接不同的子句。

---

## 第七章：LLM API 调用 — 和大模型通信

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_api/src/api.rs` | API trait 定义 |
| `crates/forge_api/src/forge_api.rs` | API 实现：构造 HTTP 请求、解析流式响应 |
| `crates/forge_app/src/dto/openai/` | OpenAI 格式的请求/响应 DTO |
| `crates/forge_app/src/dto/anthropic/` | Anthropic 格式的请求/响应 DTO |
| `crates/forge_app/src/dto/google/` | Google 格式的请求/响应 DTO |
| `crates/forge_repo/src/provider/chat.rs` | Provider 路由：根据模型选择调哪个 API |
| `crates/forge_repo/src/provider/openai.rs` | OpenAI 兼容 API 的实现 |
| `crates/forge_repo/src/provider/anthropic.rs` | Anthropic API 的实现 |
| `crates/forge_repo/src/provider/google.rs` | Google Vertex AI 的实现 |
| `crates/forge_repo/src/provider/bedrock.rs` | AWS Bedrock 的实现 |

### 你会看到什么

调用 LLM 就是发 HTTP 请求。不同 Provider 的 API 格式不同（OpenAI 格式 vs Anthropic 格式），但核心逻辑一样：
1. 把消息数组序列化成 JSON
2. POST 到 API endpoint
3. 解析 SSE（Server-Sent Events）流式响应

**类比 Java**：就像你用 `RestTemplate` 调外部 API，只是这里的响应是流式的（chunk by chunk 回来）。

---

## 第八章：Agent 配置 — 不同的 Agent 怎么区分

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_domain/src/agent.rs` | Agent 的领域模型：ID、模型、系统提示、工具列表、权限 |
| `crates/forge_repo/src/agents/forge.md` | forge Agent 的定义（默认，能修改文件） |
| `crates/forge_repo/src/agents/sage.md` | sage Agent 的定义（只读，用于研究） |
| `crates/forge_repo/src/agents/muse.md` | muse Agent 的定义（规划，输出计划） |
| `crates/forge_app/src/agent_provider_resolver.rs` | 根据 Agent 配置解析用哪个 Provider |

### 你会看到什么

Agent 定义文件就是 Markdown + YAML front-matter。例如 sage Agent：
```yaml
---
name: sage
model: claude-sonnet
tools: [fs_read, fs_search, shell]  # 只读工具
---
你是 sage，一个只读研究助手...
```

**关键认知**：forge、sage、muse 三个 Agent 用的是**同一套代码引擎**，只是配置不同。换一套配置就是一个新 Agent。这就是 Agent 框架的本质。

---

## 第九章：会话管理 — Agent 怎么记住上下文

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_domain/src/conversation.rs` | Conversation 领域模型：ID、消息列表、元数据 |
| `crates/forge_services/src/conversation.rs` | 会话服务：CRUD 操作 |
| `crates/forge_repo/src/conversation/conversation_repo.rs` | 会话持久化（SQLite） |
| `crates/forge_repo/src/conversation/conversation_record.rs` | 数据库记录模型 |
| `crates/forge_repo/src/database/` | 数据库初始化、迁移、连接池 |
| `crates/forge_app/src/compact.rs` | 上下文压缩：对话太长时的摘要策略 |
| `crates/forge_domain/src/compact/` | 压缩配置和策略定义 |

### 你会看到什么

对话存储在 SQLite 里。每次 Agent 循环结束后，整个对话被保存。

**压缩（Compact）** 是生产级 Agent 必须解决的问题：LLM 有 token 上限，对话太长就放不下了。压缩策略是把历史消息摘要成一段简短的描述，释放 token 空间。

---

## 第十章：分层架构 — 看清全局

读到这里，你应该对核心流程有感觉了。现在退后一步看整体架构。

### 架构分层

```
┌─────────────────────────────────────────────────────┐
│  forge_main    CLI / TUI / ZSH Plugin (展示层)       │
├─────────────────────────────────────────────────────┤
│  forge_app     用例编排 / Agent Loop / 工具调度 (应用层) │
├─────────────────────────────────────────────────────┤
│  forge_services  业务逻辑 / 文件发现 / 策略 / MCP (服务层) │
├─────────────────────────────────────────────────────┤
│  forge_infra   文件系统 / HTTP / gRPC / MCP客户端 (基础设施层) │
│  forge_api     LLM API 客户端                        │
├─────────────────────────────────────────────────────┤
│  forge_repo    数据库 / Agent定义 / Provider配置 (持久层)  │
├─────────────────────────────────────────────────────┤
│  forge_domain  核心领域模型 / 接口定义 (领域层)          │
└─────────────────────────────────────────────────────┘
```

**依赖方向**：上层依赖下层，`forge_domain` 不依赖任何内部 crate。

**类比 Java**：Controller → Service → Repository 的分层，只是名字不同。

### 各 Crate 文件清单与作用

#### forge_domain — 领域模型（不依赖其他内部 crate）

| 文件 | 作用 |
|------|------|
| `agent.rs` | Agent 配置模型：ID、模型、提示词、工具白名单 |
| `message.rs` | 消息模型：System / User / Assistant / ToolCall / ToolResult |
| `chat_request.rs` | 发给 LLM 的请求封装 |
| `chat_response.rs` | LLM 返回的响应封装（支持流式） |
| `conversation.rs` | 对话模型：消息列表 + 元数据 |
| `context.rs` | 对话上下文：附加信息、初始化者标识 |
| `event.rs` | 领域事件：生命周期事件定义 |
| `model.rs` | 模型定义：ID、名称、能力（是否支持工具/视觉） |
| `model_config.rs` | 模型配置：温度、top_k、top_p 等参数 |
| `provider.rs` | LLM Provider 定义：API 地址、认证方式 |
| `tools/definition/` | 工具定义：名字、描述、参数 schema |
| `tools/call/` | 工具调用：调用请求、参数解析 |
| `tools/result.rs` | 工具结果模型 |
| `tools/catalog.rs` | 工具目录：所有内置工具的注册 |
| `skill.rs` | 技能模型：可复用的 AI 工作流 |
| `command.rs` | 命令模型：ZSH 插件的快捷命令 |
| `hook.rs` | 钩子模型：生命周期事件的处理链 |
| `snapshot.rs` | 快照模型：文件修改前后的状态记录 |
| `file_operation.rs` | 文件操作类型：创建、修改、删除 |
| `env.rs` | 环境信息模型 |
| `shell.rs` | Shell 执行模型 |
| `workspace.rs` | 语义搜索工作区模型 |
| `mcp.rs` | MCP（Model Context Protocol）基础类型 |
| `mcp_servers.rs` | MCP 服务器配置模型 |
| `error.rs` | 领域错误类型 |
| `repo.rs` | Repository trait 定义（接口） |
| `policies/` | 策略引擎：权限控制规则 |
| `compact/` | 上下文压缩配置和策略 |
| `transformer/` | 消息变换器：工具参数标准化、排序、图片处理等 |
| `attachment.rs` | 文件附件模型 |
| `auth/` | 认证模型：OAuth、API Key 等认证方式 |
| `console.rs` | 控制台 I/O trait |
| `image.rs` | 图片处理模型 |
| `reasoning.rs` | 推理努力级别模型 |
| `suggestion.rs` | Shell 命令建议模型 |
| `system_context.rs` | 系统上下文：工具描述模板配置 |
| `temperature.rs` | 温度参数模型 |
| `template.rs` | 模板配置模型 |
| `tool_order.rs` | 工具排序策略 |
| `top_k.rs` / `top_p.rs` | 采样参数模型 |
| `validation.rs` | 输入验证 |
| `xml.rs` | XML 解析工具 |
| `node.rs` / `point.rs` | 语义搜索的向量/节点模型 |
| `session_metrics.rs` | 会话指标：token 使用量统计 |
| `http_config.rs` | HTTP 客户端配置 |
| `suggestion.rs` | Shell 命令建议 |
| `terminal_context.rs` | 终端上下文信息 |

#### forge_app — 应用层（用例编排）

| 文件 | 作用 |
|------|------|
| `app.rs` | **ForgeApp**：核心入口，组装所有组件，启动编排器 |
| `orch.rs` | **Orchestrator**：Agent Loop 主循环，最核心的文件 |
| `agent_executor.rs` | Agent 作为工具被调用时的执行器（多 Agent 协作） |
| `tool_registry.rs` | 工具注册表：收集所有系统工具 + MCP 工具 |
| `tool_resolver.rs` | 工具解析：根据 Agent 配置过滤可用工具 |
| `tool_executor.rs` | 工具执行路由：分发到具体工具服务 |
| `operation.rs` | 工具操作的输入输出格式化（所有工具调用的统一出入口） |
| `system_prompt.rs` | System Prompt 组装逻辑 |
| `user_prompt.rs` | User Prompt 组装：用户输入 + 附件 + 时间等 |
| `template_engine.rs` | Handlebars 模板引擎封装 |
| `compact.rs` | 上下文压缩实现 |
| `retry.rs` | LLM 请求重试逻辑 |
| `hooks/` | 生命周期钩子实现 |
| `hooks/tracing.rs` | 请求追踪钩子 |
| `hooks/title_generation.rs` | 自动生成对话标题 |
| `hooks/compaction.rs` | 自动触发上下文压缩 |
| `hooks/doom_loop.rs` | 检测 Agent 是否陷入死循环 |
| `hooks/pending_todos.rs` | 检查未完成的 TODO 项 |
| `dto/openai/` | OpenAI API 格式的 DTO |
| `dto/anthropic/` | Anthropic API 格式的 DTO |
| `dto/google/` | Google API 格式的 DTO |
| `dto/tools_overview.rs` | 工具概览 DTO |
| `transformers/` | 消息变换器：去重、裁剪、压缩 |
| `truncation/` | 输出截断：搜索结果、Shell 输出、Fetch 结果的超长截断 |
| `fmt/` | 工具输入输出的格式化器 |
| `services.rs` | Services trait：所有服务接口的聚合 |
| `infra.rs` | Infrastructure trait：所有基础设施接口的聚合 |
| `agent_provider_resolver.rs` | Agent → Provider 的解析逻辑 |
| `agent.rs` | Agent 服务 trait |
| `changed_files.rs` | 检测对话过程中文件是否被外部修改 |
| `file_tracking.rs` | 文件追踪：记录 Agent 读写过的文件 |
| `command_generator.rs` | 自然语言转 Shell 命令 |
| `git_app.rs` | Git 相关应用逻辑（commit、diff 等） |
| `mcp_executor.rs` | MCP 工具执行器 |
| `search_dedup.rs` | 搜索结果去重 |
| `walker.rs` | 项目目录遍历 |
| `workspace_status.rs` | 语义搜索工作区状态 |
| `title_generator.rs` | 对话标题生成 |
| `error.rs` | 应用层错误类型 |
| `user.rs` | 用户信息获取 |
| `utils.rs` | 工具函数 |
| `terminal_context.rs` | 终端上下文收集 |
| `apply_tunable_parameters.rs` | 应用可调参数到 Agent |
| `init_conversation_metrics.rs` | 初始化对话指标 |
| `set_conversation_id.rs` | 设置对话 ID |
| `orch_spec/` | 编排器的集成测试 |

#### forge_services — 服务层（业务逻辑）

| 文件 | 作用 |
|------|------|
| `tool_services/fs_read.rs` | 文件读取工具实现 |
| `tool_services/fs_write.rs` | 文件写入工具实现 |
| `tool_services/fs_patch.rs` | 文件局部修改（patch）工具实现 |
| `tool_services/fs_search.rs` | 文件内容搜索（grep）工具实现 |
| `tool_services/fs_remove.rs` | 文件删除工具实现 |
| `tool_services/fs_undo.rs` | 文件操作撤销 |
| `tool_services/shell.rs` | Shell 命令执行工具实现 |
| `tool_services/fetch.rs` | HTTP 请求工具实现 |
| `tool_services/followup.rs` | 向用户追问工具实现 |
| `tool_services/skill.rs` | 技能调用工具实现 |
| `tool_services/plan_create.rs` | 计划创建工具实现 |
| `tool_services/image_read.rs` | 图片读取工具实现 |
| `tool_services/syn/` | 语义搜索工具实现 |
| `agent_registry.rs` | Agent 注册表：加载内置/自定义 Agent 定义 |
| `conversation.rs` | 对话服务：CRUD、消息追加、查找 |
| `context_engine.rs` | 上下文引擎：管理对话上下文 |
| `attachment.rs` | 附件处理：文件附件的解析和格式化 |
| `app_config.rs` | 应用配置服务 |
| `instructions.rs` | 自定义指令：读取 AGENTS.md 等 |
| `policy.rs` | 策略服务：权限控制 |
| `discovery.rs` | 文件发现：扫描项目目录结构 |
| `fd.rs` | 文件发现抽象 trait |
| `fd_git.rs` | 基于 Git 的文件发现（git ls-files） |
| `fd_walker.rs` | 基于目录遍历的文件发现 |
| `command.rs` | 命令服务：加载自定义命令 |
| `template.rs` | 模板服务 |
| `clipper.rs` | 消息裁剪：控制发送给 LLM 的内容长度 |
| `provider_service.rs` | Provider 服务：模型列表查询 |
| `provider_auth.rs` | Provider 认证服务 |
| `auth.rs` | 认证服务 |
| `mcp/manager.rs` | MCP 服务器生命周期管理 |
| `mcp/service.rs` | MCP 服务 |
| `mcp/tool.rs` | MCP 工具：从外部 MCP 服务器获取的工具 |
| `sync.rs` | 语义搜索同步服务 |
| `metadata.rs` | 元数据服务 |
| `range.rs` | 行号范围工具 |
| `forge_services.rs` | ForgeServices 结构体：所有服务的组装 |
| `utils/path.rs` | 路径处理工具 |
| `utils/temp_dir.rs` | 临时目录管理 |
| `error.rs` | 服务层错误类型 |

#### forge_infra — 基础设施层

| 文件 | 作用 |
|------|------|
| `forge_infra.rs` | **ForgeInfra**：所有基础设施实现的组装（类似 Spring @Configuration） |
| `http.rs` | HTTP 客户端：构建 reqwest 客户端实例 |
| `grpc.rs` | gRPC 客户端：语义搜索的 gRPC 连接 |
| `fs_read.rs` | 文件读取基础设施实现 |
| `fs_write.rs` | 文件写入基础设施实现 |
| `fs_create_dirs.rs` | 目录创建 |
| `fs_remove.rs` | 文件/目录删除 |
| `fs_meta.rs` | 文件元信息（大小、修改时间等） |
| `fs_read_dir.rs` | 目录遍历 |
| `executor.rs` | Shell 命令执行器 |
| `kv_storage.rs` | Key-Value 存储（基于文件） |
| `env.rs` | 环境信息获取（OS、路径等） |
| `console.rs` | 控制台 I/O 实现 |
| `inquire.rs` | 用户交互（选择、确认等） |
| `walker.rs` | 文件系统遍历器实现 |
| `mcp_client.rs` | MCP 协议客户端实现 |
| `mcp_server.rs` | MCP 服务器进程管理 |
| `auth/` | 认证基础设施：OAuth、API Key 等的具体实现 |

#### forge_repo — 持久层

| 文件 | 作用 |
|------|------|
| `forge_repo.rs` | **ForgeRepo**：所有 Repository 实现的组装 |
| `database/pool.rs` | SQLite 连接池 |
| `database/schema.rs` | 数据库表结构定义 |
| `database/migrations/` | 数据库迁移脚本 |
| `conversation/conversation_repo.rs` | 对话 Repository：SQLite 增删改查 |
| `conversation/conversation_record.rs` | 对话数据库记录模型 |
| `provider/provider_repo.rs` | Provider 配置 Repository |
| `provider/chat.rs` | 聊天 API 路由：根据 Provider 类型分发 |
| `provider/openai.rs` | OpenAI 兼容 API 调用实现 |
| `provider/anthropic.rs` | Anthropic API 调用实现 |
| `provider/google.rs` | Google Vertex AI 调用实现 |
| `provider/bedrock.rs` | AWS Bedrock 调用实现 |
| `provider/retry.rs` | Provider 级别的请求重试 |
| `provider/event.rs` | SSE 流式响应解析 |
| `provider/utils.rs` | Provider 工具函数 |
| `agent.rs` | Agent 定义的加载和解析 |
| `agent_definition.rs` | Agent 定义文件（Markdown + YAML）的解析 |
| `agents/` | 内置 Agent 定义文件目录 |
| `skill.rs` | 技能定义的加载和解析 |
| `skills/` | 内置技能定义文件目录 |
| `context_engine.rs` | 上下文引擎的 Repository 实现 |
| `fs_snap.rs` | 文件快照管理 |
| `fuzzy_search.rs` | 模糊搜索实现 |
| `validation.rs` | 数据验证 |

#### forge_api — LLM API 客户端

| 文件 | 作用 |
|------|------|
| `api.rs` | API trait 定义 |
| `forge_api.rs` | ForgeAPI 实现：组装 Repo/Infra/Services，实现 trait |

#### forge_main — CLI / TUI 入口

| 文件 | 作用 |
|------|------|
| `main.rs` | 程序入口 |
| `cli.rs` | CLI 参数定义（clap derive） |
| `ui.rs` | **UI** 主控：管理交互式会话的生命周期 |
| `state.rs` | 应用状态：当前对话、Agent、模型等 |
| `prompt.rs` | 交互式提示符管理 |
| `input.rs` | 用户输入处理 |
| `stream_renderer.rs` | 流式响应渲染：实时显示 LLM 输出 |
| `banner.rs` | 启动横幅（ASCII Art） |
| `completer/` | Tab 补全：命令补全、文件搜索 |
| `highlighter.rs` | 输入语法高亮 |
| `editor.rs` | 外部编辑器集成 |
| `porcelain.rs` | 机器可读输出格式（供 ZSH 插件使用） |
| `sandbox.rs` | 沙箱模式：创建隔离的 git worktree |
| `zsh/` | ZSH 插件相关逻辑 |
| `zsh/plugin.rs` | ZSH 插件：`:` 命令的拦截和分发 |
| `zsh/rprompt.rs` | ZSH 右侧提示符：显示 token 用量和费用 |
| `conversation_selector.rs` | 对话选择器（fzf 风格） |
| `tools_display.rs` | 工具列表显示 |
| `title_display.rs` | 对话标题显示 |
| `info.rs` | `forge info` 命令实现 |
| `model.rs` | 模型相关 CLI 逻辑 |
| `tracker.rs` | 遥测初始化 |
| `update.rs` | 自动更新检查 |
| `oauth_callback.rs` | OAuth 回调服务器 |
| `vscode.rs` | VS Code 集成 |

#### forge_config — 配置管理

| 文件 | 作用 |
|------|------|
| `config.rs` | ForgeConfig 主结构体 |
| `reader.rs` | 配置文件读取 |
| `writer.rs` | 配置文件写入 |
| `model.rs` | 模型配置 |
| `retry.rs` | 重试配置 |
| `http.rs` | HTTP 客户端配置 |
| `compact.rs` | 上下文压缩配置 |
| `reasoning.rs` | 推理努力配置 |
| `decimal.rs` | 小数类型配置 |
| `percentage.rs` | 百分比类型配置 |
| `legacy.rs` | 旧版配置迁移 |
| `auto_dump.rs` | 自动导出配置 |
| `error.rs` | 配置错误类型 |

#### 辅助 Crate

| Crate | 关键文件 | 作用 |
|-------|---------|------|
| `forge_stream` | `mpsc_stream.rs` | 异步流封装：将 mpsc channel 包装成 Stream |
| `forge_markdown_stream` | `renderer.rs`, `code.rs`, `table.rs` | 流式 Markdown 渲染器：边收边渲染 |
| `forge_snaps` | `service.rs` | 文件快照服务：记录修改前后的文件状态 |
| `forge_display` | `diff.rs`, `code.rs`, `markdown.rs`, `grep.rs` | 终端显示：diff、代码高亮、Markdown、搜索结果 |
| `forge_walker` | `walker.rs` | 文件系统遍历：按规则扫描项目文件 |
| `forge_select` | `select.rs`, `multi.rs`, `confirm.rs` | 交互式选择 UI |
| `forge_template` | `element.rs` | Handlebars 模板封装 |
| `forge_tracker` | `dispatch.rs`, `event.rs` | 遥测：事件收集和发送到 PostHog |
| `forge_json_repair` | `parser.rs` | JSON 修复：LLM 输出的 JSON 可能不合法，这里做修复 |
| `forge_embed` | `lib.rs` | 编译时嵌入资源文件（模板、Agent 定义等） |
| `forge_tool_macros` | (proc-macro crate) | 工具定义的派生宏 |
| `forge_test_kit` | (test utilities) | 测试工具和 fixture |
| `forge_ci` | (CI generation) | CI 工作流自动生成 |

---

## 第十一章：组装 — DI 怎么做的

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_infra/src/forge_infra.rs` | **ForgeInfra**：基础设施层的组装（类似 @Configuration） |
| `crates/forge_services/src/forge_services.rs` | **ForgeServices**：服务层的组装 |
| `crates/forge_repo/src/forge_repo.rs` | **ForgeRepo**：持久层的组装 |
| `crates/forge_api/src/forge_api.rs` | **ForgeAPI**：把 Repo + Infra + Services 组装成最终 API |
| `crates/forge_app/src/infra.rs` | `Infrastructure` trait：定义所有基础设施能力的聚合接口 |

### 你会看到什么

没有 Spring，没有 DI 容器。手动用 `Arc<T>` 组装：

```rust
// 伪代码示例
let infra = Arc::new(ForgeInfra::new(...));
let repo = Arc::new(ForgeRepo::new(infra.clone()));
let services = Arc::new(ForgeServices::new(repo.clone(), infra.clone()));
let api = ForgeAPI::new(services, infra);
```

每一层都实现一组 trait，上层通过 trait 引用下层，不直接依赖具体类型。

---

## 第十二章：MCP — 外部工具扩展

### 要读的文件

| 文件 | 作用 |
|------|------|
| `crates/forge_domain/src/mcp.rs` | MCP 基础类型 |
| `crates/forge_domain/src/mcp_servers.rs` | MCP 服务器配置模型 |
| `crates/forge_infra/src/mcp_client.rs` | MCP 客户端：连接外部 MCP 服务器 |
| `crates/forge_infra/src/mcp_server.rs` | MCP 服务器进程管理（启动/停止） |
| `crates/forge_services/src/mcp/manager.rs` | MCP 生命周期管理 |
| `crates/forge_services/src/mcp/service.rs` | MCP 服务层 |
| `crates/forge_services/src/mcp/tool.rs` | MCP 工具：从外部服务器获取的工具定义 |

### MCP 是什么

MCP（Model Context Protocol）是一个让 Agent 调用外部工具的协议。Forge 自己有内置工具（文件读写等），但用户可以通过 MCP 接入任意外部工具（浏览器、数据库、API 等）。

---

## 第十三章：高级主题

读完了核心流程，可以按兴趣选读：

| 主题 | 文件 | 你会学到 |
|------|------|---------|
| 多 Agent 协作 | `forge_app/src/agent_executor.rs` | Agent A 调用 Agent B，子 Agent 有独立的对话和工具集 |
| 上下文压缩 | `forge_app/src/compact.rs` | 对话太长时的摘要策略，生产级 Agent 必备 |
| 死循环检测 | `forge_app/src/hooks/doom_loop.rs` | 检测 Agent 是否在重复做同样的事 |
| 策略引擎 | `forge_domain/src/policies/` | 权限控制：哪些文件可读、哪些命令可执行 |
| 流式渲染 | `forge_main/src/stream_renderer.rs` + `forge_markdown_stream/` | 边收边渲染 LLM 的流式输出 |
| 文件快照/撤销 | `forge_snaps/src/service.rs` + `forge_services/src/tool_services/fs_undo.rs` | 记录每次修改，支持撤销 |
| ZSH 插件 | `forge_main/src/zsh/plugin.rs` | Shell 级别的命令拦截 |
| 语义搜索 | `forge_services/src/tool_services/syn/` + `forge_services/src/sync.rs` | 用向量搜索代码，而不是文本匹配 |
| 命令建议 | `forge_app/src/command_generator.rs` | 自然语言转 Shell 命令 |

---

## 附录：完整请求流程图

```
用户输入 "帮我重构 auth 模块"
         │
         ▼
    ┌─ main.rs ─────────────────────────────────────┐
    │ UI 收到输入，调用 ForgeApp.chat()              │
    └────────────────────────────────────────────────┘
         │
         ▼
    ┌─ app.rs ──────────────────────────────────────┐
    │ 1. 加载对话历史 (forge_repo/conversation)       │
    │ 2. 获取 Agent 配置 (forge_repo/agents/forge.md) │
    │ 3. 获取可用工具 (forge_app/tool_registry)       │
    │ 4. 拼装 System Prompt (forge_app/system_prompt) │
    │ 5. 拼装 User Prompt (forge_app/user_prompt)     │
    │ 6. 创建 Orchestrator                            │
    └────────────────────────────────────────────────┘
         │
         ▼
    ┌─ orch.rs: Agent Loop ──────────────────────────┐
    │                                                  │
    │  ┌─ 第 1 轮 ──────────────────────────────────┐  │
    │  │ 发送消息给 LLM (forge_repo/provider/chat)   │  │
    │  │   → HTTP POST 到 OpenAI/Anthropic API      │  │
    │  │   → 解析 SSE 流式响应                       │  │
    │  │                                            │  │
    │  │ LLM 返回: "我要先读取文件"                   │  │
    │  │   + ToolCall { fs_read, path: "src/auth" }  │  │
    │  │                                            │  │
    │  │ 执行工具 (forge_app/tool_executor)          │  │
    │  │   → 路由到 forge_services/tool_services     │  │
    │  │   → fs_read 读取文件内容                     │  │
    │  │   → 调用 forge_infra/fs_read 做实际 I/O     │  │
    │  │                                            │  │
    │  │ 结果追加到消息历史                            │  │
    │  └────────────────────────────────────────────┘  │
    │                    │                              │
    │                    ▼                              │
    │  ┌─ 第 2 轮 ──────────────────────────────────┐  │
    │  │ 再次发送消息给 LLM（带着工具结果）            │  │
    │  │                                            │  │
    │  │ LLM 返回: "我要修改这个函数"                  │  │
    │  │   + ToolCall { fs_patch, path, content }    │  │
    │  │                                            │  │
    │  │ 执行工具 → fs_patch 写入修改                  │  │
    │  │ 结果追加到消息历史                            │  │
    │  └────────────────────────────────────────────┘  │
    │                    │                              │
    │                    ▼                              │
    │  ┌─ 第 3 轮 ──────────────────────────────────┐  │
    │  │ 发送消息给 LLM                               │  │
    │  │                                            │  │
    │  │ LLM 返回最终回答（无工具调用）                 │  │
    │  │ "重构完成，主要改动如下..."                    │  │
    │  └────────────────────────────────────────────┘  │
    │                                                  │
    │ 保存对话到 SQLite                                 │
    └──────────────────────────────────────────────────┘
         │
         ▼
    流式渲染输出到终端 (forge_main/stream_renderer)
```

---

## 推荐阅读顺序总结

| 阶段 | 模块 | 目标 |
|------|------|------|
| 1 | `forge_domain/message.rs`, `chat_request.rs`, `chat_response.rs` | 理解消息模型 |
| 2 | `forge_domain/tools/` | 理解工具定义 |
| 3 | `forge_app/orch.rs` | **理解 Agent Loop（最核心）** |
| 4 | `forge_app/app.rs` | 理解组装流程 |
| 5 | `forge_app/tool_registry.rs`, `tool_executor.rs`, `forge_services/tool_services/` | 理解工具实现 |
| 6 | `forge_app/system_prompt.rs` | 理解 System Prompt |
| 7 | `forge_repo/provider/` | 理解 LLM API 调用 |
| 8 | `forge_repo/agents/` | 理解 Agent 配置 |
| 9 | `forge_services/conversation.rs`, `forge_app/compact.rs` | 理解会话管理 |
| 10 | `forge_infra/forge_infra.rs`, `forge_services/forge_services.rs`, `forge_api/forge_api.rs` | 理解分层和组装 |
| 11 | `forge_services/mcp/` | 理解 MCP 扩展 |
| 12 | `forge_main/ui.rs`, `stream_renderer.rs`, `zsh/` | 理解 TUI 和插件 |
