"""Tests for Kotlin language handler."""
from tempograph.parser import FileParser
from tempograph.types import Language, EdgeKind, SymbolKind


class TestKotlinParser:
    def _parse(self, code: str):
        p = FileParser("test.kt", Language.KOTLIN, code.encode())
        symbols, edges, imports = p.parse()
        return symbols, edges, imports

    def test_basic_function(self):
        symbols, _, _ = self._parse(
            "fun greet(name: String): String {\n    return \"Hello\"\n}"
        )
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert len(funcs) == 1
        assert funcs[0].name == "greet"
        assert funcs[0].exported is True

    def test_class_with_methods(self):
        symbols, edges, _ = self._parse(
            "class MyClass {\n    fun doSomething() {}\n    fun anotherMethod() {}\n}"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "MyClass"

        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        assert len(methods) == 2
        names = {m.name for m in methods}
        assert names == {"doSomething", "anotherMethod"}

        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert len(contains) == 2

    def test_interface_detection(self):
        symbols, _, _ = self._parse(
            "interface Printable {\n    fun print()\n}"
        )
        ifaces = [s for s in symbols if s.kind == SymbolKind.INTERFACE]
        assert len(ifaces) == 1
        assert ifaces[0].name == "Printable"
        assert ifaces[0].exported is True

        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        assert len(methods) == 1
        assert methods[0].name == "print"

    def test_object_declaration(self):
        symbols, _, _ = self._parse(
            "object Singleton {\n    fun instance(): Singleton = this\n}"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "Singleton"

        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        assert len(methods) == 1
        assert methods[0].name == "instance"

    def test_companion_object(self):
        symbols, edges, _ = self._parse(
            "class MyClass {\n    companion object {\n        fun create(): MyClass = MyClass(0)\n    }\n}"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "MyClass"

        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        assert len(methods) == 1
        assert methods[0].name == "create"
        assert "MyClass" in methods[0].qualified_name

        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("create" in e.target_id for e in contains)

    def test_visibility_modifiers(self):
        symbols, _, _ = self._parse(
            "fun publicFunc() {}\nprivate fun privateFunc() {}\nprotected fun protectedFunc() {}\ninternal fun internalFunc() {}"
        )
        funcs = {s.name: s for s in symbols if s.kind == SymbolKind.FUNCTION}
        assert funcs["publicFunc"].exported is True
        assert funcs["privateFunc"].exported is False
        assert funcs["protectedFunc"].exported is False
        assert funcs["internalFunc"].exported is False

    def test_extension_function(self):
        symbols, _, _ = self._parse(
            'fun String.addExclamation(): String = this + "!"'
        )
        funcs = [s for s in symbols if s.kind == SymbolKind.FUNCTION]
        assert len(funcs) == 1
        assert funcs[0].name == "addExclamation"
        assert funcs[0].qualified_name == "String.addExclamation"

    def test_data_class(self):
        symbols, _, _ = self._parse(
            "data class User(val name: String, val age: Int)"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        assert len(classes) == 1
        assert classes[0].name == "User"
        assert classes[0].exported is True

    def test_enum_class(self):
        symbols, _, _ = self._parse(
            "enum class Color { RED, GREEN, BLUE }"
        )
        enums = [s for s in symbols if s.kind == SymbolKind.ENUM]
        assert len(enums) == 1
        assert enums[0].name == "Color"

    def test_sealed_class(self):
        symbols, _, _ = self._parse(
            "sealed class Result {\n    data class Success(val data: String) : Result()\n    data class Error(val message: String) : Result()\n}"
        )
        classes = [s for s in symbols if s.kind == SymbolKind.CLASS]
        assert len(classes) == 3  # Result + Success + Error
        names = {c.name for c in classes}
        assert names == {"Result", "Success", "Error"}

    def test_secondary_constructor(self):
        symbols, edges, _ = self._parse(
            "class Person(val name: String) {\n    var age: Int = 0\n    constructor(name: String, age: Int) : this(name) {\n        this.age = age\n    }\n}"
        )
        methods = [s for s in symbols if s.kind == SymbolKind.METHOD]
        assert len(methods) == 1
        assert methods[0].name == "constructor"

        contains = [e for e in edges if e.kind == EdgeKind.CONTAINS]
        assert any("constructor" in e.target_id for e in contains)

    def test_imports(self):
        _, _, imports = self._parse(
            "import kotlin.collections.List\nimport java.io.File\nfun foo() {}"
        )
        assert len(imports) >= 2
        assert any("List" in imp for imp in imports)
        assert any("File" in imp for imp in imports)

    def test_call_edges(self):
        symbols, edges, _ = self._parse(
            "fun foo() { bar() }\nfun bar() {}"
        )
        calls = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert any(e.target_id == "bar" for e in calls), f"No call edge to bar: {calls}"
