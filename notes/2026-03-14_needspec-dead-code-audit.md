# Dead Code Audit — NeedSpec Production (2026-03-14)

## Summary

Ran `--mode dead` on NeedSpec (534 files, 4035 symbols). Flagged 1091 unused symbols. Manually verified all 61 high-confidence entries + 16 backend entries.

**Accuracy: 24% on high-confidence components, 0% on backend constants.**

Only 11 of 45 high-confidence component flags were truly dead. All backend flags were false positives.

---

## Bug 1 (P0): React.lazy() dynamic imports not traced

**Impact:** 34 false positives — largest source of error. Affects any React codebase using code splitting.

**Root cause in tempograph code:**

`parser.py` line 1113: `lazy` is in the `_BUILTIN_IGNORE` set, so `lazy()` calls are filtered out during `_scan_calls()` and never produce edges. Additionally, `import()` expressions inside arrow functions are not parsed as import statements — `_handle_js_ts()` (lines 354-374) only captures `import_statement` and `import` node types from tree-sitter, which are static imports. Dynamic `import()` is a `call_expression` in the tree-sitter AST, not an `import_statement`.

`builder.py` `_resolve_imports()` (lines 280-369) only processes the raw import strings collected by the parser, which never include dynamic imports.

**Pattern causing false positives:**
```typescript
// App.tsx — 20+ components loaded this way
const AITranslator = lazy(() => import('./components/AITranslator'));
const Settings = lazy(() => import('./components/Settings'));
// ConfigureView.tsx — 10 tabs loaded this way
const ConfigureGeneralTab = lazy(() => import('./ConfigureGeneralTab'));
```

**Fix location:** `parser.py` → `_handle_js_ts()` or `_scan_calls()`

**Fix approach:**
1. In `_scan_calls()` (lines 1171-1215), detect `call_expression` nodes where the callee is `import`. These have tree-sitter structure:
   ```
   call_expression
     import
     arguments
       string / template_string  ← the module path
   ```
2. When found, extract the string argument and add it to `self.imports` (same list as static imports).
3. Remove `"lazy"` from `_BUILTIN_IGNORE` (line 1113) so that `lazy(fn)` calls are tracked. Alternatively, handle `lazy` specially: when `lazy(() => import('./X'))` is detected, treat it as both an `IMPORTS` edge (file-to-file) and a `RENDERS` edge (since lazy components are rendered in JSX).

**Alternative simpler fix in `_handle_js_ts()`:** Add a regex scan for dynamic imports after the tree-sitter walk:
```python
# After existing import handling in _handle_js_ts()
import re
for m in re.finditer(r'''import\(\s*['"]([^'"]+)['"]\s*\)''', self.source):
    self.imports.append(f"from '{m.group(1)}'")  # normalize to static import format
```
This piggybacks on the existing `_resolve_imports()` resolution in `builder.py` which already handles `from '...'` strings.

**Test cases to add:**
- `lazy(() => import('./Foo'))` → should create IMPORTS edge from current file to Foo
- `import('./utils/bar').then(m => m.baz)` → should create IMPORTS edge
- Nested: `lazy(() => import(/* webpackChunkName */ './Foo'))` → should still work

**False positives this would fix (from NeedSpec):**
AITranslator, AnalyticsDashboard, ChangeFeedView, ConfigureView, CoverageDashboard, DependencyGraph, ExportView, GuidedTour, HelpCenter, LearnSidebar, LinksHub, ListView, ListsView, MobileNav, NeedsTableView, ProjectsView, RightPanel, Settings, SketchPadView, SpecsTableView, TraceabilityMatrix, AboutPage, LandingPage, PrivacyPolicy, ConfigureCustomFieldsTab, ConfigureDataTab, ConfigureGeneralTab, ConfigureImportTab, ConfigureNotificationsTab, ConfigurePipelinesTab, ConfigureTagsTab, ConfigureTeamTab, ConfigureTemplatesTab, ConfigureVersionControlTab

---

## Bug 2 (P1): Same-file constant usage not detected for exported top-level symbols

