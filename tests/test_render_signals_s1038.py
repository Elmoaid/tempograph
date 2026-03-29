"""Tests for S1038: Component render tree signal in render_focused.

S1038 fires when the focus seed is a React/JSX component with RENDERS edges.
BFS traverses CALLS edges only — JSX component composition via <FooBar /> uses
RENDERS edges that are completely invisible to BFS.

Makes two kinds of relationships visible:
- JSX children: components the seed renders (<Avatar />, <Button />)
- JSX parents: components that render the seed (callers via JSX, not CALLS)

Distinct from:
- S1037 (subclass exposure): INHERITS edges for class hierarchy — not JSX composition
- S65 (change_exposure): caller-file blast risk — not RENDERS edges
"""
from __future__ import annotations
import types
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sym(
    name: str,
    file_path: str = "src/Button.tsx",
    kind: str = "function",
    exported: bool = True,
):
    from tempograph.types import Symbol, SymbolKind, Language
    kmap = {
        "function": SymbolKind.FUNCTION,
        "method": SymbolKind.METHOD,
        "class": SymbolKind.CLASS,
        "variable": SymbolKind.VARIABLE,
    }
    return Symbol(
        id=f"{file_path}::{name}",
        name=name,
        qualified_name=name,
        kind=kmap.get(kind, SymbolKind.FUNCTION),
        language=Language.TYPESCRIPT,
        file_path=file_path,
        line_start=1,
        line_end=30,
        exported=exported,
    )


def _make_graph(
    seed_sym,
    *,
    renders_children=None,   # list of (name, file_path) for resolved children
    renders_unresolved=None, # list of bare names (PascalCase) for unresolved JSX
    rendered_by=None,        # list of (name, file_path) for parents that render seed
    same_file_children=None, # list of (name,) for same-file renders (should be silent)
):
    """Build a minimal fake Tempo for component render tree unit tests."""
    from tempograph.types import EdgeKind

    renders_children = renders_children or []
    renders_unresolved = renders_unresolved or []
    rendered_by = rendered_by or []
    same_file_children = same_file_children or []

    g = types.SimpleNamespace()
    g.symbols = {seed_sym.id: seed_sym}
    g.edges = []
    g.files = {}
    g.hot_files = set()
    g._callers = {}
    g._callees = {}
    g._children = {}
    g._importers = {}
    g._renderers = {}
    g._subtypes = {}

    # Add resolved children (seed renders these via JSX)
    for child_name, child_fp in renders_children:
        child_sym = _make_sym(child_name, file_path=child_fp)
        g.symbols[child_sym.id] = child_sym
        g.edges.append(types.SimpleNamespace(
            kind=EdgeKind.RENDERS,
            source_id=seed_sym.id,
            target_id=child_sym.id,
            line=10,
        ))

    # Add unresolved JSX children (bare PascalCase names not yet resolved to symbol IDs)
    for bare_name in renders_unresolved:
        g.edges.append(types.SimpleNamespace(
            kind=EdgeKind.RENDERS,
            source_id=seed_sym.id,
            target_id=bare_name,
            line=15,
        ))

    # Add parents (components that render the seed)
    for parent_name, parent_fp in rendered_by:
        parent_sym = _make_sym(parent_name, file_path=parent_fp)
        g.symbols[parent_sym.id] = parent_sym
        g._renderers.setdefault(seed_sym.id, []).append(parent_sym.id)

    # Add same-file children (should NOT appear in output — same-file is visible in context)
    for (child_name,) in same_file_children:
        child_sym = _make_sym(child_name, file_path=seed_sym.file_path)
        g.symbols[child_sym.id] = child_sym
        g.edges.append(types.SimpleNamespace(
            kind=EdgeKind.RENDERS,
            source_id=seed_sym.id,
            target_id=child_sym.id,
            line=20,
        ))

    def _renderers_of(sym_id):
        return [g.symbols[s] for s in g._renderers.get(sym_id, []) if s in g.symbols]

    def _callers_of(sym_id):
        return [g.symbols[s] for s in g._callers.get(sym_id, []) if s in g.symbols]

    def _callees_of(sym_id):
        return [g.symbols[s] for s in g._callees.get(sym_id, []) if s in g.symbols]

    def _importers_of(fp):
        return g._importers.get(fp, [])

    def _subtypes_of(name):
        return []

    def _find_symbol(name):
        return [s for s in g.symbols.values() if s.name == name]

    g.renderers_of = _renderers_of
    g.callers_of = _callers_of
    g.callees_of = _callees_of
    g.importers_of = _importers_of
    g.subtypes_of = _subtypes_of
    g.find_symbol = _find_symbol

    return g


# ---------------------------------------------------------------------------
# Unit tests for _compute_component_render_tree
# ---------------------------------------------------------------------------

from tempograph.render.focused import _compute_component_render_tree


