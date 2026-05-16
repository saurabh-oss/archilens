"""
AI-powered analysis using LLMs for capabilities beyond static analysis.

Handles:
- Process flow inference from controller/handler code
- Natural-language annotations for diagram nodes
- Business capability mapping suggestions
- Architectural pattern detection
- Module responsibility summaries
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from archilens.config import AIConfig
from archilens.models import (
    ArchNode,
    ArchPattern,
    ArchSnapshot,
    FlowStep,
    ProcessFlow,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

PROCESS_FLOW_PROMPT = """You are an expert software architect analyzing source code to extract runtime process flows.

Given the following source code from an entry point (controller/handler/route), identify the end-to-end request lifecycle.

**File:** {file_path}
**Entry Point Type:** {entry_type}

```{language}
{code}
```

Additional context - files that this entry point imports or calls:
{dependency_context}

Analyze this code and return a JSON array of process flow steps. Each step should have:
- "order": integer sequence number
- "actor": the component/service performing the action (use the class/module name)
- "action": what it does (e.g., "Validates request body", "Queries database")
- "target": what it interacts with (another service, database, external API, etc.)
- "data": what data is exchanged (optional)
- "is_async": boolean, whether the step is asynchronous
- "condition": guard condition if this is a conditional branch (optional)

Also provide:
- "flow_name": a descriptive name for this flow (e.g., "Create Order Flow")
- "trigger": what initiates this flow (e.g., "POST /api/orders")
- "description": one-sentence summary

Return ONLY valid JSON, no markdown fences. Structure:
{{
  "flow_name": "...",
  "trigger": "...",
  "description": "...",
  "steps": [...]
}}"""

MODULE_SUMMARY_PROMPT = """You are an expert software architect. Given the following module information, provide a concise one-sentence summary of its responsibility.

**Module:** {module_name}
**Files:**
{file_list}

**Key classes/functions found:**
{symbols}

**Dependencies (imports from other modules):**
{dependencies}

Return ONLY a JSON object:
{{
  "summary": "One sentence describing what this module does",
  "responsibility": "Primary responsibility in 3-5 words",
  "suggested_capability": "Business capability this maps to (e.g., 'Order Management', 'Authentication')"
}}"""

PATTERN_DETECTION_PROMPT = """You are an expert software architect. Given the following architecture overview, identify which architectural patterns are present.

**Project:** {project_name}
**Modules and their relationships:**
{module_graph}

**Directory structure:**
{directory_structure}

Identify which of these patterns are present:
- mvc (Model-View-Controller)
- hexagonal (Ports and Adapters)
- layered (Traditional layered architecture)
- microservices (Independent deployable services)
- event_driven (Event/message-based communication)
- cqrs (Command Query Responsibility Segregation)
- repository (Repository pattern for data access)
- factory (Factory pattern)
- observer (Observer/pub-sub pattern)
- middleware_chain (Middleware/pipeline pattern)
- saga (Saga pattern for distributed transactions)
- clean_architecture (Clean Architecture / Onion)

