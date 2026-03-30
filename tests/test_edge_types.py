"""Tests for USES_TYPE and IMPLEMENTS edge creation."""
from tempograph.builder import build_graph
from tempograph.types import EdgeKind


class TestUsesTypeEdges:
    def test_python_return_type_creates_edge(self, tmp_path):
        (tmp_path / "models.py").write_text("class User:\n    pass\n")
        (tmp_path / "service.py").write_text(
            "from models import User\n"
            "def get_user(user_id: int) -> User:\n"
            "    return User()\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        uses_type = [e for e in g.edges if e.kind == EdgeKind.USES_TYPE]
        assert any(e.target_id.endswith("::User") for e in uses_type), (
            f"Expected resolved USES_TYPE edge to User; got: {[(e.source_id, e.target_id) for e in uses_type]}"
        )

    def test_python_param_type_creates_edge(self, tmp_path):
        (tmp_path / "models.py").write_text("class Config:\n    pass\n")
        (tmp_path / "app.py").write_text(
            "from models import Config\n"
            "def setup(cfg: Config) -> None:\n"
            "    pass\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        uses_type = [e for e in g.edges if e.kind == EdgeKind.USES_TYPE]
        assert any("Config" in e.target_id for e in uses_type), (
            f"Expected USES_TYPE edge for Config param; got: {[(e.source_id, e.target_id) for e in uses_type]}"
        )

    def test_no_edge_for_builtin_types(self, tmp_path):
        (tmp_path / "utils.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        uses_type = [e for e in g.edges if e.kind == EdgeKind.USES_TYPE]
        # int is a builtin (lowercase) — should NOT create USES_TYPE edge
        assert not any("int" in e.target_id for e in uses_type), (
            f"Should not create USES_TYPE for builtins; got: {[(e.source_id, e.target_id) for e in uses_type]}"
        )

    def test_no_edge_for_container_types(self, tmp_path):
        (tmp_path / "models.py").write_text("class Item:\n    pass\n")
        (tmp_path / "svc.py").write_text(
            "from typing import Optional, List\n"
            "from models import Item\n"
            "def get_items() -> Optional[List[Item]]:\n"
            "    return []\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        uses_type = [e for e in g.edges if e.kind == EdgeKind.USES_TYPE]
        # Optional and List are containers — should be skipped
        assert not any(e.target_id == "Optional" for e in uses_type)
        assert not any(e.target_id == "List" for e in uses_type)
        # Item is a real type — should be present
        assert any("Item" in e.target_id for e in uses_type), (
            f"Expected USES_TYPE for Item; got: {[(e.source_id, e.target_id) for e in uses_type]}"
        )

    def test_union_type_annotation(self, tmp_path):
        (tmp_path / "types.py").write_text(
            "class Success:\n    pass\n\n"
            "class Error:\n    pass\n"
        )
        (tmp_path / "handler.py").write_text(
            "from types import Success, Error\n"
            "def handle() -> Success | Error:\n"
            "    pass\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        uses_type = [e for e in g.edges if e.kind == EdgeKind.USES_TYPE]
        targets = {e.target_id.split("::")[-1] if "::" in e.target_id else e.target_id for e in uses_type}
        assert "Success" in targets, f"Expected USES_TYPE for Success; targets: {targets}"
        assert "Error" in targets, f"Expected USES_TYPE for Error; targets: {targets}"


class TestImplementsEdges:
    def test_typescript_implements_creates_edge(self, tmp_path):
        (tmp_path / "app.ts").write_text(
            "interface Serializable {\n"
            "  serialize(): string;\n"
            "}\n\n"
            "class User implements Serializable {\n"
            "  serialize(): string { return ''; }\n"
            "}\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        impl_edges = [e for e in g.edges if e.kind == EdgeKind.IMPLEMENTS]
        assert any("Serializable" in e.target_id for e in impl_edges), (
            f"Expected IMPLEMENTS edge to Serializable; got: {[(e.source_id, e.target_id) for e in impl_edges]}"
        )

    def test_java_implements_creates_edge(self, tmp_path):
        (tmp_path / "App.java").write_text(
            "interface Runnable {\n"
            "  void run();\n"
            "}\n\n"
            "class Task implements Runnable {\n"
            "  public void run() {}\n"
            "}\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        impl_edges = [e for e in g.edges if e.kind == EdgeKind.IMPLEMENTS]
        assert any("Runnable" in e.target_id for e in impl_edges), (
            f"Expected IMPLEMENTS edge to Runnable; got: {[(e.source_id, e.target_id) for e in impl_edges]}"
        )
