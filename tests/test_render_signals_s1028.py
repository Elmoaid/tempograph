"""S1028: Symbol-level blast preview in diff mode.

When a changed (non-test) file contains a function/method with ≥10 cross-file callers
outside the diff, surface the specific symbol and its top consumer files.

Distinct from S80 (file-level blast verdict) and the 'Risk:' file summary — this names
the specific function that is the blast center, enabling agents to reason about impact
without running blast_radius separately.
"""

from tempograph.builder import build_graph
from tempograph.render import render_diff_context


class TestSymbolBlastPreview:
    """S1028: blast preview fires for high-caller-count symbols in changed files."""

    def _build(self, tmp_path, files: dict):
        for name, content in files.items():
            (tmp_path / name).write_text(content)
        return build_graph(str(tmp_path), use_cache=False)

    def test_fires_when_function_has_10_plus_callers(self, tmp_path):
        """Signal fires when a changed file contains a function with ≥10 cross-file callers."""
        files = {
            "core.py": "def do_work():\n    pass\n",
        }
        for i in range(12):
            files[f"consumer_{i}.py"] = f"from core import do_work\ndef use_{i}():\n    do_work()\n"
        g = self._build(tmp_path, files)
        out = render_diff_context(g, ["core.py"])
        assert "blast preview" in out, f"Expected 'blast preview' signal; got:\n{out}"
        assert "do_work" in out, f"Expected symbol name 'do_work'; got:\n{out}"

    def test_caller_count_in_output(self, tmp_path):
        """Output includes the number of cross-file callers."""
        files = {"core.py": "def important():\n    pass\n"}
        for i in range(11):
            files[f"user_{i}.py"] = f"from core import important\ndef call_{i}():\n    important()\n"
        g = self._build(tmp_path, files)
        out = render_diff_context(g, ["core.py"])
        assert "11 cross-file callers" in out, f"Expected caller count; got:\n{out}"

    def test_top_consumers_listed(self, tmp_path):
        """Output includes top consumer file names."""
        files = {"api.py": "def handle():\n    pass\n"}
        for i in range(12):
            files[f"service_{i}.py"] = f"from api import handle\ndef svc_{i}():\n    handle()\n"
        g = self._build(tmp_path, files)
        out = render_diff_context(g, ["api.py"])
        assert "blast preview" in out, f"Expected blast preview; got:\n{out}"
        # At least one consumer file name should appear
        assert any(f"service_{i}" in out for i in range(12)), f"Expected consumer file in output; got:\n{out}"

    def test_silent_below_threshold(self, tmp_path):
        """Signal is silent when function has fewer than 10 cross-file callers."""
        files = {"util.py": "def helper():\n    pass\n"}
        for i in range(8):
            files[f"caller_{i}.py"] = f"from util import helper\ndef use_{i}():\n    helper()\n"
        g = self._build(tmp_path, files)
        out = render_diff_context(g, ["util.py"])
        assert "blast preview" not in out, f"Unexpected blast preview for low-caller count; got:\n{out}"

    def test_silent_for_test_files(self, tmp_path):
        """Signal does not fire when the changed file is a test file, even if it has many callers."""
        files = {"test_core.py": "def test_fn():\n    pass\n"}
        for i in range(12):
            files[f"helper_{i}.py"] = f"from test_core import test_fn\ndef use_{i}():\n    test_fn()\n"
        g = self._build(tmp_path, files)
        out = render_diff_context(g, ["test_core.py"])
        assert "blast preview" not in out, f"Unexpected blast preview for test file; got:\n{out}"

    def test_callers_in_diff_excluded(self, tmp_path):
        """Callers whose files are also in the diff are not counted — they're co-changing."""
        files = {
            "core.py": "def shared():\n    pass\n",
            "co_changing.py": "from core import shared\ndef other():\n    shared()\n",
        }
        # Only 2 OTHER callers outside the diff — below threshold
        for i in range(2):
            files[f"ext_{i}.py"] = f"from core import shared\ndef use_{i}():\n    shared()\n"
        g = self._build(tmp_path, files)
        # co_changing.py is in the diff so its call doesn't count; only 2 external callers
        out = render_diff_context(g, ["core.py", "co_changing.py"])
        assert "blast preview" not in out, f"Expected silence when co-changers excluded; got:\n{out}"

    def test_shows_top_2_symbols(self, tmp_path):
        """When 2+ symbols qualify, shows top 2 by caller count."""
        files = {
            "api.py": "def primary():\n    pass\ndef secondary():\n    pass\n",
        }
        # primary: 15 callers, secondary: 12 callers
        for i in range(15):
            files[f"a_{i}.py"] = f"from api import primary\ndef ap_{i}():\n    primary()\n"
        for i in range(12):
            files[f"b_{i}.py"] = f"from api import secondary\ndef bp_{i}():\n    secondary()\n"
        g = self._build(tmp_path, files)
        out = render_diff_context(g, ["api.py"])
        assert "primary" in out, f"Expected 'primary' in output; got:\n{out}"
        assert "secondary" in out, f"Expected 'secondary' in output; got:\n{out}"
        assert out.count("blast preview") <= 2, f"Expected at most 2 blast preview lines; got:\n{out}"

    def test_overflow_count_in_consumers(self, tmp_path):
        """Consumer list shows '+N more' when there are more than 2 consumer files."""
        files = {"core.py": "def engine():\n    pass\n"}
        for i in range(15):
            files[f"mod_{i}.py"] = f"from core import engine\ndef run_{i}():\n    engine()\n"
        g = self._build(tmp_path, files)
        out = render_diff_context(g, ["core.py"])
        assert "more" in out, f"Expected '+N more' overflow in consumer list; got:\n{out}"
        assert "blast preview" in out, f"Expected blast preview; got:\n{out}"
