# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Forge (forgecode) is an AI-powered coding agent that runs in the terminal, built in Rust. It supports interactive TUI mode, one-shot CLI mode (`-p`), and a ZSH plugin (`:` prefix commands). It integrates with multiple LLM providers (OpenAI, Anthropic, OpenRouter, Google Vertex, etc.) and supports MCP servers, semantic search, custom agents, and skills.

## Build & Development Commands

```bash
# Quick check (fastest verification)
cargo check

# Run tests (uses cargo-nextest via insta config)
cargo insta test --accept

# Run tests for a specific crate
cargo insta test -p forge_domain --accept
cargo insta test -p forge_app --accept

# Build (debug)
cargo build

# Build release binary (slow, only when needed)
cargo build --release

# Format check
cargo fmt --check

# Lint
cargo clippy -- -D warnings

# Run the CLI locally
cargo run -- <args>
# e.g. cargo run -- -p "explain this codebase"
```

- Rust edition 2024, toolchain 1.92 (see `rust-toolchain.toml`)
- Tests use **insta** for snapshot testing with **nextest** as the runner (configured in `insta.yaml`)
- CI uses `cargo llvm-cov --all-features --workspace` for coverage

## Architecture

The codebase is a Cargo workspace with 23 crates following clean architecture layers:

### Domain Layer
- **`forge_domain`** — Core domain types: `Agent`, `Conversation`, `Message`, `Model`, `Provider`, `Tool`, `Skill`, `Command`, `Event`, `Context`, `Snapshot`, `Workspace`, etc. Defines repository traits (`forge_domain::repo::*`) that infrastructure implements.

### Application Layer
- **`forge_app`** — Use cases and orchestration. Contains `App` (the main application entry), `AgentExecutor` (runs agent loops), `ToolExecutor`/`ToolRegistry` (tool dispatch), `Orch` (orchestration), system prompt assembly, conversation compaction, retry logic, and hook processing.

### Infrastructure Layer
- **`forge_infra`** — Concrete implementations of domain traits: filesystem operations (`fs_read`, `fs_write`, `fs_create_dirs`, `fs_remove`), HTTP client, gRPC, MCP client/server, key-value storage, environment config, console I/O.
- **`forge_api`** — LLM provider API client. Handles streaming chat completions via provider-specific HTTP endpoints.

### Services Layer
- **`forge_services`** — Business logic services: agent registry, conversation management, attachment handling, context engine, file discovery (`fd`, `fd_git`, `fd_walker`), policy enforcement, template rendering, tool services, MCP lifecycle, provider auth, and sync.

### Data/Persistence Layer
- **`forge_repo`** — Repository implementations: conversation storage (SQLite via `database/`), agent definitions (`agents/`), skill definitions (`skills/`), provider credential storage, fuzzy search, filesystem snapshots.

### Presentation Layer
- **`forge_main`** — CLI entry point, TUI (reedline-based), banner, prompt handling, shell integration, ZSH plugin, completer, syntax highlighting, porcelain output, sandbox mode.

### Supporting Crates
- **`forge_config`** — Configuration file parsing (`forge.yaml`, `.forge.toml`)
- **`forge_display`** — Terminal display/rendering utilities
- **`forge_stream`** — Async stream combinators
- **`forge_markdown_stream`** — Streaming markdown parser
- **`forge_snaps`** — Snapshot diff/patch engine
- **`forge_spinner`** — Terminal spinner
- **`forge_template`** — Handlebars template engine
- **`forge_tool_macros`** — Proc macros for tool definitions
- **`forge_tracker`** — Telemetry/analytics (PostHog)
- **`forge_walker`** — Filesystem traversal for context gathering
- **`forge_embed`** — Embedded resources (templates, agents, skills)
- **`forge_select`** — Interactive selection UI (fzf-like)
- **`forge_json_repair`** — JSON repair for malformed LLM output
- **`forge_test_kit`** — Test utilities and fixtures
- **`forge_ci`** — CI workflow generation (gh-workflow)

### Dependency Flow

```
forge_main → forge_app → forge_services → forge_infra → forge_api
                ↓              ↓
          forge_domain    forge_repo
```

`forge_domain` has no internal crate dependencies. Services depend on domain traits and infrastructure abstractions, never on other services directly.

## Key Patterns

### Service Pattern
Services take at most one generic type parameter for infrastructure, stored as `Arc<T>`. Use tuple struct pattern for simple services: `struct FileService<F>(Arc<F>)`. Constructor `new()` has no type bounds; bounds are applied only on methods that need them. Compose multiple trait bounds with `+`. Never use `Box<dyn Trait>`.

### Testing Pattern
Tests live in the same file as source code (using `#[cfg(test)]` modules). Use `pretty_assertions::assert_eq!`. Structure tests as: setup/fixture → execute → expected → assert. Use `fixture`, `actual`, `expected` naming. Use `derive_setters` for test data construction.

### Error Handling
Use `anyhow::Result` in services/repositories. Create domain errors with `thiserror`. Never implement `From` for converting domain errors — convert manually.

### Domain Types
Use `derive_setters` with `strip_option` and `into` attributes on struct types.

## Configuration & Data Files
- Agent definitions: `crates/forge_repo/src/agents/` (Markdown with YAML front-matter)
- Skills: `crates/forge_repo/src/skills/` (same format)
- Templates: `templates/` (Handlebars `.md` files)
- User config: `~/forge/.forge.toml` (runtime), `forge.yaml` (project)
- Provider credentials: managed via `forge provider login`
- MCP config: `.mcp.json` (project) or `~/forge/.mcp.json` (global)

## Git Conventions
- Use `Co-Authored-By: ForgeCode <noreply@forgecode.dev>` for commits
- CI workflows are auto-generated by `forge_ci` — do not edit `.github/workflows/*.yml` by hand
- Labels: `ci: build all targets` triggers cross-platform release builds on PRs