Return ONLY a JSON object:
{{
  "patterns": ["pattern1", "pattern2"],
  "reasoning": {{
    "pattern1": "Brief explanation of why this pattern was detected"
  }}
}}"""


# ---------------------------------------------------------------------------
# AI Client abstraction
# ---------------------------------------------------------------------------

class AIClient:
    """
    Abstraction over LLM providers.
    
    Supports Anthropic (Claude), OpenAI, Ollama, and any
    OpenAI-compatible endpoint via LiteLLM.
    """
    
    def __init__(self, config: AIConfig):
        self.config = config
        self._client: Any = None
    
    def _get_client(self) -> Any:
        """Lazy-initialize the LLM client."""
        if self._client is not None:
            return self._client
        
        if self.config.provider == "anthropic":
            try:
                import anthropic
                self._client = anthropic.Anthropic()
                return self._client
            except ImportError:
                raise RuntimeError(
                    "anthropic package not installed. "
                    "Run: pip install anthropic"
                )
        else:
            # Use LiteLLM for all other providers
            try:
                import litellm
                self._client = litellm
                return self._client
            except ImportError:
                raise RuntimeError(
                    "litellm package not installed. "
                    "Run: pip install litellm"
                )
    
    def complete(self, prompt: str, max_tokens: int = 4096) -> str:
        """Send a prompt to the LLM and return the response text."""
        client = self._get_client()
        
        if self.config.provider == "anthropic":
            response = client.messages.create(
                model=self.config.model,
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        else:
            # LiteLLM path
            response = client.completion(
                model=self.config.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content


# ---------------------------------------------------------------------------
# AI Analysis Functions
# ---------------------------------------------------------------------------

def infer_process_flows(
    entry_point_files: list[dict[str, str]],
    ai_client: AIClient,
    repo_path: Path,
) -> list[ProcessFlow]:
    """
    Use AI to infer process flows from entry point source code.
    
    Args:
        entry_point_files: List of dicts with keys: path, language, entry_type, dependencies
        ai_client: Configured AI client
        repo_path: Repository root path
    
    Returns:
        List of ProcessFlow objects
    """
    flows: list[ProcessFlow] = []
    
    for ep in entry_point_files:
        file_path = repo_path / ep["path"]
        if not file_path.exists():
            continue
        
        try:
            code = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        
        # Truncate very long files to fit context window
        if len(code) > 15000:
            code = code[:15000] + "\n# ... (truncated)"
        
        # Build dependency context
        dep_context = ep.get("dependencies", "No additional context available.")
        
        prompt = PROCESS_FLOW_PROMPT.format(
            file_path=ep["path"],
            entry_type=ep.get("entry_type", "http_handler"),
            language=ep.get("language", "python"),
            code=code,
            dependency_context=dep_context,
        )
        
        try:
            response = ai_client.complete(prompt)
            flow_data = _parse_json_response(response)
            
            if flow_data:
                flow = ProcessFlow(
                    id=f"flow:{ep['path']}",
                    name=flow_data.get("flow_name", f"Flow: {ep['path']}"),
                    description=flow_data.get("description", ""),
                    trigger=flow_data.get("trigger", ""),
                    entry_point=ep["path"],
                    steps=[
                        FlowStep(
                            order=s.get("order", i),
                            actor=s.get("actor", "Unknown"),
                            action=s.get("action", ""),
                            target=s.get("target", ""),
                            data=s.get("data"),
                            is_async=s.get("is_async", False),
                            condition=s.get("condition"),
                        )
                        for i, s in enumerate(flow_data.get("steps", []))
                    ],
                )
                flows.append(flow)
                logger.info(f"Inferred flow: {flow.name} ({len(flow.steps)} steps)")
        except Exception as e:
            logger.warning(f"AI flow inference failed for {ep['path']}: {e}")
    
    return flows


def generate_module_summaries(
    snapshot: ArchSnapshot,
    ai_client: AIClient,
) -> dict[str, dict[str, str]]:
    """
    Generate AI-powered summaries for each module.
    
    Returns dict: module_id -> {"summary": ..., "responsibility": ..., "suggested_capability": ...}
    """
    summaries: dict[str, dict[str, str]] = {}
    module_nodes = snapshot.get_module_nodes()
    
    for node in module_nodes:
        # Build context for the prompt
        children = snapshot.get_children(node.id)
        symbols = ", ".join(c.name for c in children[:20])
        
        # Find dependencies
        deps = [
            e.target.replace("module:", "")
            for e in snapshot.edges
            if e.source == node.id
        ]
        
        prompt = MODULE_SUMMARY_PROMPT.format(
            module_name=node.name,
            file_list=node.file_path or "N/A",
            symbols=symbols or "N/A",
            dependencies=", ".join(deps) if deps else "None",
        )
        
        try:
            response = ai_client.complete(prompt, max_tokens=500)
            result = _parse_json_response(response)
            if result:
                summaries[node.id] = result
                node.ai_summary = result.get("summary", "")
        except Exception as e:
            logger.warning(f"AI summary failed for {node.name}: {e}")
    
    return summaries


def detect_patterns(
    snapshot: ArchSnapshot,
    directory_tree: str,
    ai_client: AIClient,
) -> list[ArchPattern]:
    """Use AI to detect architectural patterns in the codebase."""
    # Build module graph description
    module_graph_lines: list[str] = []
    for node in snapshot.get_module_nodes():
        deps = [e.target.replace("module:", "") for e in snapshot.edges if e.source == node.id]
        dep_str = " -> " + ", ".join(deps) if deps else " (no dependencies)"
        module_graph_lines.append(f"  {node.name}{dep_str}")
    
    prompt = PATTERN_DETECTION_PROMPT.format(
        project_name=snapshot.project_name,
        module_graph="\n".join(module_graph_lines),
        directory_structure=directory_tree[:3000],
    )
    
    try:
        response = ai_client.complete(prompt, max_tokens=1000)
        result = _parse_json_response(response)
        if result and "patterns" in result:
            return [
                ArchPattern(p)
                for p in result["patterns"]
                if p in ArchPattern.__members__.values()
                or p in [e.value for e in ArchPattern]
            ]
    except Exception as e:
        logger.warning(f"AI pattern detection failed: {e}")
    
    return []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_response(text: str) -> Optional[dict[str, Any]]:
    """Parse JSON from an LLM response, handling common formatting issues."""
    # Strip markdown code fences if present
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:])
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass
    
    logger.warning("Failed to parse JSON from AI response")
    return None