**Impact:** 11 false positives — all backend agent constants.

**Root cause in tempograph code:**

`types.py` → `find_dead_code()` (lines 278-313). The core loop at lines 282-292 builds `referenced_cross_file` by checking edges where `src_file != tgt_file`. For top-level exported symbols (line 300), the check is:
```python
if sym.id not in referenced_cross_file:
    dead.append(sym)
```

This means a top-level exported constant that is ONLY used within its own file is flagged as dead, even if it has active callers. The method DOES track `referenced_any` (all references including same-file) but only uses it for nested symbols (line 306), not for top-level symbols.

**Examples from NeedSpec:**
- `backend/agents/core/controller.py:28` — `CONTROLLER_SYSTEM_PROMPT` defined, used at line 50 in same file
- `backend/agents/core/sme.py:12` — `SME_SYSTEM_PROMPT` defined, used at line 25 in same file
- `backend/agents/core/synthesizer.py:91` — `MAX_SME_CHARS_EACH` defined, used at line 107 in same file
- All 10 agent constants follow this pattern

**Fix location:** `types.py` → `find_dead_code()`, line 300

**Fix approach:** Change the top-level check to also exclude symbols that have same-file references:
```python
# Current (line 300):
if sym.id not in referenced_cross_file:
    dead.append(sym)

# Fixed:
if sym.id not in referenced_cross_file and sym.id not in referenced_any:
    dead.append(sym)
```

Where `referenced_any` already exists — it's computed at lines 293-298:
```python
referenced_any: set[str] = set()
for edge in self.edges:
    if edge.kind == EdgeKind.CONTAINS: continue
    referenced_any.add(edge.target_id)
```

This is a one-line fix. The `referenced_any` set is already computed but only used for method-level dead code (line 306). Just add it to the top-level check too.

**Nuance:** An exported symbol used only within its own file might still be a candidate for un-exporting (making private), but it's not "dead code." Consider adding a separate category: "exported but only used internally — consider un-exporting" with lower confidence.

**False positives this would fix (from NeedSpec):**
SME_DOMAINS, CONTROLLER_SYSTEM_PROMPT, JUDGE_SYSTEM_PROMPT, SME_SYSTEM_PROMPT, SYNTHESIZER_SYSTEM_PROMPT, MAX_SME_CHARS_EACH, MAX_COMBINED_CHARS, WATCHER_SYSTEM_PROMPT, MAX_HISTORY_MESSAGES, EXTRACTOR_SYSTEM_PROMPT, OllamaClient.close (used in main.py lifespan but also same-module)

---

## Bug 3 (P2): Dynamic import().then() chains not traced

**Impact:** 3 false positives — dev utilities.

**Root cause:** Same as Bug 1 — `import()` expressions are not detected.

**Pattern:**
```typescript
if (import.meta.env.DEV) {
  import('./utils/demoDataSeeder').then(m => { window.seedDemoData = m.seedDemoData; });
  import('./utils/cleanupAndReseed').then(m => { window.cleanupAndReseed = m.cleanupAndReseed; });
}
```

**Fix:** Covered by Bug 1 fix — once `import()` expressions are detected, these will produce IMPORTS edges. The `window` assignment is not traceable statically but the file-level import edge is sufficient to mark the target module as "used."

**False positives this would fix:** cleanupAndReseed, seedDemoData, seedMockEdgeCasesProject

---

## Bug 4 (P2): Zustand StateCreator spread pattern not recognized as call

**Impact:** 2 false positives — createBackupSlice, createBranchSlice.

**Root cause in tempograph code:**

`parser.py` → `_scan_calls()` (lines 1171-1215) creates CALLS edges for `call_expression` nodes. The Zustand pattern uses spread syntax with function calls inside an object literal:

```typescript
export const useStore = create<AppState>()((...args) => ({
  ...createBackupSlice(...args),
  ...createBranchSlice(...args),
  ...createProjectSlice(...args),
}))
```

`_scan_calls()` should detect `createBackupSlice(...args)` as a call expression. The issue is likely that the spread `...` wrapper or the fact that the call is inside an object literal prevents tree-sitter from surfacing it as a standard `call_expression`.

