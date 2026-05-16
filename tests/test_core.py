"""
Tests for ArchiLens core analysis pipeline.
"""

import tempfile
from pathlib import Path

import pytest

from archilens.analyzers.discovery import discover_files, SourceFile
from archilens.analyzers.dependencies import analyze_dependencies
from archilens.config import ArchiLensConfig, load_config
from archilens.models import ArchSnapshot, DiagramLevel, EdgeType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """Create a sample Python project structure for testing."""
    # src/orders/service.py
    orders_dir = tmp_path / "src" / "orders"
    orders_dir.mkdir(parents=True)
    (orders_dir / "__init__.py").write_text("")
    (orders_dir / "service.py").write_text(
        "from src.payments.gateway import PaymentGateway\n"
        "from src.users.auth import authenticate\n\n"
        "class OrderService:\n"
        "    def __init__(self):\n"
        "        self.payment = PaymentGateway()\n\n"
        "    def create_order(self, user_id, items):\n"
        "        user = authenticate(user_id)\n"
        "        return {'order_id': 1}\n"
    )
    (orders_dir / "models.py").write_text(
        "class Order:\n"
        "    def __init__(self, id, user_id):\n"
        "        self.id = id\n"
        "        self.user_id = user_id\n"
    )

    # src/payments/gateway.py
    payments_dir = tmp_path / "src" / "payments"
    payments_dir.mkdir(parents=True)
    (payments_dir / "__init__.py").write_text("")
    (payments_dir / "gateway.py").write_text(
        "class PaymentGateway:\n"
        "    def charge(self, amount):\n"
        "        return True\n"
    )

    # src/users/auth.py
    users_dir = tmp_path / "src" / "users"
    users_dir.mkdir(parents=True)
    (users_dir / "__init__.py").write_text("")
    (users_dir / "auth.py").write_text(
        "def authenticate(user_id):\n"
        "    return {'id': user_id, 'name': 'Test'}\n\n"
        "class UserManager:\n"
        "    pass\n"
    )

    # src/api/routes.py (entry point)
    api_dir = tmp_path / "src" / "api"
    api_dir.mkdir(parents=True)
    (api_dir / "__init__.py").write_text("")
    (api_dir / "routes.py").write_text(
        "from src.orders.service import OrderService\n\n"
        "class OrderController:\n"
        "    def post_order(self, request):\n"
        "        svc = OrderService()\n"
        "        return svc.create_order(request.user_id, request.items)\n"
    )

    return tmp_path


@pytest.fixture
def default_config() -> ArchiLensConfig:
    return ArchiLensConfig(
        project_name="Test Project",
        analysis=__import__("archilens.config", fromlist=["AnalysisConfig"]).AnalysisConfig(
            languages=["python"],
            entry_points=[{"pattern": "**/routes.py", "type": "http_handler"}],
        ),
    )


# ---------------------------------------------------------------------------
# Tests: File Discovery
# ---------------------------------------------------------------------------

class TestDiscovery:
    def test_discovers_python_files(self, sample_repo, default_config):
        files, modules = discover_files(sample_repo, default_config)
        
        assert len(files) > 0
        assert all(f.language == "python" for f in files)
    
    def test_identifies_modules(self, sample_repo, default_config):
        files, modules = discover_files(sample_repo, default_config)
        
        # Should find modules: src/orders, src/payments, src/users, src/api
        module_names = set(modules.keys())
        assert "src/orders" in module_names
        assert "src/payments" in module_names
        assert "src/users" in module_names
    
    def test_detects_entry_points(self, sample_repo, default_config):
        files, _ = discover_files(sample_repo, default_config)
        
        entry_points = [f for f in files if f.is_entry_point]
        assert len(entry_points) >= 1
        assert any("routes" in ep.relative_path for ep in entry_points)
    
    def test_excludes_patterns(self, sample_repo, default_config):
        # Create a file that should be excluded
        venv_dir = sample_repo / "venv" / "lib"
        venv_dir.mkdir(parents=True)
        (venv_dir / "something.py").write_text("x = 1")
        
        files, _ = discover_files(sample_repo, default_config)
        
        assert not any("venv" in f.relative_path for f in files)


# ---------------------------------------------------------------------------
# Tests: Dependency Analysis
# ---------------------------------------------------------------------------

class TestDependencies:
    def test_extracts_module_dependencies(self, sample_repo, default_config):
        files, modules = discover_files(sample_repo, default_config)
        nodes, edges, graph = analyze_dependencies(files, modules, sample_repo)
        
        # Should have module nodes
        module_nodes = [n for n in nodes if n.level == DiagramLevel.MODULE]
        assert len(module_nodes) >= 3
    
    def test_extracts_class_nodes(self, sample_repo, default_config):
        files, modules = discover_files(sample_repo, default_config)
        nodes, _, _ = analyze_dependencies(files, modules, sample_repo)
        
        class_nodes = [n for n in nodes if n.level == DiagramLevel.COMPONENT]
        class_names = {n.name for n in class_nodes}
        
        assert "OrderService" in class_names
        assert "PaymentGateway" in class_names
    
    def test_builds_networkx_graph(self, sample_repo, default_config):
        files, modules = discover_files(sample_repo, default_config)
        _, _, graph = analyze_dependencies(files, modules, sample_repo)
        
        assert len(graph.nodes) > 0


# ---------------------------------------------------------------------------
# Tests: Configuration
# ---------------------------------------------------------------------------

class TestConfig:
    def test_loads_default_config(self, tmp_path):
        config = load_config(tmp_path)
        assert config.project_name == "Unnamed Project"
        assert config.diagrams.format == "mermaid"
    
    def test_loads_yaml_config(self, tmp_path):
        config_content = """
project:
  name: "My App"
  type: "microservices"

analysis:
  languages: ["python", "typescript"]

diagrams:
  format: "mermaid"
  theme: "dark"
"""
        (tmp_path / ".archilens.yml").write_text(config_content)
        config = load_config(tmp_path)
        
        assert config.project_name == "My App"
        assert config.project_type == "microservices"
        assert "python" in config.analysis.languages
        assert config.diagrams.theme == "dark"


# ---------------------------------------------------------------------------
# Tests: Mermaid Generation
# ---------------------------------------------------------------------------

class TestMermaidGeneration:
    def test_generates_l1_diagram(self, sample_repo, default_config):
        from archilens.engine import analyze_repository
        from archilens.generators.mermaid import generate_module_architecture
        
        snapshot = analyze_repository(sample_repo, default_config, use_ai=False)
        output = generate_module_architecture(snapshot)
        
        assert "```mermaid" in output
        assert "flowchart" in output
    
    def test_generates_process_flow(self):
        from archilens.models import FlowStep, ProcessFlow
        from archilens.generators.mermaid import generate_process_flow
        
        flow = ProcessFlow(
            id="test-flow",
            name="Create Order",
            trigger="POST /orders",
            steps=[
                FlowStep(order=1, actor="Client", action="Submit order", target="API Gateway"),
                FlowStep(order=2, actor="API Gateway", action="Validate request", target="Order Service"),
                FlowStep(order=3, actor="Order Service", action="Process payment", target="Payment Service"),
            ],
        )
        
        output = generate_process_flow(flow)
        assert "sequenceDiagram" in output
        assert "Client" in output
        assert "Submit order" in output