class TestComponentRenderTreeUnit:
    # ---- Children (outgoing RENDERS) ----

    def test_fires_single_cross_file_child(self):
        seed = _make_sym("UserCard", file_path="src/UserCard.tsx")
        g = _make_graph(seed, renders_children=[("Avatar", "src/Avatar.tsx")])
        result = _compute_component_render_tree([seed], g)
        assert "JSX renders:" in result
        assert "Avatar" in result
        assert "hidden from BFS" in result

    def test_fires_multiple_children(self):
        seed = _make_sym("Dashboard", file_path="src/Dashboard.tsx")
        g = _make_graph(seed, renders_children=[
            ("Header", "src/Header.tsx"),
            ("Sidebar", "src/Sidebar.tsx"),
            ("Content", "src/Content.tsx"),
        ])
        result = _compute_component_render_tree([seed], g)
        assert "JSX renders:" in result
        assert "Header" in result
        assert "Sidebar" in result
        assert "Content" in result

    def test_overflows_after_four_children(self):
        seed = _make_sym("App", file_path="src/App.tsx")
        g = _make_graph(seed, renders_children=[
            ("NavBar", "src/NavBar.tsx"),
            ("Hero", "src/Hero.tsx"),
            ("Features", "src/Features.tsx"),
            ("Footer", "src/Footer.tsx"),
            ("Modal", "src/Modal.tsx"),
        ])
        result = _compute_component_render_tree([seed], g)
        assert "+1 more" in result

    def test_unresolved_jsx_children_still_appear(self):
        seed = _make_sym("Layout", file_path="src/Layout.tsx")
        g = _make_graph(seed, renders_unresolved=["Navbar", "Footer"])
        result = _compute_component_render_tree([seed], g)
        assert "JSX renders:" in result
        assert "Navbar" in result
        assert "Footer" in result

    def test_silent_for_same_file_children_only(self):
        seed = _make_sym("Card", file_path="src/Card.tsx")
        g = _make_graph(seed, same_file_children=[("CardTitle",), ("CardBody",)])
        result = _compute_component_render_tree([seed], g)
        assert result == ""

    def test_deduplicates_children(self):
        """Same component rendered twice (different JSX sites) should appear once."""
        seed = _make_sym("List", file_path="src/List.tsx")
        child = _make_sym("Item", file_path="src/Item.tsx")
        from tempograph.types import EdgeKind
        g = types.SimpleNamespace()
        g.symbols = {seed.id: seed, child.id: child}
        g.edges = [
            types.SimpleNamespace(kind=EdgeKind.RENDERS, source_id=seed.id, target_id=child.id, line=5),
            types.SimpleNamespace(kind=EdgeKind.RENDERS, source_id=seed.id, target_id=child.id, line=12),
        ]
        g._renderers = {}

        def _renderers_of(sid): return []
        def _callers_of(sid): return []
        g.renderers_of = _renderers_of
        g.callers_of = _callers_of
        result = _compute_component_render_tree([seed], g)
        # "Item" should appear exactly once
        assert result.count("Item") == 1

    # ---- Parents (incoming RENDERS — renderers_of) ----

    def test_fires_single_parent(self):
        seed = _make_sym("Button", file_path="src/Button.tsx")
        g = _make_graph(seed, rendered_by=[("App", "src/App.tsx")])
        result = _compute_component_render_tree([seed], g)
        assert "JSX rendered by:" in result
        assert "App" in result
        assert "props interface" in result

    def test_fires_multiple_parents(self):
        seed = _make_sym("Button", file_path="src/Button.tsx")
        g = _make_graph(seed, rendered_by=[
            ("App", "src/App.tsx"),
            ("Modal", "src/Modal.tsx"),
            ("Form", "src/Form.tsx"),
        ])
        result = _compute_component_render_tree([seed], g)
        assert "JSX rendered by:" in result
        assert "App" in result
        assert "Modal" in result
        assert "Form" in result

    def test_parent_overflow_after_three(self):
        seed = _make_sym("Input", file_path="src/Input.tsx")
        g = _make_graph(seed, rendered_by=[
            ("LoginForm", "src/LoginForm.tsx"),
            ("SearchBar", "src/SearchBar.tsx"),
            ("FilterPanel", "src/FilterPanel.tsx"),
            ("EditProfile", "src/EditProfile.tsx"),
        ])
        result = _compute_component_render_tree([seed], g)
        assert "+1 more" in result

    def test_silent_for_same_file_parents(self):
        """Parents in the same file as seed should not appear."""
        seed = _make_sym("InnerCard", file_path="src/Card.tsx")
        parent = _make_sym("OuterCard", file_path="src/Card.tsx")
        g = types.SimpleNamespace()
        g.symbols = {seed.id: seed, parent.id: parent}
        g.edges = []
        g._renderers = {seed.id: [parent.id]}

        def _renderers_of(sid):
            return [g.symbols[s] for s in g._renderers.get(sid, []) if s in g.symbols]
        def _callers_of(sid): return []
        g.renderers_of = _renderers_of
        g.callers_of = _callers_of
        result = _compute_component_render_tree([seed], g)
        assert result == ""

    # ---- Both children AND parents ----

    def test_fires_both_children_and_parents(self):
        seed = _make_sym("UserCard", file_path="src/UserCard.tsx")
        g = _make_graph(
            seed,
            renders_children=[("Avatar", "src/Avatar.tsx"), ("Badge", "src/Badge.tsx")],
            rendered_by=[("Dashboard", "src/Dashboard.tsx"), ("Profile", "src/Profile.tsx")],
        )
        result = _compute_component_render_tree([seed], g)
        assert "JSX renders:" in result
        assert "Avatar" in result
        assert "JSX rendered by:" in result
        assert "Dashboard" in result

    # ---- Silence conditions ----

    def test_silent_when_no_renders_edges(self):
        seed = _make_sym("NotAComponent", file_path="src/utils.ts")
        g = _make_graph(seed)
        result = _compute_component_render_tree([seed], g)
        assert result == ""

    def test_silent_for_empty_seeds(self):
        from tempograph.types import Tempo, EdgeKind
        g = types.SimpleNamespace()
        g.edges = []
        g._renderers = {}
        g.renderers_of = lambda sid: []
        result = _compute_component_render_tree([], g)
        assert result == ""

    def test_silent_for_test_file_seed(self):
        seed = _make_sym("TestComponent", file_path="tests/TestComponent.test.tsx")
        g = _make_graph(seed, renders_children=[("Button", "src/Button.tsx")])
        result = _compute_component_render_tree([seed], g)
        assert result == ""