**Fix location:** `parser.py` → `_scan_calls()`

**Fix approach:** Check if `spread_element` nodes contain `call_expression` children. Tree-sitter structure:
```
spread_element
  call_expression
    identifier: "createBackupSlice"
    arguments: ...
```

If `_scan_calls()` only walks direct `call_expression` children but not those nested inside `spread_element`, add spread traversal.

**Alternatively**, this may already be covered by the recursive walk in `_scan_calls()` — in which case the issue is in `_resolve_edges()` (builder.py lines 185-277) failing to match `createBackupSlice` to its definition because of scoping. Verify by checking if a CALLS edge exists for `createBackupSlice` in the built graph.

**False positives this would fix:** createBackupSlice, createBranchSlice (and createProjectSlice if flagged at medium confidence)

---

## Bug 5 (P1): Backend Handler class false positive

**Impact:** 1 false positive — the core HTTP server class.

**Root cause:** `Handler` is passed as a class reference (not instantiated with `Handler()`) to `ThreadingHTTPServer`:
```python
httpd = ThreadingHTTPServer((args.host, args.port), Handler)
```

This is a `class_reference` usage, not a `call_expression`. Tempograph's `_scan_calls()` tracks calls (`Foo()`), not references where a class is passed as an argument.

**Fix location:** `parser.py` → `_scan_calls()` or a new `_scan_references()` method.

**Fix approach:** When scanning call arguments, if an argument is a bare `identifier` that matches a class name, create a REFERENCES edge. This is lower priority since it's a Python-specific pattern (class passed as factory to framework).

---

## What was correctly identified

| Finding | Accurate | Action taken |
|---------|----------|-------------|
| Circular import: `types.ts` ↔ `trashSlice.ts` | Yes | Fixed: changed to `import type` |
| BacklogView dead | Yes | Deleted (retired per comment in App.tsx:209) |
| KeyboardShortcutsOverlay dead | Yes | Deleted (replaced by HotkeyHUD per App.tsx:175) |
| Breadcrumbs dead | Yes | Deleted (merged into ProjectHeader per App.tsx:1185) |
| BackupRestore dead | Yes | Deleted (zero references anywhere) |
| NonGoalsEditor dead | Yes | Deleted (zero references anywhere) |
| RiskRegister dead | Yes | Deleted (zero references anywhere) |
| ExcalidrawLayer dead | Yes | Deleted (zero references anywhere) |
| Hotspot complexity rankings | Yes | All 12 extreme-complexity flags verified accurate |
| Confidence scoring | Partially | High-confidence threshold (≥70) too aggressive — many 80-confidence items were false positives |

---

## Confidence scoring adjustment recommendation

The `_dead_code_confidence()` function in `render.py` (lines 883-916) gives +30 for "no callers at all" and +25 for "parent file has no importers." For lazy-loaded components, both conditions are true (no static callers, no static importers), so they score 55+ before size bonuses. Components over 50 lines get +15, pushing them to 70+ (high confidence).

**Suggestion:** Add a negative modifier for files that contain only a single default-exported component (common React pattern). These are almost always consumed via dynamic import. Something like:
```python
# In _dead_code_confidence():
if sym.kind == SymbolKind.COMPONENT and sym.exported:
    siblings = [s for s in graph.symbols.values() if s.file_path == sym.file_path and s.kind == SymbolKind.COMPONENT]
    if len(siblings) == 1:
        score -= 20  # Likely lazy-loaded single-component file
```

---

## Verification methodology

For each flagged symbol, the following checks were performed:
1. `grep -r "ComponentName"` across entire `app/src/` for import statements
2. `grep -r "<ComponentName"` for JSX usage
3. `grep -r "lazy.*import.*ComponentName"` for dynamic imports
4. Manual inspection of `App.tsx`, `main.tsx`, and parent component files for route/view rendering
5. For backend: `grep -r "CONSTANT_NAME"` across `backend/` for intra-file and cross-file usage
6. For utilities: checked `window` exposure patterns and Zustand store composition
