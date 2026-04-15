"""Installed agent implementation for benchmarking the Forge CLI agent."""

import json
import os
import shlex
import uuid
from pathlib import Path
from typing import Any

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trajectories import (
    Agent,
    FinalMetrics,
    Metrics,
    Observation,
    ObservationResult,
    Step,
    ToolCall,
    Trajectory,
)


class ForgeAgent(BaseInstalledAgent):
    SUPPORTS_ATIF: bool = True

    FORGE_HOST_CONFIG_DIR = Path.home() / "forge"
    FORGE_CONTAINER_CONFIG_DIR = "/root/forge"

    @classmethod
    def get_forge_host_binary(cls) -> Path:
        """Get path to forge binary, from FORGE_BIN env var or default location."""
        if "FORGE_BIN" in os.environ:
            return Path(os.environ["FORGE_BIN"])
        return Path.cwd() / "target" / "release" / "forge"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._conversation_id: str | None = None

    @staticmethod
    def name() -> str:
        return "forge"

    # ------------------------------------------------------------------
    # Trajectory conversion helpers
    # ------------------------------------------------------------------

    def _convert_event_to_step(self, event: dict[str, Any], step_id: int) -> Step:
        """Convert a normalized Forge event dictionary into an ATIF step."""
        kind = event.get("kind")
        timestamp = event.get("timestamp")

        if kind == "message":
            role = event.get("role", "user")
            text = event.get("text", "")
            reasoning = event.get("reasoning")
            metrics = event.get("metrics")
            extra = event.get("extra")
            model_name = event.get("model_name")

            source = {"assistant": "agent", "user": "user"}.get(role, "system")

            step = Step(
                step_id=step_id,
                timestamp=timestamp,
                source=source,
                message=text,
            )

            if source == "agent":
                if reasoning:
                    step.reasoning_content = reasoning
                if model_name:
                    step.model_name = model_name
                elif self.model_name:
                    step.model_name = self.model_name

            if metrics:
                step.metrics = metrics
            if extra:
                step.extra = extra

            return step

        if kind == "tool_call":
            call_id = event.get("call_id")
            tool_name = event.get("tool_name")
            if not call_id or not tool_name:
                raise ValueError("Tool call event missing call_id or tool_name")

            arguments = event.get("arguments") or {}
            raw_arguments = event.get("raw_arguments")
            reasoning = event.get("reasoning")
            metrics = event.get("metrics")
            extra = event.get("extra")
            status = event.get("status")
            message = event.get("message")
            output = event.get("output")
            metadata = event.get("metadata")
            model_name = event.get("model_name") or self.model_name

            observation = (
                Observation(
                    results=[ObservationResult(source_call_id=call_id, content=output)]
                )
                if output is not None
                else None
            )

            extra = extra or {}
            for key, value in {"metadata": metadata, "raw_arguments": raw_arguments, "status": status}.items():
                if value is not None:
                    extra.setdefault(key, value)

            if not message:
                message = f"Executed {' '.join(p for p in [tool_name, call_id] if p) or 'Tool call'}"

            step = Step(
                step_id=step_id,
                timestamp=timestamp,
                source="agent",
                message=message,
                tool_calls=[ToolCall(tool_call_id=call_id, function_name=tool_name, arguments=arguments)],
                observation=observation,
            )

            if model_name:
                step.model_name = model_name
            if reasoning:
                step.reasoning_content = reasoning
            if metrics:
                step.metrics = metrics
            if extra:
                step.extra = extra

            return step

        raise ValueError(f"Unsupported event kind '{kind}'")

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    @classmethod
    def _extract_text_reasoning_tool_uses(
        cls, content: Any
    ) -> tuple[str, str | None, list[dict[str, Any]]]:
        if isinstance(content, str):
            return content.strip(), None, []

        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_blocks: list[dict[str, Any]] = []

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    text_parts.append(cls._stringify(block))
                    continue

                block_type = block.get("type")
                if block_type == "tool_use":
                    tool_blocks.append(block)
                    continue

                if block_type in {"thinking", "reasoning", "analysis"}:
                    text_value = block.get("text")
                    reasoning_parts.append(
                        text_value.strip() if isinstance(text_value, str) else cls._stringify(text_value)
                    )
                    continue

                if block_type == "code" and isinstance(block.get("code"), str):
                    text_parts.append(block["code"])
                    continue

                text_value = block.get("text")
                text_parts.append(text_value if isinstance(text_value, str) else cls._stringify(block))
        elif content is not None:
            text_parts.append(cls._stringify(content))

        join = lambda parts: "\n\n".join(p.strip() for p in parts if p and str(p).strip())  # noqa: E731
        return join(text_parts), (join(reasoning_parts) or None), tool_blocks

    @staticmethod
    def _build_metrics(usage: Any) -> Metrics | None:
        if not isinstance(usage, dict):
            return None

        cached_tokens = usage.get("cache_read_input_tokens", 0)
        prompt_tokens = usage.get("input_tokens", 0) + cached_tokens
        completion_tokens = usage.get("output_tokens", 0)

        extra = {k: v for k, v in usage.items() if k not in {"input_tokens", "output_tokens"}}

        if prompt_tokens is None and completion_tokens is None and cached_tokens is None and not extra:
            return None

        return Metrics(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
            cost_usd=None,
            extra=extra or None,
        )

    def _convert_forge_conversation_to_trajectory(
        self, conversation_data: dict[str, Any]
    ) -> Trajectory | None:
        """Convert Forge conversation JSON into an ATIF trajectory."""
        context = conversation_data.get("context")
        if not context:
            print("No context found in Forge conversation")
            return None

        messages = context.get("messages", [])
        if not messages:
            print("No messages found in Forge conversation context")
            return None

        session_id = conversation_data.get("id", str(uuid.uuid4()))
        default_model_name = self.model_name

        for msg in messages:
            if isinstance(msg, dict):
                model = msg.get("model")
                if isinstance(model, str) and model:
                    default_model_name = model
                    break

        normalized_events: list[dict[str, Any]] = []
        pending_calls: dict[str, dict[str, Any]] = {}

        for msg in messages:
            if not isinstance(msg, dict):
                continue

            msg_type = msg.get("type") or (list(msg.keys())[0] if msg else None)
            msg_content = msg.get(msg_type, msg) if msg_type else msg
            timestamp = msg.get("timestamp")

            if msg_type == "text" or (isinstance(msg_content, dict) and "content" in msg_content):
                content_data = msg_content if isinstance(msg_content, dict) else msg
                role = content_data.get("role", "user")
                content = content_data.get("content", "")

                text, reasoning, tool_blocks = self._extract_text_reasoning_tool_uses(content)
                usage = content_data.get("usage")
                metrics = self._build_metrics(usage) if usage else None
                model_name = content_data.get("model") or default_model_name
                extra: dict[str, Any] = {"model": model_name} if content_data.get("model") else {}

                if text or reasoning or not tool_blocks:
                    normalized_events.append({
                        "kind": "message",
                        "timestamp": timestamp,
                        "role": role,
                        "text": text or "",
                        "reasoning": reasoning if role == "assistant" else None,
                        "metrics": metrics,
                        "extra": extra or None,
                        "model_name": model_name,
                    })
                    metrics = None

                for idx, tool_block in enumerate(tool_blocks):
                    call_id = tool_block.get("id") or tool_block.get("tool_call_id") or f"call_{uuid.uuid4().hex[:8]}"
                    raw_arguments = tool_block.get("input") or tool_block.get("arguments")
                    arguments = raw_arguments if isinstance(raw_arguments, dict) else {"input": raw_arguments}

                    pending_calls[call_id] = {
                        "kind": "tool_call",
                        "timestamp": timestamp,
                        "call_id": call_id,
                        "tool_name": tool_block.get("name") or "",
                        "arguments": arguments or {},
                        "raw_arguments": raw_arguments,
                        "reasoning": reasoning,
                        "status": tool_block.get("status"),
                        "message": None,
                        "extra": extra.copy() if extra else None,
                        "metrics": metrics if idx == 0 and metrics is not None else None,
                        "model_name": model_name,
                    }
                    if idx == 0 and metrics is not None:
                        metrics = None

            elif msg_type == "tool" or (isinstance(msg_content, dict) and "tool_call_id" in msg_content):
                tool_data = msg_content if isinstance(msg_content, dict) else msg
                call_id = tool_data.get("tool_call_id") or tool_data.get("id")
                output = tool_data.get("output") or tool_data.get("content")

                call_info = (pending_calls.pop(call_id, None) if call_id else None) or {
                    "kind": "tool_call",
                    "timestamp": timestamp,
                    "call_id": call_id or f"call_{uuid.uuid4().hex[:8]}",
                    "tool_name": tool_data.get("name") or "",
                    "arguments": {},
                    "raw_arguments": None,
                    "reasoning": None,
                    "status": None,
                    "message": None,
                    "extra": None,
                    "metrics": None,
                    "model_name": default_model_name,
                }

                if output:
                    if isinstance(output, dict):
                        values = output.get("values", [])
                        parts = [
                            v.get("content", "") if isinstance(v, dict) and v.get("type") == "text" else v
                            for v in values if isinstance(v, (dict, str))
                        ]
                        output = "\n".join(str(p) for p in parts) if parts else json.dumps(output)
                    elif not isinstance(output, str):
                        output = self._stringify(output)

                call_info["output"] = output
                call_info["timestamp"] = call_info.get("timestamp") or timestamp
                normalized_events.append(call_info)

        for leftover in pending_calls.values():
            normalized_events.append(leftover)

        steps: list[Step] = []
        for idx, norm_event in enumerate(normalized_events, start=1):
            try:
                step = self._convert_event_to_step(norm_event, idx)
            except ValueError as exc:
                print(f"Skipping event during step conversion: {exc}")
                continue
            if step.source == "agent" and not step.model_name and default_model_name:
                step.model_name = default_model_name
            steps.append(step)

        if not steps:
            print("No valid steps produced from Forge conversation")
            return None

        total_prompt = sum(s.metrics.prompt_tokens for s in steps if s.metrics and s.metrics.prompt_tokens is not None) or None
        total_completion = sum(s.metrics.completion_tokens for s in steps if s.metrics and s.metrics.completion_tokens is not None) or None
        total_cached = sum(s.metrics.cached_tokens for s in steps if s.metrics and s.metrics.cached_tokens is not None) or None

        accumulated = conversation_data.get("accumulated_usage")
        total_cost = accumulated.get("cost") if accumulated else None

        return Trajectory(
            schema_version="ATIF-v1.2",
            session_id=session_id,
            agent=Agent(name="forge", version="unknown", model_name=default_model_name, extra=None),
            steps=steps,
            final_metrics=FinalMetrics(
                total_prompt_tokens=total_prompt,
                total_completion_tokens=total_completion,
                total_cached_tokens=total_cached,
                total_cost_usd=total_cost,
                total_steps=len(steps),
                extra=None,
            ),
        )

    def _convert_events_to_trajectory(self, session_dir: Path) -> Trajectory | None:
        """Convert Forge session logs into an ATIF trajectory."""
        conversation_files = list(session_dir.glob("conversation*.json")) or list(session_dir.glob("*.json"))

        if not conversation_files:
            print(f"No Forge conversation files found in {session_dir}")
            return None

        conversation_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        for conv_file in conversation_files:
            try:
                with open(conv_file, "r") as handle:
                    data = json.load(handle)
                trajectory = self._convert_forge_conversation_to_trajectory(data)
                if trajectory:
                    return trajectory
            except (json.JSONDecodeError, OSError) as exc:
                print(f"Failed to parse conversation file {conv_file}: {exc}")

        return None

    # ------------------------------------------------------------------
    # Environment variable helpers
    # ------------------------------------------------------------------

    def _get_forge_env(self) -> dict[str, str]:
        """Build environment variables for forge execution."""
        env: dict[str, str] = {}

        # Provider / model overrides
        provider = os.environ.get("FORGE_OVERRIDE_PROVIDER") or os.environ.get("DEFAULT_PROVIDER")
        if provider:
            env["FORGE_OVERRIDE_PROVIDER"] = provider

        model = (
            os.environ.get("FORGE_OVERRIDE_MODEL")
            or os.environ.get("DEFAULT_MODEL")
            or self.model_name
        )
        if model:
            env["FORGE_OVERRIDE_MODEL"] = model

        # API keys and URL parameters
        passthrough = [
            "FORGE_API_KEY",
            "ANTHROPIC_API_KEY",
            "CLAUDE_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
            "DEEPSEEK_API_KEY",
            "GITHUB_COPILOT_API_KEY",
            "REQUESTY_API_KEY",
            "XAI_API_KEY",
            "CEREBRAS_API_KEY",
            "ZAI_API_KEY",
            "ZAI_CODING_API_KEY",
            "BIG_MODEL_API_KEY",
            "VERTEX_AI_AUTH_TOKEN",
            "AZURE_API_KEY",
            "LLAMA_CPP_API_KEY",
            "VLLM_API_KEY",
            "JAN_AI_API_KEY",
            "OLLAMA_API_KEY",
            "LM_STUDIO_API_KEY",
            "IO_INTELLIGENCE_API_KEY",
            "OPENAI_URL",
            "ANTHROPIC_URL",
            "PROJECT_ID",
            "LOCATION",
            "AZURE_RESOURCE_NAME",
            "AZURE_DEPLOYMENT_NAME",
            "AZURE_API_VERSION",
            "LLAMA_CPP_URL",
            "LLAMA_CPP_PORT",
            "VLLM_URL",
            "VLLM_PORT",
            "JAN_AI_URL",
            "JAN_AI_PORT",
            "OLLAMA_URL",
            "OLLAMA_PORT",
            "LM_STUDIO_URL",
            "LM_STUDIO_PORT",
            "FORGE_WORKSPACE_SERVER_URL",
            "AWS_REGION",
        ]
        for key in passthrough:
            if key in os.environ:
                env[key] = os.environ[key]

        return env

    # ------------------------------------------------------------------
    # Harbor lifecycle hooks
    # ------------------------------------------------------------------

    async def install(self, environment: BaseEnvironment) -> None:
        """Upload forge binary and configure it in the container."""
        await environment.upload_file(
            source_path=self.get_forge_host_binary(),
            target_path="/usr/local/bin/forge",
        )
        await self.exec_as_root(environment, command="chmod +x /usr/local/bin/forge")
        await self.exec_as_root(environment, command=f"mkdir -p {self.FORGE_CONTAINER_CONFIG_DIR}")

        config_file = self.FORGE_HOST_CONFIG_DIR / ".config.json"
        credentials_file = self.FORGE_HOST_CONFIG_DIR / ".credentials.json"

        if config_file.exists():
            await environment.upload_file(
                source_path=config_file,
                target_path=f"{self.FORGE_CONTAINER_CONFIG_DIR}/.config.json",
            )
        if credentials_file.exists():
            await environment.upload_file(
                source_path=credentials_file,
                target_path=f"{self.FORGE_CONTAINER_CONFIG_DIR}/.credentials.json",
            )

        await self.exec_as_root(
            environment,
            command='/usr/local/bin/forge --version || echo "Forge installed"',
        )

    def populate_context_post_run(self, context: AgentContext) -> None:
        """Write trajectory JSON and populate token/cost metrics on the context."""
        session_dir = self.logs_dir / "agent"
        if not session_dir.exists():
            print(f"No forge session directory found at {session_dir}")
            return

        try:
            trajectory = self._convert_events_to_trajectory(session_dir)
        except Exception as exc:
            print(f"Failed to convert Forge events to trajectory: {exc}")
            return

        if not trajectory:
            print("Failed to convert Forge session to trajectory")
            return

        trajectory_path = self.logs_dir / "trajectory.json"
        try:
            with open(trajectory_path, "w", encoding="utf-8") as handle:
                json.dump(trajectory.to_json_dict(), handle, indent=2, ensure_ascii=False)
            print(f"Wrote Forge trajectory to {trajectory_path}")
        except OSError as exc:
            print(f"Failed to write trajectory file {trajectory_path}: {exc}")

        if trajectory.final_metrics:
            m = trajectory.final_metrics
            context.cost_usd = m.total_cost_usd
            context.n_input_tokens = m.total_prompt_tokens or 0
            context.n_cache_tokens = m.total_cached_tokens or 0
            context.n_output_tokens = m.total_completion_tokens or 0

    async def run(
        self, instruction: str, environment: BaseEnvironment, context: AgentContext
    ) -> None:
        """Run forge with the given instruction."""
        env = self._get_forge_env()
        self._conversation_id = str(uuid.uuid4())
        env["_FORGE_CONVERSATION_ID"] = self._conversation_id

        await self.exec_as_agent(environment, command="/usr/local/bin/forge workspace sync", env=env)
        await self.exec_as_agent(
            environment,
            command=(
                f"/usr/local/bin/forge --verbose "
                f"--conversation-id {self._conversation_id} "
                f"-p {shlex.quote(instruction)} 2>&1 | tee /logs/agent/forge-output.txt"
            ),
            env=env,
        )