# ---------------------------------------------------------------------------
# Integration tests using render_focused
# ---------------------------------------------------------------------------

def _make_jsx_graph_for_integration():
    """Build a minimal graph simulating a React component tree for integration tests."""
    from tempograph.types import Symbol, SymbolKind, Language, Edge, EdgeKind, FileInfo, Tempo

    def _sym(name, fp, kind=SymbolKind.FUNCTION, line_start=1, line_end=20):
        return Symbol(
            id=f"{fp}::{name}",
            name=name,
            qualified_name=name,
            kind=kind,
            language=Language.TYPESCRIPT,
            file_path=fp,
            line_start=line_start,
            line_end=line_end,
            exported=True,
        )

    card = _sym("UserCard", "src/UserCard.tsx")
    avatar = _sym("Avatar", "src/Avatar.tsx")
    badge = _sym("Badge", "src/Badge.tsx")
    dashboard = _sym("Dashboard", "src/Dashboard.tsx")

    symbols = {s.id: s for s in [card, avatar, badge, dashboard]}
    files = {
        "src/UserCard.tsx": FileInfo(path="src/UserCard.tsx", language=Language.TYPESCRIPT, line_count=50, byte_size=1000, symbols=[card.id]),
        "src/Avatar.tsx": FileInfo(path="src/Avatar.tsx", language=Language.TYPESCRIPT, line_count=30, byte_size=500, symbols=[avatar.id]),
        "src/Badge.tsx": FileInfo(path="src/Badge.tsx", language=Language.TYPESCRIPT, line_count=25, byte_size=400, symbols=[badge.id]),
        "src/Dashboard.tsx": FileInfo(path="src/Dashboard.tsx", language=Language.TYPESCRIPT, line_count=80, byte_size=2000, symbols=[dashboard.id]),
    }
    edges = [
        # UserCard renders Avatar and Badge
        Edge(kind=EdgeKind.RENDERS, source_id=card.id, target_id=avatar.id, line=10),
        Edge(kind=EdgeKind.RENDERS, source_id=card.id, target_id=badge.id, line=11),
        # Dashboard renders UserCard
        Edge(kind=EdgeKind.RENDERS, source_id=dashboard.id, target_id=card.id, line=15),
    ]

    g = Tempo(root="/fake/repo", symbols=symbols, files=files, edges=edges)
    g.build_indexes()
    return g, card, avatar, badge, dashboard


class TestComponentRenderTreeIntegration:
    def test_render_focused_shows_jsx_children(self):
        from tempograph.render.focused import render_focused
        g, card, _, _, _ = _make_jsx_graph_for_integration()
        result = render_focused(g, "UserCard")
        assert "JSX renders:" in result
        assert "Avatar" in result
        assert "Badge" in result

    def test_render_focused_shows_jsx_parent(self):
        from tempograph.render.focused import render_focused
        g, card, _, _, dashboard = _make_jsx_graph_for_integration()
        result = render_focused(g, "UserCard")
        assert "JSX rendered by:" in result
        assert "Dashboard" in result

    def test_render_focused_avatar_has_parent_usercard(self):
        """Avatar is rendered by UserCard — the 'rendered by' signal fires for Avatar too."""
        from tempograph.render.focused import render_focused
        g, _, avatar, _, _ = _make_jsx_graph_for_integration()
        # Avatar IS rendered by UserCard (there's a RENDERS edge UserCard→Avatar)
        result = render_focused(g, "Avatar")
        assert "JSX rendered by:" in result
        assert "UserCard" in result

    def test_render_focused_silent_for_renders_signal_not_triggered(self):
        """Dashboard renders UserCard but has no parents — only the child line fires."""
        from tempograph.render.focused import render_focused
        g, _, _, _, dashboard = _make_jsx_graph_for_integration()
        result = render_focused(g, "Dashboard")
        assert "JSX renders:" in result
        assert "UserCard" in result
        # Dashboard has no parents
        assert "JSX rendered by:" not in result
