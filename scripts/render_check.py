"""
Render every diagram ArchiLens generates with the official tools, and fail on any error.

Generates all levels (L0-L3) in all three formats for:
  - a feature-rich snapshot (capabilities, external systems, inheritance, an
    interface, a process flow with data, a condition and an async step),
  - a hostile snapshot whose names and texts contain characters that break
    naive generators (quotes, colons, semicolons, braces, #, <>, parentheses,
    non-ASCII, leading digits, newlines),
  - this repository, analysed for real,
then renders them with:
  - Mermaid:  the minlag/mermaid-cli Docker image (mermaid-cli with a bundled Chrome)
  - D2:      the terrastruct/d2 Docker image      (needs Docker)
  - PlantUML: the plantuml/plantuml Docker image  (needs Docker)

Usage:  python scripts/render_check.py [--keep DIR]
Run in CI by .github/workflows/ci.yml (job "diagrams").
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from archilens.config import ArchiLensConfig, ExternalSystem, load_config
from archilens.engine import analyze_repository
from archilens.generators.base import get_generator
from archilens.models import (
    ArchEdge,
    ArchNode,
    ArchPattern,
    ArchSnapshot,
    DiagramLevel,
    EdgeType,
    FlowStep,
    NodeType,
    ProcessFlow,
)

MERMAID_IMAGE = "minlag/mermaid-cli:latest"
D2_IMAGE = "terrastruct/d2:latest"
PLANTUML_IMAGE = "plantuml/plantuml:latest"


def _module(path: str, name: str, capability: str | None, summary: str | None, loc: int = 1200) -> ArchNode:
    return ArchNode(
        id=f"module:{path}",
        name=name,
        node_type=NodeType.MODULE,
        level=DiagramLevel.MODULE,
        capability=capability,
        ai_summary=summary,
        lines_of_code=loc,
        file_path=path,
    )


def _class(name: str, path: str, module: str, kind: NodeType = NodeType.CLASS) -> ArchNode:
    return ArchNode(
        id=f"class:{path}:{name}",
        name=name,
        node_type=kind,
        level=DiagramLevel.COMPONENT,
        file_path=path,
        parent=f"module:{module}",
    )


def _functions(file_name: str, module: str, members: list[str]) -> ArchNode:
    """A file of functions, as analyze_dependencies builds it."""
    path = f"{module}/{file_name}"
    return ArchNode(
        id=f"functions:{path}",
        name=file_name,
        node_type=NodeType.FUNCTION,
        level=DiagramLevel.COMPONENT,
        file_path=path,
        parent=f"module:{module}",
        members=members,
    )


def fixture(name: str, texts: dict[str, str]) -> tuple[ArchSnapshot, ArchiLensConfig]:
    """A snapshot exercising every diagram feature; *texts* supplies the names and descriptions."""
    t = texts
    a = _module("src/orders", t["module_a"], t["cap_a"], t["summary_a"])
    b = _module("src/payments", t["module_b"], t["cap_b"], t["summary_b"])
    c = _module("src/api-gateway", t["module_c"], None, None, 300)
    classes = [
        _class(t["class_1"], "src/orders/service.py", "src/orders"),
        _class(t["class_2"], "src/orders/base.py", "src/orders"),
        _class(t["class_3"], "src/orders/repo.py", "src/orders", NodeType.INTERFACE),
        _class(t["class_2"], "src/orders/legacy/base.py", "src/orders"),  # same name, other file
        _class("Gateway", "src/payments/gateway.py", "src/payments"),
    ]
    # Files of functions: a short list, and one long enough to end in "(and N more)".
    helpers = _functions(t["functions_1"], "src/orders", ["price", "$format", "_round"])
    handlers = _functions(t["functions_2"], "src/orders", [f"handle_{i}" for i in range(12)])
    classes += [helpers, handlers]
    a.children = [x.id for x in classes if x.parent == a.id]
    b.children = [classes[4].id]
    edges = [
        ArchEdge(source=c.id, target=a.id, edge_type=EdgeType.DEPENDENCY, label="12 imports", weight=12),
        ArchEdge(source=a.id, target=b.id, edge_type=EdgeType.DEPENDENCY, label="3 imports", weight=3),
        ArchEdge(source=classes[0].id, target=classes[1].id, edge_type=EdgeType.INHERITANCE, label="extends"),
        ArchEdge(source=classes[0].id, target=classes[2].id, edge_type=EdgeType.IMPLEMENTATION, label="implements"),
        ArchEdge(source=handlers.id, target=helpers.id, edge_type=EdgeType.DEPENDENCY, label="imports"),
        ArchEdge(source=handlers.id, target=classes[0].id, edge_type=EdgeType.DEPENDENCY, label="imports"),
    ]
    flow = ProcessFlow(
        id="flow:checkout",
        name=t["flow"],
        description=t["flow_description"],
        trigger="POST /api/orders",
        steps=[
            FlowStep(order=1, actor=t["actor_1"], action=t["action_1"], target=t["actor_2"], data=t["data_1"]),
            FlowStep(order=2, actor=t["actor_2"], action="create order", target=t["actor_3"]),
            FlowStep(
                order=3,
                actor=t["actor_3"],
                action="charge card",
                target="Payment Gateway",
                data="amount: 42.00",
                condition=t["condition"],
            ),
            FlowStep(order=4, actor=t["actor_3"], action="publish OrderPlaced", target="Event Bus", is_async=True),
            FlowStep(order=5, actor=t["actor_2"], action="201 Created", target=t["actor_1"]),
        ],
    )
    snapshot = ArchSnapshot(
        project_name=t["project"],
        git_ref="HEAD",
        nodes=[a, b, c, *classes],
        edges=edges,
        flows=[flow],
        detected_patterns=[ArchPattern.LAYERED, ArchPattern.EVENT_DRIVEN],
        capability_map={t["cap_a"]: [a.id], t["cap_b"]: [b.id]},
    )
    config = ArchiLensConfig()
    config.project_name = t["project"]
    config.external_systems = [
        ExternalSystem(name=t["ext_1"], type="database", description=t["ext_1_description"]),
        ExternalSystem(name="Stripe API", type="external_api", description="Payments and refunds"),
    ]
    return snapshot, config


RICH = {
    "project": "Shop",
    "module_a": "Orders",
    "module_b": "Payments",
    "module_c": "Api Gateway",
    "cap_a": "Order Management",
    "cap_b": "Payment Processing",
    "summary_a": "Handles the order lifecycle",
    "summary_b": "Charges cards",
    "class_1": "OrderService",
    "class_2": "BaseService",
    "class_3": "Repository",
    "functions_1": "pricing.py",
    "functions_2": "handlers.py",
    "flow": "Checkout Flow",
    "flow_description": "Customer places an order",
    "actor_1": "Client",
    "actor_2": "API Gateway",
    "actor_3": "Order Service",
    "action_1": "POST /orders",
    "data_1": "items, card",
    "condition": "card is valid",
    "ext_1": "PostgreSQL",
    "ext_1_description": "Primary store",
}

HOSTILE = {
    "project": 'Shop (demo) "v2": ünïcode; #1',
    "module_a": "Orders: core {v2}",
    "module_b": "Pay#ments; <EU>",
    "module_c": "3rd-party API (beta)",
    "cap_a": 'Order "Management": B2B',
    "cap_b": "Payments & {Refunds}; EU/UK",
    "summary_a": 'Handles "checkout" & order <state>; retries #3\nsecond line',
    "summary_b": "Charges cards: Stripe | PayPal {fallback}",
    "class_1": "Repo<Order>",
    "class_2": "Base Service",
    "class_3": "IRepo[T]",
    "functions_1": "price utils (v2) #1.js",
    "functions_2": "[id].handlers.ts",
    "flow": 'Checkout: "fast" path; #2',
    "flow_description": "Places an order: payment & <confirm>\nline two",
    "actor_1": "Client (web)",
    "actor_2": 'API "Gateway"',
    "actor_3": "Order-Service: v2",
    "action_1": "POST /orders; retry #1",
    "data_1": '{"items": [1, 2], "note": "<b>"}',
    "condition": 'card.valid && amount > 0; "3DS"',
    "ext_1": "Postgres (primary) #1",
    "ext_1_description": 'Store: "orders" & <events>; v15',
}


def run(cmd: list[str], timeout: int = 600) -> tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _docker(mount: Path, workdir: str, image: str, *args: str, as_caller: bool = True) -> list[str]:
    cmd = ["docker", "run", "--rm", "-v", f"{mount}:{workdir}", "-w", workdir]
    if as_caller and hasattr(os, "getuid"):  # Linux/macOS: write output files as the calling user
        cmd += ["--user", f"{os.getuid()}:{os.getgid()}", "-e", "HOME=/tmp"]
    return [*cmd, image, *args]


def render(path: Path) -> tuple[Path, bool, str]:
    if path.suffix == ".d2":
        code, out = run(_docker(path.parent, "/home/debian/src", D2_IMAGE, path.name, path.stem + ".svg"))
        return path, code == 0 and (path.parent / (path.stem + ".svg")).exists(), out
    if path.suffix == ".puml":
        code, out = run(_docker(path.parent, "/data", PLANTUML_IMAGE, "-tsvg", "-failfast2", path.name))
        return path, code == 0 and "Error" not in out, out
    # The image's Chrome lives under its own user, so it runs as that user; main()
    # makes the output tree writable for it.
    out_md = path.stem + ".out.md"
    code, out = run(_docker(path.parent, "/data", MERMAID_IMAGE, "-i", path.name, "-o", out_md, as_caller=False))
    return path, code == 0 and (path.parent / out_md).exists(), out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--keep", type=Path, help="write diagrams here and keep them (default: a temp dir)")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)

    root = args.keep or Path(tempfile.mkdtemp(prefix="archilens-render-"))
    repo = Path(__file__).resolve().parent.parent
    cases = [("rich", *fixture("rich", RICH)), ("hostile", *fixture("hostile", HOSTILE))]
    repo_config = load_config(repo)
    cases.append(("repository", analyze_repository(repo, repo_config, use_ai=False), repo_config))

    for name, snapshot, config in cases:
        for fmt in ("mermaid", "d2", "plantuml"):
            config.diagrams.format = fmt
            get_generator(config).generate_all(snapshot, root / name / fmt)

    files = sorted(
        p
        for p in root.rglob("*")
        if (p.suffix in (".d2", ".puml") or (p.suffix == ".md" and "```mermaid" in p.read_text(encoding="utf-8")))
        and p.name != "INDEX.md"
        and not p.name.endswith(".out.md")
    )
    if hasattr(os, "chmod"):  # let the mermaid-cli container's own user write next to the inputs
        for item in [root, *root.rglob("*")]:
            item.chmod(0o777 if item.is_dir() else 0o666)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(render, files))

    failed = [(p, out) for p, ok, out in results if not ok]
    for path, ok, _ in results:
        print(f"{'ok  ' if ok else 'FAIL'} {path.relative_to(root).as_posix()}")
    print(f"\n{len(results) - len(failed)}/{len(results)} diagrams rendered" + ("" if args.keep else f" (in {root})"))
    for path, out in failed:
        print(f"\n--- {path.relative_to(root).as_posix()}\n{out[-1500:]}")
    if not args.keep:
        shutil.rmtree(root, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
