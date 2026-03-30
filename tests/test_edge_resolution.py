"""Tests for self.method() and cls.method() edge resolution in Python."""
import os

from tempograph.builder import build_graph


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
