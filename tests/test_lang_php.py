"""Tests for PHP language handler."""
from tempograph.parser import FileParser
from tempograph.types import Language, EdgeKind, SymbolKind


class TestPHPParser:
    def _parse(self, code: str):
        p = FileParser("test.php", Language.PHP, code.encode())
        symbols, edges, imports = p.parse()
        return symbols, edges, imports

    def test_free_function(self):
        symbols, edges, _ = self._parse(
            '<?php\nfunction greet($name) { echo "Hello"; }'
        )
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert len(funcs) == 1
        assert funcs[0].name == "greet"
        assert funcs[0].exported is True

    def test_class(self):
        symbols, _, _ = self._parse(
            "<?php\nclass MyController {}"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "MyController"
        assert classes[0].exported is True

    def test_public_method(self):
        symbols, edges, _ = self._parse(
            "<?php\nclass Foo {\n    public function bar() {}\n}"
        )
        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        assert len(methods) == 1
        assert methods[0].name == "bar"
        assert methods[0].exported is True
        # Should have CONTAINS edge from Foo to bar
        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("Foo" in e.source_id and "bar" in e.target_id for e in contains)

    def test_private_method(self):
        symbols, _, _ = self._parse(
            "<?php\nclass Foo {\n    private function baz() {}\n}"
        )
        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        assert len(methods) == 1
        assert methods[0].name == "baz"
        assert methods[0].exported is False

    def test_protected_method(self):
        symbols, _, _ = self._parse(
            "<?php\nclass Foo {\n    protected function helper() {}\n}"
        )
        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        assert len(methods) == 1
        assert methods[0].name == "helper"
        assert methods[0].exported is False

    def test_interface(self):
        symbols, _, _ = self._parse(
            "<?php\ninterface Printable {\n    public function printOut();\n}"
        )
        ifaces = [s for s in symbols if s.kind == SymbolKind.INTERFACE]
        assert len(ifaces) == 1
        assert ifaces[0].name == "Printable"
        assert ifaces[0].exported is True

    def test_trait(self):
        symbols, _, _ = self._parse(
            "<?php\ntrait Cacheable {\n    public function cache() {}\n}"
        )
        traits = [s for s in symbols if s.kind == SymbolKind.TRAIT]
        assert len(traits) == 1
        assert traits[0].name == "Cacheable"
        assert traits[0].exported is True

    def test_calls_edge(self):
        symbols, edges, _ = self._parse(
            "<?php\nfunction foo() { bar(); }\nfunction bar() {}"
        )
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert any(e.target_id == "bar" for e in calls), f"No call edge to bar: {calls}"

    def test_member_call_edge(self):
        symbols, edges, _ = self._parse(
            '<?php\nclass Svc {\n    public function run() {\n        $this->helper();\n    }\n    private function helper() {}\n}'
        )
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert any(e.target_id == "helper" for e in calls), f"No call edge to helper: {calls}"

    def test_static_call_edge(self):
        symbols, edges, _ = self._parse(
            "<?php\nfunction main() { User::find(1); }"
        )
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert any("User" in e.target_id and "find" in e.target_id for e in calls), f"No call to User.find: {calls}"

    def test_extends_edge(self):
        symbols, edges, _ = self._parse(
            "<?php\nclass Child extends Parent {}"
        )
        inherits = [e for e in edges if e.kind == EdgeKind.INHERITS]
        assert any(e.target_id == "Parent" for e in inherits), f"No INHERITS to Parent: {inherits}"

    def test_implements_edge(self):
        symbols, edges, _ = self._parse(
            "<?php\nclass Svc implements Printable {}"
        )
        impls = [e for e in edges if e.kind == EdgeKind.IMPLEMENTS]
        assert any(e.target_id == "Printable" for e in impls), f"No IMPLEMENTS to Printable: {impls}"

    def test_use_imports(self):
        _, _, imports = self._parse(
            "<?php\nuse App\\Models\\User;\nfunction foo() {}"
        )
        assert len(imports) >= 1
        assert any("User" in imp for imp in imports)
