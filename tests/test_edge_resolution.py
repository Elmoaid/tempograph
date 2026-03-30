"""Tests for self.method(), cls.method(), mixin, and decorator edge resolution in Python."""
import os

from tempograph.builder import build_graph
from tempograph.types import EdgeKind


class TestSelfMethodResolution:
    """self.method() should resolve to the class method in Python."""

    def test_self_method_creates_edge(self, tmp_path):
        """self.validate() inside MyService resolves to MyService.validate."""
        (tmp_path / "service.py").write_text(
            "class MyService:\n"
            "    def validate(self): return True\n"
            "    def process(self):\n"
            "        return self.validate()\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        validate = [s for s in g.symbols.values() if s.name == "validate"][0]
        callers = g.callers_of(validate.id)
        caller_names = [c.name for c in callers]
        assert "process" in caller_names, (
            f"self.validate() should resolve; callers of validate: {caller_names}"
        )

    def test_cls_method_creates_edge(self, tmp_path):
        """cls.create() inside a classmethod resolves to the class method."""
        (tmp_path / "factory.py").write_text(
            "class Widget:\n"
            "    @classmethod\n"
            "    def create(cls):\n"
            "        return cls()\n"
            "    @classmethod\n"
            "    def batch_create(cls, n):\n"
            "        return [cls.create() for _ in range(n)]\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        create = [s for s in g.symbols.values() if s.name == "create"][0]
        callers = g.callers_of(create.id)
        caller_names = [c.name for c in callers]
        assert "batch_create" in caller_names, (
            f"cls.create() should resolve; callers of create: {caller_names}"
        )

    def test_self_method_cross_not_resolved_to_wrong_class(self, tmp_path):
        """self.run() in ClassA should NOT resolve to ClassB.run()."""
        (tmp_path / "a.py").write_text(
            "class ClassA:\n"
            "    def run(self): pass\n"
            "    def go(self): self.run()\n"
        )
        (tmp_path / "b.py").write_text(
            "class ClassB:\n"
            "    def run(self): pass\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        b_run = [s for s in g.symbols.values() if s.name == "run" and "b.py" in s.file_path][0]
        callers = g.callers_of(b_run.id)
        caller_names = [c.name for c in callers]
        assert "go" not in caller_names, (
            f"self.run() in ClassA should NOT resolve to ClassB.run; callers: {caller_names}"
        )

    def test_self_resolution_on_tempograph_repo(self):
        """Self-test: _init_schema should have callers from self._init_schema() calls."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        g = build_graph(repo, use_cache=False, use_db=False)
        init_schema = [s for s in g.symbols.values()
                       if s.name == "_init_schema" and "storage" in s.file_path]
        if init_schema:
            callers = g.callers_of(init_schema[0].id)
            caller_names = [c.name for c in callers]
            assert len(callers) > 0, (
                f"self._init_schema() should have callers; got: {caller_names}"
            )


class TestMixinSelfResolution:
    """self.method() in mixin classes should resolve through inheritance."""

    def test_mixin_method_resolves_to_parent(self, tmp_path):
        """self.helper() in a child class resolves to the parent class method."""
        (tmp_path / "base.py").write_text(
            "class Base:\n"
            "    def helper(self): return 1\n"
        )
        (tmp_path / "child.py").write_text(
            "from base import Base\n"
            "class Child(Base):\n"
            "    def process(self):\n"
            "        return self.helper()\n"
        )
        g = build_graph(str(tmp_path), use_cache=False, use_db=False)
        helper = [s for s in g.symbols.values() if s.name == "helper"][0]
        callers = g.callers_of(helper.id)
        caller_names = [c.name for c in callers]
        assert "process" in caller_names, (
            f"self.helper() in Child should resolve to Base.helper; callers: {caller_names}"
        )

    def test_mixin_resolves_to_child_class(self, tmp_path):
        """self.method() in a mixin resolves to the composing child class."""
        (tmp_path / "mixin.py").write_text(
            "class LogMixin:\n"
            "    def do_work(self):\n"
            "        return self.get_name()\n"
        )
        (tmp_path / "main.py").write_text(
            "from mixin import LogMixin\n"
            "class App(LogMixin):\n"
            "    def get_name(self): return 'app'\n"
        )
        g = build_graph(str(tmp_path), use_cache=False, use_db=False)
        get_name = [s for s in g.symbols.values() if s.name == "get_name"][0]
        callers = g.callers_of(get_name.id)
        caller_names = [c.name for c in callers]
        assert "do_work" in caller_names, (
            f"self.get_name() in LogMixin should resolve to App.get_name; callers: {caller_names}"
        )

    def test_sibling_mixin_resolution(self, tmp_path):
        """self.method() in MixinA resolves to MixinB when both mixed into same class."""
        (tmp_path / "mixin_a.py").write_text(
            "class MixinA:\n"
            "    def action(self):\n"
            "        return self.validate()\n"
        )
        (tmp_path / "mixin_b.py").write_text(
            "class MixinB:\n"
            "    def validate(self): return True\n"
        )
        (tmp_path / "composed.py").write_text(
            "from mixin_a import MixinA\n"
            "from mixin_b import MixinB\n"
            "class Composed(MixinA, MixinB):\n"
            "    pass\n"
        )
        g = build_graph(str(tmp_path), use_cache=False, use_db=False)
        validate = [s for s in g.symbols.values() if s.name == "validate"][0]
        callers = g.callers_of(validate.id)
        caller_names = [c.name for c in callers]
        assert "action" in caller_names, (
            f"self.validate() in MixinA should resolve to MixinB.validate via Composed; "
            f"callers: {caller_names}"
        )

    def test_handler_mixin_resolves_to_parser(self):
        """Real test: CHandlerMixin._handle_c_function -> self._compute_complexity resolves."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        g = build_graph(repo, use_cache=False, use_db=False)
        compute_cx = [s for s in g.symbols.values()
                      if s.name == "_compute_complexity" and "parser" in s.file_path]
        assert compute_cx, "_compute_complexity should exist in parser.py"
        callers = g.callers_of(compute_cx[0].id)
        handler_callers = [c for c in callers if "handler" in c.file_path.lower()]
        assert len(handler_callers) > 0, (
            f"_compute_complexity should have callers from handler mixins; "
            f"got: {[c.name for c in callers]}"
        )

    def test_unresolved_self_count(self):
        """Real test: unresolved self.X calls should be minimal after mixin resolution."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        g = build_graph(repo, use_cache=False, use_db=False)
        unresolved = [e for e in g.edges
                      if e.kind == EdgeKind.CALLS
                      and "self." in e.target_id
                      and "::" not in e.target_id]
        assert len(unresolved) <= 5, (
            f"Expected <= 5 unresolved self.X calls after mixin resolution; "
            f"got {len(unresolved)}: {[(e.source_id, e.target_id) for e in unresolved]}"
        )


class TestDecoratorEdges:
    """Decorator dispatch: @route, @fixture, @mcp.tool create CALLS edges."""

    @staticmethod
    def _raw_callers(graph, sym_id):
        """Return raw caller IDs from _callers index (includes unresolved decorator sources)."""
        return graph._callers.get(sym_id, [])

    def test_route_decorator_creates_edge(self, tmp_path):
        """@app.route creates a CALLS edge from app.route to the decorated function."""
        (tmp_path / "app.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/api/users')\n"
            "def get_users():\n"
            "    return []\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        get_users = [s for s in g.symbols.values() if s.name == "get_users"]
        assert get_users, "get_users symbol should exist"
        callers = self._raw_callers(g, get_users[0].id)
        assert any("app.route" in c for c in callers), (
            f"@app.route should be a raw caller; got: {callers}"
        )

    def test_pytest_fixture_creates_edge(self, tmp_path):
        """@pytest.fixture creates a CALLS edge from pytest.fixture to the function."""
        (tmp_path / "conftest.py").write_text(
            "import pytest\n"
            "@pytest.fixture\n"
            "def db_session():\n"
            "    return 'session'\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        db_session = [s for s in g.symbols.values() if s.name == "db_session"]
        assert db_session, "db_session symbol should exist"
        callers = self._raw_callers(g, db_session[0].id)
        assert any("pytest.fixture" in c for c in callers), (
            f"@pytest.fixture should be a raw caller; got: {callers}"
        )

    def test_property_decorator_skipped(self, tmp_path):
        """@property should NOT create a decorator edge."""
        (tmp_path / "model.py").write_text(
            "class User:\n"
            "    @property\n"
            "    def name(self):\n"
            "        return self._name\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        prop_edges = [e for e in g.edges if e.kind == EdgeKind.CALLS
                      and "property" in e.source_id]
        assert len(prop_edges) == 0, "property decorator should not create edge"

    def test_staticmethod_decorator_skipped(self, tmp_path):
        """@staticmethod should NOT create a decorator edge."""
        (tmp_path / "util.py").write_text(
            "class Utils:\n"
            "    @staticmethod\n"
            "    def helper():\n"
            "        return 1\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        sm_edges = [e for e in g.edges if e.kind == EdgeKind.CALLS
                    and "staticmethod" in e.source_id]
        assert len(sm_edges) == 0, "staticmethod decorator should not create edge"

    def test_click_command_creates_edge(self, tmp_path):
        """@click.command() creates a CALLS edge from click.command to the function."""
        (tmp_path / "cli.py").write_text(
            "import click\n"
            "@click.command()\n"
            "def main():\n"
            "    pass\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        main_fn = [s for s in g.symbols.values() if s.name == "main"]
        assert main_fn, "main symbol should exist"
        callers = self._raw_callers(g, main_fn[0].id)
        assert any("click.command" in c for c in callers), (
            f"@click.command() should be a raw caller; got: {callers}"
        )

    def test_multiple_decorators_create_edges(self, tmp_path):
        """Multiple decorators should all create CALLS edges."""
        (tmp_path / "views.py").write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n"
            "@app.route('/api')\n"
            "@login_required\n"
            "def protected_view():\n"
            "    return []\n"
        )
        g = build_graph(str(tmp_path), use_cache=False)
        view = [s for s in g.symbols.values() if s.name == "protected_view"]
        assert view, "protected_view symbol should exist"
        callers = self._raw_callers(g, view[0].id)
        assert any("app.route" in c for c in callers), (
            f"@app.route should be a raw caller; got: {callers}"
        )
        assert any("login_required" in c for c in callers), (
            f"@login_required should be a raw caller; got: {callers}"
        )

    def test_mcp_tool_decorator_on_self_repo(self):
        """MCP tools in server.py should have mcp.tool as a raw caller."""
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        g = build_graph(repo, use_cache=False, use_db=False)
        focus_fns = [s for s in g.symbols.values()
                     if s.name == "focus" and "server" in s.file_path]
        if focus_fns:
            callers = self._raw_callers(g, focus_fns[0].id)
            assert any("mcp.tool" in c for c in callers), (
                f"@mcp.tool() decorated focus should have mcp.tool as raw caller; "
                f"got: {callers}"
            )
