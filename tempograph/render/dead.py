from __future__ import annotations

from ..types import Tempo, Symbol, SymbolKind
from ._utils import count_tokens, _is_test_file, _dead_code_confidence, _DISPATCH_PATTERNS


def _file_effort_badge(syms: list[tuple[Symbol, int]], graph: Tempo) -> str:
    """Compute cleanup effort badge for a file group of dead symbols.

    Effort score = dead_count * (1 + avg_external_caller_weight), where symbols
    with 0 external callers are weighted as 0.5 (trivially removable).
    Tiers: HIGH > 10, MEDIUM 4-10, LOW < 4.
    """
    if not syms:
        return ""
    weights = []
    for sym, _conf in syms:
        ext_callers = sum(
            1 for c in graph.callers_of(sym.id) if c.file_path != sym.file_path
        )
        weights.append(0.5 if ext_callers == 0 else float(ext_callers))
    avg_w = sum(weights) / len(weights)
    score = len(syms) * (1.0 + avg_w)
    if score > 10:
        label = "HIGH"
    elif score >= 4:
        label = "MEDIUM"
    else:
        label = "LOW"
    return f" [effort: {label}]"


def _core_private_module_breakdown(
    graph: Tempo, scored: list[tuple[Symbol, int]], dead: list[Symbol], lines: list[str]
) -> None:
    """S76 private dead hint + S123 dead-by-module breakdown."""
    # S76: Private dead hint — non-exported functions/methods with 0 callers.
    _private_dead_count = 0
    for _pd_sym in graph.symbols.values():
        if _pd_sym.exported or _is_test_file(_pd_sym.file_path):
            continue
        if _pd_sym.kind.value not in ("function", "method"):
            continue
        if not graph.callers_of(_pd_sym.id) and _pd_sym.line_count >= 2:
            _private_dead_count += 1
    if _private_dead_count >= 3:
        lines.append(f"Private dead: {_private_dead_count} non-exported symbols with 0 callers (not shown here)")

    # S123: Dead-by-module breakdown — which top-level directories carry the most dead code.
    if len(dead) >= 8:
        _dead_by_module: dict[str, int] = {}
        for _dm_sym, _dm_conf in scored:
            if _dm_conf < 40:
                continue
            _parts = _dm_sym.file_path.split("/")
            _mod = _parts[0] if len(_parts) > 1 else "."
            _dead_by_module[_mod] = _dead_by_module.get(_mod, 0) + 1
        _module_items = sorted(_dead_by_module.items(), key=lambda x: -x[1])
        if len(_module_items) >= 2:
            _mb_parts = [f"{mod}/ ({cnt})" for mod, cnt in _module_items[:4]]
            lines.append(f"dead by module: {', '.join(_mb_parts)}")


def _core_name_patterns_a(scored: list[tuple[Symbol, int]], lines: list[str]) -> None:
    """S159 dead constants + S202 error handlers + S208 callbacks + S218 initializers."""
    # S159: Dead constants — unused constant/variable declarations.
    _dead_consts = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("constant", "variable")
    ]
    if len(_dead_consts) >= 3:
        _dc_names = [s.name for s in _dead_consts[:3]]
        _dc_str = ", ".join(_dc_names)
        if len(_dead_consts) > 3:
            _dc_str += f" +{len(_dead_consts) - 3} more"
        lines.append(f"dead constants: {len(_dead_consts)} unused constants/variables ({_dc_str})")

    # S202: Dead error handlers — error-handling functions that are dead.
    _s202_error_patterns = ("handle_", "on_error", "catch_", "except_", "error_handler")
    _s202_dead_handlers = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) or sym.name.endswith("_error") or "error_handler" in sym.name
                for p in _s202_error_patterns)
    ]
    if len(_s202_dead_handlers) >= 1:
        _eh_names = [s.name for s in _s202_dead_handlers[:3]]
        _eh_str = ", ".join(_eh_names)
        if len(_s202_dead_handlers) > 3:
            _eh_str += f" +{len(_s202_dead_handlers) - 3} more"
        lines.append(
            f"dead error handlers: {len(_s202_dead_handlers)} unused error handler(s) ({_eh_str})"
            f" — missing error recovery"
        )

    # S208: Dead callbacks — callback/handler/listener/hook functions that are dead.
    _s208_cb_patterns = ("_callback", "_handler", "_listener", "_hook")
    _s208_dead_cbs = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.endswith(p) for p in _s208_cb_patterns)
    ]
    if len(_s208_dead_cbs) >= 1:
        _cb_names = [s.name for s in _s208_dead_cbs[:3]]
        _cb_str = ", ".join(_cb_names)
        if len(_s208_dead_cbs) > 3:
            _cb_str += f" +{len(_s208_dead_cbs) - 3} more"
        lines.append(
            f"dead callbacks: {len(_s208_dead_cbs)} unused callback/handler fn(s) ({_cb_str})"
            f" — event wiring may have been removed"
        )

    # S218: Dead initializers — init/setup/configure functions with 0 callers.
    _s218_init_patterns = ("init_", "initialize_", "setup_app", "configure_", "bootstrap_", "startup_")
    _s218_dead_inits = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s218_init_patterns)
    ]
    if len(_s218_dead_inits) >= 1:
        _init_names = [s.name for s in _s218_dead_inits[:3]]
        _init_str = ", ".join(_init_names)
        if len(_s218_dead_inits) > 3:
            _init_str += f" +{len(_s218_dead_inits) - 3} more"
        lines.append(
            f"dead initializers: {len(_s218_dead_inits)} unused init/setup fn(s) ({_init_str})"
            f" — abandoned initialization paths"
        )


def _core_name_patterns_b(scored: list[tuple[Symbol, int]], lines: list[str]) -> None:
    """S232 dead serializers + S238 middleware + S225 validators + S196 fixtures."""
    # S232: Dead serializers — serialize/to_dict/from_dict/to_json fns with 0 callers.
    _s232_ser_patterns = ("serialize_", "deserialize_", "to_dict", "from_dict",
                          "to_json", "from_json", "to_xml", "from_xml", "marshal_", "unmarshal_")
    _s232_dead_sers = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) or sym.name == p for p in _s232_ser_patterns)
    ]
    if len(_s232_dead_sers) >= 1:
        _ser_names = [s.name for s in _s232_dead_sers[:3]]
        _ser_str = ", ".join(_ser_names)
        if len(_s232_dead_sers) > 3:
            _ser_str += f" +{len(_s232_dead_sers) - 3} more"
        lines.append(
            f"dead serializers: {len(_s232_dead_sers)} unused serialize/marshal fn(s) ({_ser_str})"
            f" — abandoned API shapes or migration leftovers"
        )

    # S238: Dead middleware — middleware/before_*/after_* functions that are dead.
    _s238_mw_patterns = ("middleware_", "before_", "after_", "pre_", "post_",
                          "intercept_", "filter_", "on_request", "on_response")
    _s238_dead_mw = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s238_mw_patterns)
    ]
    if len(_s238_dead_mw) >= 1:
        _mw_names = [s.name for s in _s238_dead_mw[:3]]
        _mw_str = ", ".join(_mw_names)
        if len(_s238_dead_mw) > 3:
            _mw_str += f" +{len(_s238_dead_mw) - 3} more"
        lines.append(
            f"dead middleware: {len(_s238_dead_mw)} unused middleware fn(s) ({_mw_str})"
            f" — request pipeline wiring may have been removed"
        )

    # S225: Dead validators — validate_*/check_* functions with 0 callers.
    _s225_val_patterns = ("validate_", "check_", "verify_", "assert_", "ensure_")
    _s225_dead_vals = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s225_val_patterns)
    ]
    if len(_s225_dead_vals) >= 2:
        _val_names = [s.name for s in _s225_dead_vals[:3]]
        _val_str = ", ".join(_val_names)
        if len(_s225_dead_vals) > 3:
            _val_str += f" +{len(_s225_dead_vals) - 3} more"
        lines.append(
            f"dead validators: {len(_s225_dead_vals)} unused validate/check fn(s) ({_val_str})"
            f" — removed feature gates or abandoned integrity checks"
        )

    # S196: Dead fixtures — setup_*/teardown_* functions that are dead.
    _s196_dead_fixtures = [
        sym for sym, conf in scored
        if conf >= 40
        and sym.kind.value in ("function", "method")
        and (
            sym.name.startswith("setup_") or sym.name.startswith("teardown_")
            or sym.name.startswith("fixture_") or sym.name.startswith("create_")
        )
    ]
    if len(_s196_dead_fixtures) >= 2:
        _fix_names = [s.name for s in _s196_dead_fixtures[:3]]
        _fix_str = ", ".join(_fix_names)
        if len(_s196_dead_fixtures) > 3:
            _fix_str += f" +{len(_s196_dead_fixtures) - 3} more"
        lines.append(
            f"dead fixtures: {len(_s196_dead_fixtures)} unused setup/teardown/fixture fns ({_fix_str})"
        )


def _core_class_methods(graph: Tempo, scored: list[tuple[Symbol, int]], lines: list[str]) -> None:
    """S190 dead overrides + S184 dead accessors + S172 dead classes + S166 zombie methods."""
    # S190: Dead overrides — methods in a live class that override a parent method but have 0 callers.
    # S46-precompute: Invert _subtypes (27 entries, 31 children) instead of scanning 30,695 edges.
    # Before: 30,695 × enum.kind.value = 2.58ms. After: 27-entry dict inversion = 2.3µs (1,146×).
    _s190_inherits_parent: dict[str, str] = {
        cid: pid
        for pid, cids in graph._subtypes.items()
        if pid in graph.symbols
        for cid in cids
    }
    _s190_dead_overrides: list[str] = []
    for _cls190 in graph.symbols.values():
        if _cls190.kind.value != "class" or _is_test_file(_cls190.file_path):
            continue
        # Class must be live: at least one method has cross-file callers
        _cls190_children = graph.children_of(_cls190.id)
        if not any(
            any(c.file_path != _cls190.file_path for c in graph.callers_of(m.id))
            for m in _cls190_children
            if m.kind.value == "method"
        ):
            continue
        # Find parent class via precomputed INHERITS index (indexed O(1) vs edge scan)
        _parent190_id = _s190_inherits_parent.get(_cls190.id)
        _parent190 = graph.symbols[_parent190_id] if _parent190_id else None
        if _parent190 is None:
            continue
        _parent_method_names190 = {
            s.name for s in graph.children_of(_parent190.id)
            if s.kind.value == "method"
        }
        for _child190 in graph.children_of(_cls190.id):
            if (
                _child190.kind.value == "method"
                and _child190.name in _parent_method_names190
                and len(graph.callers_of(_child190.id)) == 0
            ):
                _s190_dead_overrides.append(_child190.name)
    if len(_s190_dead_overrides) >= 1:
        _ov_str = ", ".join(list(dict.fromkeys(_s190_dead_overrides))[:3])
        if len(_s190_dead_overrides) > 3:
            _ov_str += f" +{len(_s190_dead_overrides) - 3} more"
        lines.append(
            f"dead overrides: {len(_s190_dead_overrides)} override method(s) unused ({_ov_str})"
        )

    # S184: Dead getters/setters — accessor methods (get_*/set_*) that are dead.
    _s184_dead_accessors = [
        sym for sym, conf in scored
        if conf >= 15
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and (sym.name.startswith("get_") or sym.name.startswith("set_"))
    ]
    if len(_s184_dead_accessors) >= 2:
        _acc_names = [s.name for s in _s184_dead_accessors[:3]]
        _acc_str = ", ".join(_acc_names)
        if len(_s184_dead_accessors) > 3:
            _acc_str += f" +{len(_s184_dead_accessors) - 3} more"
        lines.append(
            f"dead accessors: {len(_s184_dead_accessors)} dead getter/setter methods ({_acc_str})"
        )

    # S172: Dead class — a class with conf >= 40 that contains at least 1 method.
    _s172_dead_classes: list[str] = []
    for _cls172, _conf172 in scored:
        if _conf172 < 40:
            continue
        if _is_test_file(_cls172.file_path):
            continue
        if _cls172.kind.value != "class":
            continue
        _methods172 = [
            ch for ch in graph.children_of(_cls172.id)
            if ch.kind.value == "method"
        ]
        if _methods172:
            _s172_dead_classes.append(_cls172.name)
    if len(_s172_dead_classes) >= 1:
        _dclass_str = ", ".join(_s172_dead_classes[:3])
        if len(_s172_dead_classes) > 3:
            _dclass_str += f" +{len(_s172_dead_classes) - 3} more"
        lines.append(f"dead classes: {len(_s172_dead_classes)} fully-dead class(es) ({_dclass_str})")

    # S166: Zombie methods — dead methods that belong to classes with active (live) callers.
    _s166_zombies: list[str] = []
    for _sym166, _conf166 in scored:
        if _conf166 < 40:
            continue
        if _is_test_file(_sym166.file_path):
            continue
        if _sym166.kind.value != "method":
            continue
        # Find the parent class via parent_id (indexed, avoids edge scan)
        _parent_cls166 = (
            graph.symbols[_sym166.parent_id]
            if _sym166.parent_id and _sym166.parent_id in graph.symbols
            and graph.symbols[_sym166.parent_id].kind.value == "class"
            else None
        )
        if _parent_cls166 is not None and len(graph.callers_of(_parent_cls166.id)) > 0:
            _s166_zombies.append(_sym166.name)
    if len(_s166_zombies) >= 2:
        _z_str = ", ".join(_s166_zombies[:3])
        if len(_s166_zombies) > 3:
            _z_str += f" +{len(_s166_zombies) - 3} more"
        lines.append(f"zombie methods: {len(_s166_zombies)} dead methods in live classes ({_z_str})")


def _core_size_exports_files(graph: Tempo, scored: list[tuple[Symbol, int]], lines: list[str]) -> None:
    """S178 dead exports + S148 largest dead fn + S140 dead test helpers + S153 whole-file dead."""
    # S178: Dead exports — exported functions that have 0 callers and confidence >= 40.
    _s178_dead_exports = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.exported
        and sym.kind.value in ("function", "method")
        and len(graph.callers_of(sym.id)) == 0
    ]
    if len(_s178_dead_exports) >= 3:
        _de_names = [s.name for s in _s178_dead_exports[:3]]
        _de_str = ", ".join(_de_names)
        if len(_s178_dead_exports) > 3:
            _de_str += f" +{len(_s178_dead_exports) - 3} more"
        lines.append(f"dead exports: {len(_s178_dead_exports)} exported fns with 0 callers ({_de_str})")

    # S148: Largest dead fn — the single biggest dead symbol by line count.
    _src_dead_fns = [
        (sym, conf) for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and sym.line_count >= 20
    ]
    if _src_dead_fns:
        _biggest_sym = max(_src_dead_fns, key=lambda x: x[0].line_count)[0]
        lines.append(
            f"largest dead fn: {_biggest_sym.line_count}L {_biggest_sym.name}"
            f" in {_biggest_sym.file_path.rsplit('/', 1)[-1]} — consider removing"
        )

    # S140: Dead test helpers — unused functions defined in test files (not fixtures/conftest).
    _dead_test_helpers = [
        sym for sym, conf in scored
        if conf >= 10
        and _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and not sym.name.startswith("test_")
        and not sym.name.startswith("Test")
        and sym.name not in ("setUp", "tearDown", "setUpClass", "tearDownClass")
    ]
    if len(_dead_test_helpers) >= 3:
        _dth_names = [s.name for s in _dead_test_helpers[:3]]
        _dth_str = ", ".join(_dth_names)
        if len(_dead_test_helpers) > 3:
            _dth_str += f" +{len(_dead_test_helpers) - 3} more"
        lines.append(f"dead test helpers: {len(_dead_test_helpers)} unused helper fns in test files ({_dth_str})")

    # S153: Whole-file dead — source files where every symbol is a dead code candidate.
    _dead_sym_files: dict[str, int] = {}
    for sym, conf in scored:
        if conf >= 40 and not _is_test_file(sym.file_path):
            _dead_sym_files[sym.file_path] = _dead_sym_files.get(sym.file_path, 0) + 1
    _whole_file_dead: list[str] = []
    for _wf_fp, _wf_dead_count in _dead_sym_files.items():
        _wf_fi = graph.files.get(_wf_fp)
        if not _wf_fi:
            continue
        # Count total src symbols (not just dead ones) in this file
        _wf_total = sum(
            1 for sid in _wf_fi.symbols
            if sid in graph.symbols
            and graph.symbols[sid].kind.value in ("function", "method", "class")
        )
        if _wf_total >= 2 and _wf_dead_count >= _wf_total:
            _whole_file_dead.append(_wf_fp)
    if len(_whole_file_dead) >= 2:
        _wfd_names = [fp.rsplit("/", 1)[-1] for fp in sorted(_whole_file_dead)[:3]]
        _wfd_str = ", ".join(_wfd_names)
        if len(_whole_file_dead) > 3:
            _wfd_str += f" +{len(_whole_file_dead) - 3} more"
        lines.append(f"whole-file dead: {len(_whole_file_dead)} files fully dead ({_wfd_str}) — candidates for deletion")


def _signals_dead_core(graph: Tempo, scored: list[tuple[Symbol, int]], dead: list[Symbol], lines: list[str]) -> None:
    """Dead code signals S76-S153: structural/type signals — private dead, module breakdown, constants, error handlers, callbacks, initializers, overrides, exports, zombie methods, test helpers, whole-file dead."""
    _core_private_module_breakdown(graph, scored, dead, lines)
    _core_name_patterns_a(scored, lines)
    _core_name_patterns_b(scored, lines)
    _core_class_methods(graph, scored, lines)
    _core_size_exports_files(graph, scored, lines)


def _patterns_a_cfg_types(
    scored: list[tuple[Symbol, int]],
    lines: list[str],
    _fn_candidates: list[tuple[Symbol, int]],
    _fn_candidates_40: list[tuple[Symbol, int]],
) -> None:
    """S241-S279: config, exceptions, types, CLI commands, event handlers, async functions."""
    # S241: Dead config/settings — config_*/settings_*/get_config/load_config functions with 0 callers.
    _s241_cfg_patterns = ("config_", "settings_", "get_config", "load_config", "get_setting",
                          "load_settings", "parse_config", "read_config")
    _s241_dead_cfg = [
        sym for sym, conf in _fn_candidates_40
        if any(sym.name.lower().startswith(p) for p in _s241_cfg_patterns)
    ]
    if len(_s241_dead_cfg) >= 2:
        _cfg_str = ", ".join(s.name for s in _s241_dead_cfg[:3])
        if len(_s241_dead_cfg) > 3:
            _cfg_str += f" +{len(_s241_dead_cfg) - 3} more"
        lines.append(
            f"dead config: {len(_s241_dead_cfg)} unused config fn(s) ({_cfg_str})"
            f" — removed feature configurations not yet cleaned up"
        )

    # S248: Dead exception classes — custom exception classes with 0 raise/except sites.
    _s248_exc_indicators = ("error", "exception", "err", "exc", "fault", "failure")
    _s248_dead_exc = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value == "class"
        and any(sym.name.lower().endswith(ind) for ind in _s248_exc_indicators)
    ]
    if len(_s248_dead_exc) >= 2:
        _exc_str = ", ".join(s.name for s in _s248_dead_exc[:3])
        if len(_s248_dead_exc) > 3:
            _exc_str += f" +{len(_s248_dead_exc) - 3} more"
        lines.append(
            f"dead exceptions: {len(_s248_dead_exc)} unused exception class(es) ({_exc_str})"
            f" — removed error paths not yet cleaned up"
        )

    # S257: Dead type definitions — Schema/DTO/Request/Response/Config classes with 0 callers.
    _s257_type_suffixes = ("schema", "dto", "request", "response", "config", "settings",
                           "payload", "params", "options", "data", "model", "spec")
    _s257_dead_types = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value == "class"
        and any(sym.name.lower().endswith(ind) for ind in _s257_type_suffixes)
    ]
    if len(_s257_dead_types) >= 2:
        _type_str = ", ".join(s.name for s in _s257_dead_types[:3])
        if len(_s257_dead_types) > 3:
            _type_str += f" +{len(_s257_dead_types) - 3} more"
        lines.append(
            f"dead type defs: {len(_s257_dead_types)} unused type class(es) ({_type_str})"
            f" — removed data contracts not yet cleaned up"
        )

    # S264: Dead CLI commands — cmd_*/command_*/do_* functions with 0 callers.
    _s264_cmd_prefixes = ("cmd_", "command_", "do_", "run_cmd", "execute_", "action_", "subcommand_")
    _s264_dead_cmds = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s264_cmd_prefixes)
    ]
    if len(_s264_dead_cmds) >= 2:
        _cmd_str = ", ".join(s.name for s in _s264_dead_cmds[:3])
        if len(_s264_dead_cmds) > 3:
            _cmd_str += f" +{len(_s264_dead_cmds) - 3} more"
        lines.append(
            f"dead CLI commands: {len(_s264_dead_cmds)} unused command handler(s) ({_cmd_str})"
            f" — subcommand removed but handler not cleaned up"
        )

    # S270: Dead event handlers — on_*/handle_*/listener_* functions with 0 callers (conf >= 40).
    _s270_evt_prefixes = ("on_", "handle_", "listener_", "observer_", "subscriber_",
                          "on_message", "on_event", "on_change", "on_error", "on_connect")
    _s270_dead_evt = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s270_evt_prefixes)
    ]
    if len(_s270_dead_evt) >= 2:
        _evt_str = ", ".join(s.name for s in _s270_dead_evt[:3])
        if len(_s270_dead_evt) > 3:
            _evt_str += f" +{len(_s270_dead_evt) - 3} more"
        lines.append(
            f"dead event handlers: {len(_s270_dead_evt)} unused event handler(s) ({_evt_str})"
            f" — event subscription may have been removed or silently detached"
        )

    # S279: Dead async functions — async def functions with 0 callers (conf >= 40).
    _s279_dead_async = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and sym.signature and "async" in sym.signature.lower()
    ]
    if len(_s279_dead_async) >= 2:
        _async_str = ", ".join(s.name for s in _s279_dead_async[:3])
        if len(_s279_dead_async) > 3:
            _async_str += f" +{len(_s279_dead_async) - 3} more"
        lines.append(
            f"dead async fns: {len(_s279_dead_async)} unused async function(s) ({_async_str})"
            f" — may be detached coroutines or removed event loop callbacks"
        )


def _patterns_a_factories_adapters(
    scored: list[tuple[Symbol, int]],
    lines: list[str],
    _fn_candidates: list[tuple[Symbol, int]],
    _fn_candidates_40: list[tuple[Symbol, int]],
) -> None:
    """S285-S310: factories, getters, validators, middleware, serializers, adapters."""
    # S285: Dead factory functions — create_*/make_*/build_* functions with 0 callers.
    _s285_factory_prefixes = ("create_", "make_", "build_", "construct_", "instantiate_",
                              "new_", "factory_", "get_or_create_")
    _s285_dead_factories = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s285_factory_prefixes)
    ]
    if len(_s285_dead_factories) >= 2:
        _fac_str = ", ".join(s.name for s in _s285_dead_factories[:3])
        if len(_s285_dead_factories) > 3:
            _fac_str += f" +{len(_s285_dead_factories) - 3} more"
        lines.append(
            f"dead factories: {len(_s285_dead_factories)} unused factory fn(s) ({_fac_str})"
            f" — object creation paths removed or replaced; safe to clean up"
        )

    # S291: Dead property getters — get_*/fetch_*/retrieve_* methods with 0 callers.
    _s291_getter_prefixes = ("get_", "fetch_", "retrieve_", "load_", "read_", "query_")
    _s291_dead_getters = [
        sym for sym, conf in scored
        if conf >= 40
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _s291_getter_prefixes)
    ]
    if len(_s291_dead_getters) >= 3:
        _getter_str = ", ".join(s.name for s in _s291_dead_getters[:3])
        if len(_s291_dead_getters) > 3:
            _getter_str += f" +{len(_s291_dead_getters) - 3} more"
        lines.append(
            f"dead getters: {len(_s291_dead_getters)} unused getter fn(s) ({_getter_str})"
            f" — data access paths removed; safe to clean up API surface"
        )

    # S297: Dead validators — validate_*/check_*/verify_*/ensure_* functions with 0 callers.
    _s297_val_prefixes = ("validate_", "check_", "verify_", "ensure_", "assert_", "is_valid_")
    _s297_dead_vals = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s297_val_prefixes)
    ]
    if len(_s297_dead_vals) >= 3:
        _val_str = ", ".join(s.name for s in _s297_dead_vals[:3])
        if len(_s297_dead_vals) > 3:
            _val_str += f" +{len(_s297_dead_vals) - 3} more"
        lines.append(
            f"dead validators: {len(_s297_dead_vals)} unused validation fn(s) ({_val_str})"
            f" — removed features leave orphaned guards; misleading if left in codebase"
        )

    # S298: Dead middleware — middleware_*/interceptor_*/before_*/after_* functions with 0 callers.
    _s298_mw_prefixes = (
        "middleware_", "interceptor_", "before_request", "after_request",
        "pre_", "post_process", "apply_filter", "handle_request",
    )
    _s298_dead_mw = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s298_mw_prefixes)
    ]
    if len(_s298_dead_mw) >= 2:
        _mw_names = ", ".join(s.name for s in _s298_dead_mw[:3])
        if len(_s298_dead_mw) > 3:
            _mw_names += f" +{len(_s298_dead_mw) - 3} more"
        lines.append(
            f"dead middleware: {len(_s298_dead_mw)} unused middleware fn(s) ({_mw_names})"
            f" — orphaned filters; request lifecycle looks different than it is"
        )

    # S304: Dead serializers — to_dict/to_json/serialize/marshal methods with 0 callers.
    _s304_ser_patterns = (
        "to_dict", "to_json", "to_yaml", "serialize", "marshal",
        "encode", "to_proto", "to_pb", "as_dict", "dump",
    )
    _s304_dead_ser = [
        sym for sym, conf in scored
        if conf >= 30
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.lower() == p or sym.name.lower().startswith(p + "_") for p in _s304_ser_patterns)
    ]
    if len(_s304_dead_ser) >= 2:
        _ser_names = ", ".join(s.name for s in _s304_dead_ser[:3])
        if len(_s304_dead_ser) > 3:
            _ser_names += f" +{len(_s304_dead_ser) - 3} more"
        lines.append(
            f"dead serializers: {len(_s304_dead_ser)} unused serialization fn(s) ({_ser_names})"
            f" — stale data representations; may reflect a removed API endpoint"
        )

    # S310: Dead adapters — adapter_*/converter_*/transformer_*/formatter_* functions with 0 callers.
    _s310_adapt_prefixes = (
        "adapt_", "adapter_", "convert_", "converter_", "transform_",
        "transformer_", "format_", "formatter_", "translate_",
    )
    _s310_dead_adapt = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s310_adapt_prefixes)
    ]
    if len(_s310_dead_adapt) >= 2:
        _adapt_names = ", ".join(s.name for s in _s310_dead_adapt[:3])
        if len(_s310_dead_adapt) > 3:
            _adapt_names += f" +{len(_s310_dead_adapt) - 3} more"
        lines.append(
            f"dead adapters: {len(_s310_dead_adapt)} unused adapter fn(s) ({_adapt_names})"
            f" — removed integrations; implies features that no longer exist"
        )


def _patterns_a_security_lifecycle(
    scored: list[tuple[Symbol, int]],
    lines: list[str],
    _fn_candidates: list[tuple[Symbol, int]],
    _fn_candidates_40: list[tuple[Symbol, int]],
) -> None:
    """S315-S347: rate-limiters, auth, notifications, state handlers, scheduled tasks, migrations."""
    # S315: Dead rate-limiters — rate_limit_*/throttle_*/debounce_* functions with 0 callers.
    _s315_rl_prefixes = (
        "rate_limit_", "throttle_", "debounce_", "limit_", "rate_check_", "quota_",
    )
    _s315_dead_rl = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s315_rl_prefixes)
    ]
    if len(_s315_dead_rl) >= 2:
        _rl_names = ", ".join(s.name for s in _s315_dead_rl[:3])
        if len(_s315_dead_rl) > 3:
            _rl_names += f" +{len(_s315_dead_rl) - 3} more"
        lines.append(
            f"dead rate-limiters: {len(_s315_dead_rl)} unused throttle/limit fn(s) ({_rl_names})"
            f" — removed rate controls; verify endpoint is still protected"
        )

    # S321: Dead auth functions — auth_*/authenticate_*/authorize_* with 0 callers (conf >= 40).
    _s321_auth_prefixes = (
        "auth_", "authenticate_", "authorize_", "check_auth", "verify_auth",
        "require_auth", "require_permission", "has_permission", "is_authorized",
    )
    _s321_dead_auth = [
        sym for sym, conf in _fn_candidates_40
        if any(sym.name.lower().startswith(p) for p in _s321_auth_prefixes)
    ]
    if _s321_dead_auth:
        _auth_names = ", ".join(s.name for s in _s321_dead_auth[:3])
        if len(_s321_dead_auth) > 3:
            _auth_names += f" +{len(_s321_dead_auth) - 3} more"
        lines.append(
            f"dead auth: {len(_s321_dead_auth)} unused auth fn(s) ({_auth_names})"
            f" — removed security check; verify endpoint is still protected before removing"
        )

    # S329: Dead notification functions — notify_*/send_notification_*/alert_* with 0 callers.
    _s329_notif_prefixes = (
        "notify_", "send_notification", "send_alert_", "alert_", "dispatch_event_",
        "emit_event_", "publish_", "broadcast_",
    )
    _s329_dead_notif = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s329_notif_prefixes)
    ]
    if len(_s329_dead_notif) >= 2:
        _notif_names = ", ".join(s.name for s in _s329_dead_notif[:3])
        if len(_s329_dead_notif) > 3:
            _notif_names += f" +{len(_s329_dead_notif) - 3} more"
        lines.append(
            f"dead notifications: {len(_s329_dead_notif)} unused notification fn(s)"
            f" ({_notif_names})"
            f" — removed event path; users may still expect these notifications"
        )

    # S335: Dead state handlers — on_enter_*/on_exit_*/transition_* functions with 0 callers.
    _s335_state_prefixes = (
        "on_enter_", "on_exit_", "on_leave_", "transition_", "on_transition_",
        "handle_state_", "state_", "enter_state_",
    )
    _s335_dead_state = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s335_state_prefixes)
    ]
    if len(_s335_dead_state) >= 2:
        _state_names = ", ".join(s.name for s in _s335_dead_state[:3])
        if len(_s335_dead_state) > 3:
            _state_names += f" +{len(_s335_dead_state) - 3} more"
        lines.append(
            f"dead state handlers: {len(_s335_dead_state)} unused state transition fn(s)"
            f" ({_state_names})"
            f" — removed state or transition; state machine model may be inaccurate"
        )

    # S341: Dead scheduled tasks — task_*/cron_*/scheduled_*/periodic_* functions with 0 callers.
    _s341_task_prefixes = (
        "task_", "cron_", "scheduled_", "periodic_", "job_", "background_task_",
    )
    _s341_dead_tasks = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s341_task_prefixes)
    ]
    if len(_s341_dead_tasks) >= 2:
        _task_names = ", ".join(s.name for s in _s341_dead_tasks[:3])
        if len(_s341_dead_tasks) > 3:
            _task_names += f" +{len(_s341_dead_tasks) - 3} more"
        lines.append(
            f"dead scheduled tasks: {len(_s341_dead_tasks)} unused task fn(s) ({_task_names})"
            f" — may still be registered in scheduler; deregister before removing"
        )

    # S347: Dead migration helpers — migrate_*/upgrade_*/downgrade_* functions with 0 callers.
    _s347_mig_prefixes = (
        "migrate_", "upgrade_", "downgrade_", "rollback_", "apply_migration_",
        "revert_migration_", "run_migration_",
    )
    _s347_dead_mig = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s347_mig_prefixes)
    ]
    if len(_s347_dead_mig) >= 1:
        _mig_names = ", ".join(s.name for s in _s347_dead_mig[:3])
        if len(_s347_dead_mig) > 3:
            _mig_names += f" +{len(_s347_dead_mig) - 3} more"
        lines.append(
            f"dead migration helpers: {len(_s347_dead_mig)} unused migration fn(s) ({_mig_names})"
            f" — check if registered in migration history; remove from both code and migration registry"
        )


def _patterns_a_parsers_accessors(
    scored: list[tuple[Symbol, int]],
    lines: list[str],
    _fn_candidates: list[tuple[Symbol, int]],
    _fn_candidates_40: list[tuple[Symbol, int]],
) -> None:
    """S378, S372, S366, S360: parsers, serializers, property accessor pairs, event handlers."""
    # S378: Dead parsers — parse_*/decode_*/deserialize_* functions with 0 callers.
    _s378_parser_prefixes = (
        "parse_", "decode_", "deserialize_", "from_json_", "from_dict_",
        "from_string_", "load_from_", "read_from_",
    )
    _s378_dead_parsers = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s378_parser_prefixes)
    ]
    if len(_s378_dead_parsers) >= 2:
        _parser_names = ", ".join(s.name for s in _s378_dead_parsers[:3])
        if len(_s378_dead_parsers) > 3:
            _parser_names += f" +{len(_s378_dead_parsers) - 3} more"
        lines.append(
            f"dead parsers: {len(_s378_dead_parsers)} unused parser fn(s) ({_parser_names})"
            f" — unintegrated format parsers; creates false impression that format is supported"
        )

    # S372: Dead serializers — to_dict/to_json/serialize/as_dict functions with 0 callers.
    _s372_ser_names = (
        "to_dict", "to_json", "serialize", "as_dict", "as_json",
        "to_payload", "to_response", "marshal", "dump",
    )
    _s372_dead_sers = [
        sym for sym, conf in scored
        if conf >= 30
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.lower() == n or sym.name.lower().startswith(n + "_") for n in _s372_ser_names)
    ]
    if len(_s372_dead_sers) >= 2:
        _ser_names = ", ".join(s.name for s in _s372_dead_sers[:3])
        if len(_s372_dead_sers) > 3:
            _ser_names += f" +{len(_s372_dead_sers) - 3} more"
        lines.append(
            f"dead serializers: {len(_s372_dead_sers)} unused serializer(s) ({_ser_names})"
            f" — may represent removed endpoints or deprecated formats; remove from public API surface"
        )

    # S366: Dead property accessors — get_*/set_* pairs where both are unused.
    _s366_get_names = {
        sym.name[4:]: sym
        for sym, conf in scored
        if conf >= 30
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and sym.name.lower().startswith("get_")
    }
    _s366_set_names = {
        sym.name[4:]: sym
        for sym, conf in scored
        if conf >= 30
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and sym.name.lower().startswith("set_")
    }
    _s366_dead_pairs = [
        (_s366_get_names[k], _s366_set_names[k])
        for k in _s366_get_names
        if k in _s366_set_names
    ]
    if _s366_dead_pairs:
        _pair_str = ", ".join(
            f"get_{k}/{_s366_set_names[k].name}" for k in list(_s366_get_names)[:2] if k in _s366_set_names
        )
        lines.append(
            f"dead accessors: {len(_s366_dead_pairs)} unused get/set pair(s) ({_pair_str})"
            f" — accessor pairs suggest a removed property; delete both or restore the underlying attribute"
        )

    # S360: Dead event handlers — on_*/handle_*/listen_* functions with 0 callers (conf >= 30).
    _s360_ev_prefixes = (
        "on_", "handle_", "listen_", "when_", "after_", "before_",
        "on_event_", "event_handler_", "process_event_",
    )
    _s360_dead_ev = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s360_ev_prefixes)
    ]
    if len(_s360_dead_ev) >= 2:
        _ev_names = ", ".join(s.name for s in _s360_dead_ev[:3])
        if len(_s360_dead_ev) > 3:
            _ev_names += f" +{len(_s360_dead_ev) - 3} more"
        lines.append(
            f"dead event handlers: {len(_s360_dead_ev)} unregistered handler(s) ({_ev_names})"
            f" — may mislead developers into thinking events are handled; deregister or remove"
        )


def _signals_dead_patterns_a(graph: Tempo, scored: list[tuple[Symbol, int]], dead: list[Symbol], lines: list[str]) -> None:
    """Dead code signals S241-S360: named-prefix patterns batch A — config, exceptions, types, CLI, events, async, factory, validators, middleware, serializers, adapters, auth, notifications, scheduled tasks, migration, parsers, property accessors."""
    # Pre-filter to fn/method candidates — avoids 22× redundant enum scans across all signals.
    _fn_candidates = [
        (sym, conf) for sym, conf in scored
        if conf >= 30
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
    ]
    _fn_candidates_40 = [(sym, conf) for sym, conf in _fn_candidates if conf >= 40]
    _patterns_a_cfg_types(scored, lines, _fn_candidates, _fn_candidates_40)
    _patterns_a_factories_adapters(scored, lines, _fn_candidates, _fn_candidates_40)
    _patterns_a_security_lifecycle(scored, lines, _fn_candidates, _fn_candidates_40)
    _patterns_a_parsers_accessors(scored, lines, _fn_candidates, _fn_candidates_40)


def _patterns_b_creation_ops(_fn_candidates: list, lines: list[str]) -> None:
    """S354/S396/S390/S384/S402: factory, logging, report, cleanup, background-task signals."""
    # S354: Dead factory functions — create_*/make_*/build_* functions with 0 callers.
    _s354_factory_prefixes = (
        "create_", "make_", "build_", "construct_", "new_", "factory_", "spawn_",
    )
    _s354_dead_factories = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s354_factory_prefixes)
    ]
    if len(_s354_dead_factories) >= 2:
        _fac_names354 = ", ".join(s.name for s in _s354_dead_factories[:3])
        if len(_s354_dead_factories) > 3:
            _fac_names354 += f" +{len(_s354_dead_factories) - 3} more"
        lines.append(
            f"dead factories: {len(_s354_dead_factories)} unused factory fn(s) ({_fac_names354})"
            f" — abandoned constructor alternatives; safe to remove after confirming no dynamic use"
        )

    # S396: Dead logging functions — log_*/debug_*/trace_* functions with 0 callers.
    _s396_log_prefixes = (
        "log_", "debug_", "trace_", "emit_log_", "write_log_",
        "log_event_", "record_event_",
    )
    _s396_dead_logs = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s396_log_prefixes)
    ]
    if len(_s396_dead_logs) >= 2:
        _log_names396 = ", ".join(s.name for s in _s396_dead_logs[:3])
        if len(_s396_dead_logs) > 3:
            _log_names396 += f" +{len(_s396_dead_logs) - 3} more"
        lines.append(
            f"dead logging: {len(_s396_dead_logs)} unused log fn(s) ({_log_names396})"
            f" — unintegrated observability wrappers; simplify by removing or wiring in"
        )

    # S390: Dead report generators — report_*/generate_*_report/export_* with 0 callers.
    _s390_report_prefixes = (
        "report_", "generate_report", "generate_", "export_", "build_report_",
        "create_report_", "render_report_",
    )
    _s390_dead_reports = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s390_report_prefixes)
    ]
    if len(_s390_dead_reports) >= 2:
        _rpt_names390 = ", ".join(s.name for s in _s390_dead_reports[:3])
        if len(_s390_dead_reports) > 3:
            _rpt_names390 += f" +{len(_s390_dead_reports) - 3} more"
        lines.append(
            f"dead report generators: {len(_s390_dead_reports)} unused report fn(s) ({_rpt_names390})"
            f" — unshipped reporting features; misleads about what the system can produce"
        )

    # S384: Dead cleanup functions — cleanup_*/teardown_*/destroy_* functions with 0 callers.
    _s384_cleanup_prefixes = (
        "cleanup_", "teardown_", "destroy_", "shutdown_", "close_",
        "dispose_", "finalize_", "free_", "release_",
    )
    _s384_dead_cleanup = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s384_cleanup_prefixes)
    ]
    if len(_s384_dead_cleanup) >= 2:
        _cl_names384 = ", ".join(s.name for s in _s384_dead_cleanup[:3])
        if len(_s384_dead_cleanup) > 3:
            _cl_names384 += f" +{len(_s384_dead_cleanup) - 3} more"
        lines.append(
            f"dead cleanup: {len(_s384_dead_cleanup)} unused lifecycle fn(s) ({_cl_names384})"
            f" — may be unregistered from lifecycle hooks; false confidence in cleanup behavior"
        )

    # S402: Dead background tasks — background_task_*/worker_*/celery_* functions with 0 callers.
    _s402_bg_prefixes = (
        "background_task_", "worker_", "celery_", "task_",
        "async_job_", "queue_", "job_",
    )
    _s402_dead_bg = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s402_bg_prefixes)
    ]
    if len(_s402_dead_bg) >= 2:
        _bg_names402 = ", ".join(s.name for s in _s402_dead_bg[:3])
        if len(_s402_dead_bg) > 3:
            _bg_names402 += f" +{len(_s402_dead_bg) - 3} more"
        lines.append(
            f"dead background tasks: {len(_s402_dead_bg)} unused task fn(s) ({_bg_names402})"
            f" — may be deregistered from task queue; dead worker slots confuse task routing"
        )


def _patterns_b_validation_tasks(_fn_candidates: list, lines: list[str]) -> None:
    """S408/S414/S420/S426/S432: validators, converters, schedulers, decorators, subscriptions."""
    # S408: Dead validators — validate_*/check_*/verify_* functions with 0 callers.
    _s408_val_prefixes = (
        "validate_", "check_", "verify_", "assert_", "ensure_",
        "is_valid_", "sanitize_",
    )
    _s408_dead_validators = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s408_val_prefixes)
    ]
    if len(_s408_dead_validators) >= 2:
        _val_names408 = ", ".join(s.name for s in _s408_dead_validators[:3])
        if len(_s408_dead_validators) > 3:
            _val_names408 += f" +{len(_s408_dead_validators) - 3} more"
        lines.append(
            f"dead validators: {len(_s408_dead_validators)} unused validation fn(s) ({_val_names408})"
            f" — may be bypassed rather than deleted; silent bypass weakens data integrity"
        )

    # S414: Dead converters — convert_*/transform_*/map_* functions with 0 callers.
    _s414_conv_prefixes = (
        "convert_", "transform_", "map_", "translate_",
        "serialize_", "format_", "encode_", "decode_",
    )
    _s414_dead_converters = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s414_conv_prefixes)
    ]
    if len(_s414_dead_converters) >= 2:
        _conv_names414 = ", ".join(s.name for s in _s414_dead_converters[:3])
        if len(_s414_dead_converters) > 3:
            _conv_names414 += f" +{len(_s414_dead_converters) - 3} more"
        lines.append(
            f"dead converters: {len(_s414_dead_converters)} unused converter fn(s) ({_conv_names414})"
            f" — abandoned pipeline stages; stale logic diverges from active converters"
        )

    # S420: Dead schedulers — schedule_*/cron_*/periodic_* functions with 0 callers.
    _s420_sched_prefixes = (
        "schedule_", "cron_", "periodic_", "run_every_",
        "hourly_", "daily_", "weekly_", "nightly_",
    )
    _s420_dead_schedulers = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s420_sched_prefixes)
    ]
    if len(_s420_dead_schedulers) >= 2:
        _sched_names420 = ", ".join(s.name for s in _s420_dead_schedulers[:3])
        if len(_s420_dead_schedulers) > 3:
            _sched_names420 += f" +{len(_s420_dead_schedulers) - 3} more"
        lines.append(
            f"dead schedulers: {len(_s420_dead_schedulers)} unused scheduler fn(s) ({_sched_names420})"
            f" — may be removed from job registry; phantom scheduled logic confuses ops"
        )

    # S426: Dead decorators — register_*/decorator_* functions with 0 callers.
    _s426_dec_prefixes = (
        "register_", "decorator_", "with_", "apply_decorator_",
        "patch_", "monkey_patch_", "decorate_",
    )
    _s426_dead_decorators = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s426_dec_prefixes)
    ]
    if len(_s426_dead_decorators) >= 2:
        _dec_names426 = ", ".join(s.name for s in _s426_dead_decorators[:3])
        if len(_s426_dead_decorators) > 3:
            _dec_names426 += f" +{len(_s426_dead_decorators) - 3} more"
        lines.append(
            f"dead decorators: {len(_s426_dead_decorators)} unused decorator fn(s) ({_dec_names426})"
            f" — removed decorator pattern may still apply dynamically; verify before deleting"
        )

    # S432: Dead event handlers (subscription style) — subscribe_*/listen_*/watch_* with 0 callers.
    _s432_sub_prefixes = (
        "subscribe_", "listen_", "watch_", "observe_",
        "on_event_", "attach_", "bind_",
    )
    _s432_dead_subs = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s432_sub_prefixes)
    ]
    if len(_s432_dead_subs) >= 2:
        _sub_names432 = ", ".join(s.name for s in _s432_dead_subs[:3])
        if len(_s432_dead_subs) > 3:
            _sub_names432 += f" +{len(_s432_dead_subs) - 3} more"
        lines.append(
            f"dead subscriptions: {len(_s432_dead_subs)} unwired subscription fn(s) ({_sub_names432})"
            f" — events fire without listener; silently drops signals"
        )


def _patterns_b_infra_patterns(_fn_candidates: list, lines: list[str]) -> None:
    """S438/S444/S450/S456/S462: migrations, CLI, error handlers, formatters, validators."""
    # S438: Dead migrations — migrate_*/migration_*/upgrade_*/downgrade_* helpers with 0 callers.
    _s438_migration_prefixes = (
        "migrate_", "migration_", "upgrade_", "downgrade_",
        "apply_migration_", "run_migration_",
    )
    _s438_dead_migrations = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s438_migration_prefixes)
    ]
    if len(_s438_dead_migrations) >= 1:
        _mig_names438 = ", ".join(s.name for s in _s438_dead_migrations[:3])
        if len(_s438_dead_migrations) > 3:
            _mig_names438 += f" +{len(_s438_dead_migrations) - 3} more"
        lines.append(
            f"dead migrations: {len(_s438_dead_migrations)} unapplied migration fn(s) ({_mig_names438})"
            f" — schema changes may never have been applied; verify before deleting"
        )

    # S444: Dead CLI commands — main_*/cli_*/cmd_* functions with 0 callers.
    _s444_cli_prefixes = ("main_", "cmd_", "cli_", "command_", "run_command_", "handle_command_")
    _s444_dead_cli = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s444_cli_prefixes)
    ]
    if len(_s444_dead_cli) >= 1:
        _cli_names444 = ", ".join(s.name for s in _s444_dead_cli[:3])
        if len(_s444_dead_cli) > 3:
            _cli_names444 += f" +{len(_s444_dead_cli) - 3} more"
        lines.append(
            f"dead CLI commands: {len(_s444_dead_cli)} unused command fn(s) ({_cli_names444})"
            f" — may be removed features; check docs and scripts before deleting"
        )

    # S450: Dead error handlers — handle_*/on_error_*/except_* functions with 0 callers.
    _s450_error_prefixes = (
        "handle_error_", "handle_exception_", "on_error_", "on_exception_",
        "except_", "catch_", "recover_",
    )
    _s450_dead_handlers = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s450_error_prefixes)
    ]
    if len(_s450_dead_handlers) >= 1:
        _handler_names450 = ", ".join(s.name for s in _s450_dead_handlers[:3])
        if len(_s450_dead_handlers) > 3:
            _handler_names450 += f" +{len(_s450_dead_handlers) - 3} more"
        lines.append(
            f"dead error handlers: {len(_s450_dead_handlers)} unregistered error fn(s) ({_handler_names450})"
            f" — error paths may be unhandled; verify before deleting"
        )

    # S456: Dead formatters — format_*/formatter_*/pretty_* functions with 0 callers.
    _s456_fmt_prefixes = (
        "format_", "formatter_", "pretty_", "pretty_print_",
        "render_output_", "display_",
    )
    _s456_dead_fmts = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s456_fmt_prefixes)
    ]
    if len(_s456_dead_fmts) >= 2:
        _fmt_names456 = ", ".join(s.name for s in _s456_dead_fmts[:3])
        if len(_s456_dead_fmts) > 3:
            _fmt_names456 += f" +{len(_s456_dead_fmts) - 3} more"
        lines.append(
            f"dead formatters: {len(_s456_dead_fmts)} unused display fn(s) ({_fmt_names456})"
            f" — output may be unformatted; verify display layer before deleting"
        )

    # S462: Dead validators — validate_*/check_*/verify_* functions with 0 callers.
    _s462_val_prefixes = (
        "validate_", "check_", "verify_", "assert_", "ensure_", "is_valid_",
    )
    _s462_dead_vals = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s462_val_prefixes)
    ]
    if len(_s462_dead_vals) >= 2:
        _val_names462 = ", ".join(s.name for s in _s462_dead_vals[:3])
        if len(_s462_dead_vals) > 3:
            _val_names462 += f" +{len(_s462_dead_vals) - 3} more"
        lines.append(
            f"dead validators: {len(_s462_dead_vals)} unused validation fn(s) ({_val_names462})"
            f" — validation may be bypassed; verify constraints still enforced before deleting"
        )


def _patterns_b_data_lifecycle(_fn_candidates: list, lines: list[str]) -> None:
    """S468/S474/S480: serializers, initializers, debug helpers."""
    # S468: Dead serializers — serialize_*/marshal_*/encode_* functions with 0 callers.
    _s468_serial_prefixes = (
        "serialize_", "marshal_", "encode_", "to_json_", "to_dict_",
        "export_", "dump_", "as_json_",
    )
    _s468_dead_serials = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s468_serial_prefixes)
    ]
    if len(_s468_dead_serials) >= 1:
        _serial_names468 = ", ".join(s.name for s in _s468_dead_serials[:3])
        if len(_s468_dead_serials) > 3:
            _serial_names468 += f" +{len(_s468_dead_serials) - 3} more"
        lines.append(
            f"dead serializers: {len(_s468_dead_serials)} unused serializer fn(s) ({_serial_names468})"
            f" — data export path may be missing; verify before deleting"
        )

    # S474: Dead initializers — setup_*/initialize_*/init_* functions with 0 callers.
    _s474_init_prefixes = (
        "setup_", "initialize_", "init_", "bootstrap_", "configure_", "startup_",
    )
    _s474_dead_inits = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s474_init_prefixes)
    ]
    if len(_s474_dead_inits) >= 1:
        _init_names474 = ", ".join(s.name for s in _s474_dead_inits[:3])
        if len(_s474_dead_inits) > 3:
            _init_names474 += f" +{len(_s474_dead_inits) - 3} more"
        lines.append(
            f"dead initializers: {len(_s474_dead_inits)} uncalled setup fn(s) ({_init_names474})"
            f" — component may be running without initialization; verify defaults before deleting"
        )

    # S480: Dead debug helpers — debug_*/log_debug_*/dump_* functions with 0 callers.
    _s480_debug_prefixes = (
        "debug_", "log_debug_", "dump_", "print_debug_", "trace_", "verbose_",
        "debug_print_", "debug_log_",
    )
    _s480_dead_debug = [
        sym for sym, conf in _fn_candidates
        if any(sym.name.lower().startswith(p) for p in _s480_debug_prefixes)
    ]
    if len(_s480_dead_debug) >= 2:
        _dbg_names480 = ", ".join(s.name for s in _s480_dead_debug[:3])
        if len(_s480_dead_debug) > 3:
            _dbg_names480 += f" +{len(_s480_dead_debug) - 3} more"
        lines.append(
            f"dead debug helpers: {len(_s480_dead_debug)} unused debug fn(s) ({_dbg_names480})"
            f" — safe to delete; verify the path they were debugging still works"
        )


def _patterns_b_cm_event_scans(graph: Tempo, _fn_candidates_40: list, lines: list[str], _s487_enter: list) -> None:
    """S487/S493: dead context managers and event handlers (graph symbol scans)."""
    # S487: Dead context managers — class defines __enter__/__exit__ but is never imported.
    # _s487_enter: pre-filtered list of __enter__ methods (non-test) from _signals_dead_patterns_b.
    _s487_cm_names: list[str] = []
    _s487_seen_files: set[str] = set()
    for _s487sym in _s487_enter:
        if _s487sym.file_path not in _s487_seen_files:
            _importers487 = graph.importers_of(_s487sym.file_path)
            if not graph.callers_of(_s487sym.id) and not _importers487:
                # O(1) parent lookup via parent_id (replaces O(N) graph.symbols.values() scan).
                _cls487 = graph.symbols.get(_s487sym.parent_id)
                if _cls487 and _cls487.kind.value == "class":
                    _s487_cm_names.append(_cls487.name)
                    _s487_seen_files.add(_s487sym.file_path)
    if _s487_cm_names:
        lines.append(
            f"dead context managers: {', '.join(_s487_cm_names[:3])} define __enter__/__exit__"
            f" but are never used with `with` — teardown logic is untested"
        )

    # S493: Dead event handlers — on_*/handle_*/listen_* functions with 0 callers.
    _s493_handler_prefixes = (
        "on_", "handle_", "listen_", "when_", "on_event_", "receive_", "dispatch_",
    )
    _s493_dead_handlers = [
        sym for sym, conf in _fn_candidates_40
        if any(sym.name.lower().startswith(p) for p in _s493_handler_prefixes)
    ]
    if len(_s493_dead_handlers) >= 2:
        _h_names493 = ", ".join(s.name for s in _s493_dead_handlers[:3])
        if len(_s493_dead_handlers) > 3:
            _h_names493 += f" +{len(_s493_dead_handlers) - 3} more"
        lines.append(
            f"dead event handlers: {len(_s493_dead_handlers)} unused handler fn(s) ({_h_names493})"
            f" — may indicate event wiring was accidentally lost; verify these are truly unreachable"
        )


def _patterns_b_class_property_scans(graph: Tempo, lines: list[str], _s499_fn: list, _s505_meth: list) -> None:
    """S499/S505/S512: dead class methods, property methods, and test utilities (graph symbol scans).

    Receives pre-filtered buckets from _signals_dead_patterns_b file-based precompute:
    _s499_fn: non-test functions whose parent is a class (for S499).
    _s505_meth: non-test methods, all non-dunder (for S505 — prefix filter applied here).
    S512 keeps its own graph.symbols.values() scan (test-file path, ~166 fn/meth in this repo).
    """
    # S499: Dead class methods — `@classmethod` or `@staticmethod` functions with 0 callers.
    # Parser assigns kind="function" to @classmethod/@staticmethod (not "method")
    # and requires a parent class to distinguish from top-level functions.
    _s499_dead_class_methods = [
        sym for sym in _s499_fn
        if not graph.callers_of(sym.id) and not graph.importers_of(sym.file_path)
    ]
    if len(_s499_dead_class_methods) >= 2:
        _cm_names499 = ", ".join(s.name for s in _s499_dead_class_methods[:3])
        if len(_s499_dead_class_methods) > 3:
            _cm_names499 += f" +{len(_s499_dead_class_methods) - 3} more"
        lines.append(
            f"dead class methods: {len(_s499_dead_class_methods)} unused @classmethod/@staticmethod"
            f" ({_cm_names499}) — may be abandoned utilities; verify intent before deleting"
        )

    # S505: Dead property methods — @property methods with 0 callers.
    _s505_prop_prefixes = ("get_", "is_", "has_", "can_", "should_", "needs_")
    _raw_callers505 = getattr(graph, "_callers", {})
    _s505_dead_props = [
        sym for sym in _s505_meth
        if any(sym.name.lower().startswith(p) for p in _s505_prop_prefixes)
        and not graph.callers_of(sym.id)
        and not _raw_callers505.get(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if len(_s505_dead_props) >= 2:
        _p_names505 = ", ".join(s.name for s in _s505_dead_props[:3])
        if len(_s505_dead_props) > 3:
            _p_names505 += f" +{len(_s505_dead_props) - 3} more"
        lines.append(
            f"dead property methods: {len(_s505_dead_props)} unused getter-style method(s) ({_p_names505})"
            f" — stale getters block attribute renames; remove before refactoring the data model"
        )

    # S512: Dead test utilities — setup_/teardown_/fixture_ functions with 0 callers in test files.
    _s512_test_util_prefixes = ("setup_", "teardown_", "fixture_", "mock_", "stub_", "fake_", "helper_test")
    _raw_callers512 = getattr(graph, "_callers", {})
    _s512_dead_test_utils = [
        sym for sym in graph.symbols.values()
        if _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.lower().startswith(p) for p in _s512_test_util_prefixes)
        and not graph.callers_of(sym.id)
        and not _raw_callers512.get(sym.id)
    ]
    if len(_s512_dead_test_utils) >= 2:
        _tu_names512 = ", ".join(s.name for s in _s512_dead_test_utils[:3])
        if len(_s512_dead_test_utils) > 3:
            _tu_names512 += f" +{len(_s512_dead_test_utils) - 3} more"
        lines.append(
            f"dead test utilities: {len(_s512_dead_test_utils)} unused test helper(s) ({_tu_names512})"
            f" — stale helpers mislead about test coverage; remove to clarify actual test scope"
        )


def _signals_dead_patterns_b(graph: Tempo, scored: list[tuple[Symbol, int]], dead: list[Symbol], lines: list[str]) -> None:
    """Dead code signals S354-S512: named-prefix patterns batch B — factory fns, logging, reports, cleanup, background tasks, validators, converters, schedulers, decorators, event handlers, migrations, CLI, error handlers, formatters, debug helpers."""
    # Pre-filter to fn/method candidates — avoids 19× redundant enum scans across all signals.
    # 1654 scored → 96 fn/method (conf>=30, non-test): 17.2× fewer iterations per signal.
    _fn_candidates = [
        (sym, conf) for sym, conf in scored
        if conf >= 30
        and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
    ]
    _fn_candidates_40 = [(sym, conf) for sym, conf in _fn_candidates if conf >= 40]

    # File-based precompute for S487/S499/S505: iterate only non-test files (1638 symbols vs 7142 total).
    # Replaces 3 separate graph.symbols.values() full-corpus scans with 1 file-indexed pass.
    # S512 keeps its own scan (test-file path, ~166 fn/meth; precomputing it costs more than the scan).
    _s499_dunder_skip = {"__init__", "__str__", "__repr__", "__enter__", "__exit__", "__new__"}
    _s487_enter: list = []   # non-test __enter__ methods (for S487)
    _s499_fn: list = []       # non-test functions with parent class (for S499)
    _s505_meth: list = []     # non-test methods (for S505 — prefix filter applied in helper)
    for fp in graph.files:
        if _is_test_file(fp):
            continue
        for _sym in graph.symbols_in_file(fp):
            _k = _sym.kind.value
            if _k == "method":
                if _sym.name == "__enter__":
                    _s487_enter.append(_sym)
                else:
                    _s505_meth.append(_sym)
            elif _k == "function" and _sym.name not in _s499_dunder_skip and _sym.parent_id:
                _parent = graph.symbols.get(_sym.parent_id)
                if _parent and _parent.kind.value == "class":
                    _s499_fn.append(_sym)

    _patterns_b_creation_ops(_fn_candidates, lines)
    _patterns_b_validation_tasks(_fn_candidates, lines)
    _patterns_b_infra_patterns(_fn_candidates, lines)
    _patterns_b_data_lifecycle(_fn_candidates, lines)
    _patterns_b_cm_event_scans(graph, _fn_candidates_40, lines, _s487_enter)
    _patterns_b_class_property_scans(graph, lines, _s499_fn, _s505_meth)


def _typed_a_precompute(graph: Tempo) -> tuple[list, list, list, list]:
    """S44-precompute: One classification pass over all non-test symbols.
    Before: 13 × symbol_count iterations. After: symbol_count (1 pass) + bucket subsets.
    Returns (_nt_cls, _nt_fn, _nt_meth, _nt_var)."""
    _nt_cls: list = []
    _nt_fn: list = []
    _nt_meth: list = []
    _nt_var: list = []
    for _s44 in graph.symbols.values():
        _kv44 = _s44.kind.value
        if not _is_test_file(_s44.file_path):
            if _kv44 == "class":
                _nt_cls.append(_s44)
            elif _kv44 == "function":
                _nt_fn.append(_s44)
            elif _kv44 == "method":
                _nt_meth.append(_s44)
            elif _kv44 == "variable":
                _nt_var.append(_s44)
    return _nt_cls, _nt_fn, _nt_meth, _nt_var


def _typed_a_abstract_types(graph: Tempo, _nt_cls: list, _nt_fn: list, _nt_meth: list, _nt_var: list, dead_typing_files: list, lines: list[str]) -> None:
    """S536-S569: abstract base, dataclass, module constants, exceptions, magic methods, value objects, CLI handlers, factories, validators, typing files."""

    # S536: Dead abstract base class — Abstract*/Protocol class with no subclasses and no callers.
    # An abstract class that was never implemented is pure dead weight; it cannot be instantiated
    # and its only value was as a type contract — which is now unrealized.
    _s536_dead_abc = [
        sym for sym in _nt_cls
        if (
            sym.name.startswith("Abstract")
            or sym.name.startswith("Base")
            or sym.name.endswith("ABC")
            or sym.name.endswith("Protocol")
            or sym.name.endswith("Interface")
        )
        and not graph.subtypes_of(sym.name)
        and not graph.callers_of(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if _s536_dead_abc:
        _abc_names536 = ", ".join(s.name for s in _s536_dead_abc[:3])
        if len(_s536_dead_abc) > 3:
            _abc_names536 += f" +{len(_s536_dead_abc) - 3} more"
        lines.append(
            f"dead abstract classes: {len(_s536_dead_abc)} unimplemented abstract/base class(es) ({_abc_names536})"
            f" — never implemented; the type contract was never realized; safe to remove"
        )

    # S543: Dead dataclass — dataclass class with 0 callers in files that use dataclasses.
    # Unused dataclasses are data models that were designed but never wired up;
    # they silently expand the schema surface and mislead future code about active models.
    _s542_dc_files = {
        fp for fp, fi in graph.files.items()
        if any("dataclass" in imp for imp in fi.imports)
    }
    _s542_dead_dc = [
        sym for sym in _nt_cls
        if sym.file_path in _s542_dc_files
        and not graph.callers_of(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if _s542_dead_dc:
        _dc_names542 = ", ".join(s.name for s in _s542_dead_dc[:3])
        if len(_s542_dead_dc) > 3:
            _dc_names542 += f" +{len(_s542_dead_dc) - 3} more"
        lines.append(
            f"dead dataclasses: {len(_s542_dead_dc)} unused dataclass(es) ({_dc_names542})"
            f" — designed but never instantiated; verify intent and remove stale models"
        )

    # S530: Dead module constants — SCREAMING_SNAKE_CASE names at module level with 0 callers.
    # Unused module-level constants accumulate from feature flags, thresholds, and magic values
    # that were never cleaned up; they mislead about the codebase's active configuration surface.
    _s530_dead_constants = [
        sym for sym in _nt_var
        if sym.name == sym.name.upper()
        and "_" in sym.name
        and len(sym.name) >= 3
        and not sym.parent_id
        and not graph.callers_of(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if len(_s530_dead_constants) >= 3:
        _const_names530 = ", ".join(s.name for s in _s530_dead_constants[:3])
        if len(_s530_dead_constants) > 3:
            _const_names530 += f" +{len(_s530_dead_constants) - 3} more"
        lines.append(
            f"dead constants: {len(_s530_dead_constants)} unused SCREAMING_SNAKE constant(s) ({_const_names530})"
            f" — stale config values mislead about active thresholds; audit before deleting"
        )

    # S524: Dead exception classes — custom exception classes with 0 callers in non-imported files.
    # Unused exception classes bloat the error hierarchy and mislead about what errors a module raises;
    # they often result from copy-pasted exception hierarchies that were never wired up.
    _s524_exc_suffixes = ("error", "exception", "fault", "failure", "warning")
    _s524_dead_exc = [
        sym for sym in _nt_cls
        if any(sym.name.lower().endswith(s) for s in _s524_exc_suffixes)
        and not graph.callers_of(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if _s524_dead_exc:
        _exc_names524 = ", ".join(s.name for s in _s524_dead_exc[:3])
        if len(_s524_dead_exc) > 3:
            _exc_names524 += f" +{len(_s524_dead_exc) - 3} more"
        lines.append(
            f"dead exception classes: {len(_s524_dead_exc)} unused exception class(es) ({_exc_names524})"
            f" — bloats error hierarchy and misleads callers; verify intent before deleting"
        )

    # S518: Dead magic methods — dunder display/comparison methods with 0 callers in non-imported files.
    # __str__/__repr__/__len__ in files with no importers indicate abandoned model classes;
    # magic methods are rarely flagged as dead code but the whole class is likely removable.
    _s518_dunder_targets = frozenset(("__str__", "__repr__", "__len__", "__iter__", "__contains__", "__format__"))
    _s518_dead_dunders = [
        sym for sym in _nt_meth
        if sym.name in _s518_dunder_targets
        and not graph.callers_of(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if len(_s518_dead_dunders) >= 2:
        _du_names518 = ", ".join(s.name for s in _s518_dead_dunders[:3])
        if len(_s518_dead_dunders) > 3:
            _du_names518 += f" +{len(_s518_dead_dunders) - 3} more"
        lines.append(
            f"dead magic methods: {len(_s518_dead_dunders)} unused dunder method(s) ({_du_names518})"
            f" — orphaned display/comparison methods in dead files; whole class is likely removable"
        )

    # S545: Dead value object — data/schema/payload class with 0 callers in non-imported files.
    # Value objects (DTOs, schemas, payloads) defined but never instantiated represent abandoned
    # API contracts; they are especially easy to miss because they have no logic to trigger errors.
    _s542_vo_suffixes = ("data", "schema", "payload", "dto", "record", "config", "settings", "response", "request")
    _s542_dead_vos = [
        sym for sym in _nt_cls
        if any(sym.name.lower().endswith(s) for s in _s542_vo_suffixes)
        and not graph.callers_of(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if _s542_dead_vos:
        _vo_names542 = ", ".join(s.name for s in _s542_dead_vos[:3])
        if len(_s542_dead_vos) > 3:
            _vo_names542 += f" +{len(_s542_dead_vos) - 3} more"
        lines.append(
            f"dead value objects: {len(_s542_dead_vos)} unused data/schema class(es) ({_vo_names542})"
            f" — abandoned API contracts; verify no serialization hooks before deleting"
        )

    # S551: Dead CLI handler — unused functions with cmd_/do_/handle_/on_ prefixes in non-imported files.
    # CLI and event handler functions that are never called represent abandoned command surface;
    # they clutter help output, inflate entry-point lists, and mislead tool discovery.
    _cli_prefixes551 = ("cmd_", "do_", "handle_", "on_", "command_")
    _dead_cli551 = [
        sym for sym in _nt_fn
        if any(sym.name.startswith(p) for p in _cli_prefixes551)
        and not graph.callers_of(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if len(_dead_cli551) >= 2:
        _cli_names551 = ", ".join(s.name for s in _dead_cli551[:3])
        if len(_dead_cli551) > 3:
            _cli_names551 += f" +{len(_dead_cli551) - 3} more"
        lines.append(
            f"dead handlers: {len(_dead_cli551)} unused command/event handler(s) ({_cli_names551})"
            f" — abandoned command surface; audit intent and remove or wire up before next release"
        )

    # S557: Dead factory — unused factory/builder functions (create_*/make_*/build_*) in non-imported files.
    # Factory functions are creation contracts; if they have no callers and their file has no importers,
    # the construction pattern was abandoned mid-implementation.
    _factory_prefixes557 = ("create_", "make_", "build_", "new_", "from_")
    _dead_factories557 = [
        sym for sym in _nt_fn
        if any(sym.name.startswith(p) for p in _factory_prefixes557)
        and not graph.callers_of(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if len(_dead_factories557) >= 2:
        _fac_names557 = ", ".join(s.name for s in _dead_factories557[:3])
        if len(_dead_factories557) > 3:
            _fac_names557 += f" +{len(_dead_factories557) - 3} more"
        lines.append(
            f"dead factories: {len(_dead_factories557)} unused factory/builder function(s) ({_fac_names557})"
            f" — abandoned construction patterns; verify intent and remove or wire up"
        )

    # S563: Dead validator — unused functions with validate_/check_/verify_/assert_ prefix in non-imported files.
    # Validation functions that are never called represent abandoned input guards;
    # their absence means callers silently skip validation, creating hidden injection points.
    _validator_prefixes563 = ("validate_", "check_", "verify_", "assert_", "ensure_")
    _dead_validators563 = [
        sym for sym in _nt_fn
        if any(sym.name.startswith(p) for p in _validator_prefixes563)
        and not graph.callers_of(sym.id)
        and not graph.importers_of(sym.file_path)
    ]
    if len(_dead_validators563) >= 2:
        _val_names563 = ", ".join(s.name for s in _dead_validators563[:3])
        if len(_dead_validators563) > 3:
            _val_names563 += f" +{len(_dead_validators563) - 3} more"
        lines.append(
            f"dead validators: {len(_dead_validators563)} unused validation function(s) ({_val_names563})"
            f" — abandoned input guards; callers silently skip these checks; wire up or remove"
        )

    # S569: Dead typing file (computed near top, before early return)
    if dead_typing_files:
        _tf_names569 = ", ".join(fp.rsplit("/", 1)[-1] for fp in dead_typing_files[:3])
        if len(dead_typing_files) > 3:
            _tf_names569 += f" +{len(dead_typing_files) - 3} more"
        lines.append(
            f"dead type aliases: {len(dead_typing_files)} typing-only file(s) with no importers ({_tf_names569})"
            f" — stale type definitions from refactored APIs; safe to remove after confirming no runtime use"
        )



def _typed_a_class_service(graph: Tempo, _nt_cls: list, dead: list, dead_util_fns: list, lines: list[str]) -> None:
    """S575-S629: context managers, service classes, exception classes, dead modules, large classes, async, constants, callbacks."""
    # S575: Dead context manager — unused class with __enter__ and __exit__ dunder methods.
    # Context managers defined but never instantiated represent abandoned resource management;
    # their with-block protocol is never invoked and resource cleanup never happens.
    _cm_dunder575 = frozenset(("__enter__", "__exit__"))
    _dead_cm575: list = []
    for _sym575 in _nt_cls:
        if (
            not graph.callers_of(_sym575.id)
            and not graph.importers_of(_sym575.file_path)
        ):
            _children575 = {c.name for c in graph.children_of(_sym575.id)}
            if _cm_dunder575.issubset(_children575):
                _dead_cm575.append(_sym575)
    if _dead_cm575:
        _cm_names575 = ", ".join(s.name for s in _dead_cm575[:3])
        if len(_dead_cm575) > 3:
            _cm_names575 += f" +{len(_dead_cm575) - 3} more"
        lines.append(
            f"dead context managers: {len(_dead_cm575)} unused context manager class(es) ({_cm_names575})"
            f" — __enter__/__exit__ never invoked; resource cleanup never happens; remove or wire up"
        )

    # S586: Dead service class — unused class whose name ends with a service-layer suffix
    # (Manager, Service, Controller, Registry, Repository, Handler, Provider, Dispatcher).
    # These heavyweight wiring-layer classes are commonly created but never integrated.
    _svc_suffixes586 = ("Manager", "Service", "Controller", "Registry",
                        "Repository", "Handler", "Provider", "Dispatcher")
    _dead_svc586: list = []
    for _sym586 in _nt_cls:
        if (
            _sym586.name.endswith(_svc_suffixes586)
            and not graph.callers_of(_sym586.id)
            and not graph.importers_of(_sym586.file_path)
        ):
            _dead_svc586.append(_sym586)
    if _dead_svc586:
        _svc_names586 = ", ".join(s.name for s in _dead_svc586[:3])
        if len(_dead_svc586) > 3:
            _svc_names586 += f" +{len(_dead_svc586) - 3} more"
        lines.append(
            f"dead service classes: {len(_dead_svc586)} unused service-layer class(es) ({_svc_names586})"
            f" — never instantiated or imported; wiring was never completed; remove or integrate"
        )

    # S592: Dead exception class — unused class whose name ends with Error, Exception, or Warning.
    # Custom exceptions that are never raised or caught represent abandoned error-handling design;
    # they add noise to exception hierarchies and mislead readers about error contracts.
    _exc_suffixes592 = ("Error", "Exception", "Warning", "Fault", "Failure")
    _dead_exc592: list = []
    for _sym592 in _nt_cls:
        if (
            _sym592.name.endswith(_exc_suffixes592)
            and not graph.callers_of(_sym592.id)
            and not graph.importers_of(_sym592.file_path)
        ):
            _dead_exc592.append(_sym592)
    if _dead_exc592:
        _exc_names592 = ", ".join(s.name for s in _dead_exc592[:3])
        if len(_dead_exc592) > 3:
            _exc_names592 += f" +{len(_dead_exc592) - 3} more"
        lines.append(
            f"dead exception classes: {len(_dead_exc592)} unused exception class(es) ({_exc_names592})"
            f" — never raised or caught; remove or integrate into error-handling contract"
        )

    # S598: Dead module — source file with symbols but zero importers and zero callers.
    # An entire file that is never imported and none of its symbols are called anywhere
    # is a strong signal that the module is abandoned and safe to remove.
    _s598_dead_modules = [
        fp for fp, fi in graph.files.items()
        if not _is_test_file(fp)
        and list(fi.symbols)
        and not graph.importers_of(fp)
        and not any(graph.callers_of(sym_id) for sym_id in fi.symbols)
    ]
    if _s598_dead_modules:
        _mod_names598 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _s598_dead_modules[:3])
        if len(_s598_dead_modules) > 3:
            _mod_names598 += f" +{len(_s598_dead_modules) - 3} more"
        lines.append(
            f"dead modules: {len(_s598_dead_modules)} entire file(s) unreachable ({_mod_names598})"
            f" — no importers and no cross-file callers; likely abandoned; safe to remove"
        )

    # S605: Dead utility function (pre-computed near top to allow firing when dead[] is empty)
    if dead_util_fns:
        _util_names605 = ", ".join(s.name for s in dead_util_fns[:3])
        if len(dead_util_fns) > 3:
            _util_names605 += f" +{len(dead_util_fns) - 3} more"
        lines.append(
            f"dead utility functions: {len(dead_util_fns)} unused factory/utility function(s) ({_util_names605})"
            f" — never called; the use case they were written for never materialized; remove or promote"
        )

    # S611: Dead large class — unused class spanning 30+ lines.
    # A large class that is never instantiated or imported represents significant invested effort
    # that never paid off; it likely contains stale logic that diverged from the live codebase.
    _dead_large611 = [
        s for s in dead
        if s.kind.value == "class"
        and s.line_count >= 30
        and not _is_test_file(s.file_path)
    ]
    if _dead_large611:
        _large_names611 = ", ".join(
            f"{s.name} ({s.line_count}L)" for s in _dead_large611[:3]
        )
        if len(_dead_large611) > 3:
            _large_names611 += f" +{len(_dead_large611) - 3} more"
        lines.append(
            f"dead large class: {len(_dead_large611)} unused class(es) over 30 lines ({_large_names611})"
            f" — significant effort invested but never used; stale logic likely; safe to remove"
        )

    # S617: Dead async function — unused function whose signature contains "async def".
    # An async function that is never awaited represents an abandoned coroutine design;
    # it may be a partially-implemented feature or leftover from a refactored async layer.
    _dead_async617 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and (s.signature or "").startswith("async ")
    ]
    if _dead_async617:
        _async_names617 = ", ".join(s.name for s in _dead_async617[:3])
        if len(_dead_async617) > 3:
            _async_names617 += f" +{len(_dead_async617) - 3} more"
        lines.append(
            f"dead async functions: {len(_dead_async617)} unused async function(s) ({_async_names617})"
            f" — never awaited; likely abandoned coroutine design; remove or wire into async context"
        )

    # S623: Dead constant — exported module-level variable with SCREAMING_SNAKE_CASE name, no callers.
    # Unused constants accumulate as the codebase evolves; they represent configuration
    # or magic values that were once used but never cleaned up.
    _dead_consts623 = [
        s for s in dead
        if s.kind.value in ("constant", "variable")
        and not _is_test_file(s.file_path)
        and s.name == s.name.upper()
        and len(s.name) >= 3
        and "_" in s.name or s.name.isupper()
    ]
    if _dead_consts623:
        _const_names623 = ", ".join(s.name for s in _dead_consts623[:3])
        if len(_dead_consts623) > 3:
            _const_names623 += f" +{len(_dead_consts623) - 3} more"
        lines.append(
            f"dead constants: {len(_dead_consts623)} unused SCREAMING_SNAKE_CASE variable(s) ({_const_names623})"
            f" — orphaned configuration; remove or re-wire to avoid misleading future readers"
        )

    # S629: Dead callback — unused function/method with callback/handler/listener/hook suffix.
    # These naming patterns signal event-driven intent; an unregistered callback is a
    # dead wire — the event contract was designed but the wiring was never completed.
    _cb_suffixes629 = ("_callback", "_handler", "_listener", "_hook", "_receiver", "_observer")
    _dead_cb629 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and any(s.name.lower().endswith(sfx) for sfx in _cb_suffixes629)
    ]
    if _dead_cb629:
        _cb_names629 = ", ".join(s.name for s in _dead_cb629[:3])
        if len(_dead_cb629) > 3:
            _cb_names629 += f" +{len(_dead_cb629) - 3} more"
        lines.append(
            f"dead callbacks: {len(_dead_cb629)} unused callback/handler function(s) ({_cb_names629})"
            f" — dead wire; event contract was designed but never wired; remove or register"
        )



def _typed_a_symbol_patterns(graph: Tempo, dead: list, lines: list[str]) -> None:
    """S635-S689: deprecated symbols, inner/mixin/proto/empty classes, annotated fns, dead modules, overloaded names, long fns, derived classes."""
    # S635: Dead deprecated symbol — unused exported symbol with "deprecated" or "obsolete" in its doc.
    # Symbols explicitly marked deprecated but still exported are technical debt traps;
    # callers may still depend on them even though maintainers intend removal.
    _depr_keywords635 = ("deprecated", "obsolete", "do not use", "do_not_use", "legacy")
    _dead_depr635 = [
        s for s in dead
        if not _is_test_file(s.file_path)
        and any(kw in (s.doc or "").lower() for kw in _depr_keywords635)
    ]
    if _dead_depr635:
        _depr_names635 = ", ".join(s.name for s in _dead_depr635[:3])
        if len(_dead_depr635) > 3:
            _depr_names635 += f" +{len(_dead_depr635) - 3} more"
        lines.append(
            f"dead deprecated: {len(_dead_depr635)} deprecated-marked symbol(s) are unused ({_depr_names635})"
            f" — safe removal targets; deprecated intent + no callers = clean delete"
        )

    # S641: Dead inner class — unused nested class (parent_id is not None, kind == class).
    # Inner classes that are never instantiated may be leftover design artifacts;
    # they carry cognitive overhead without providing value.
    _dead_inner641 = [
        s for s in dead
        if s.kind.value == "class"
        and s.parent_id is not None
        and not _is_test_file(s.file_path)
    ]
    if _dead_inner641:
        _inner_names641 = ", ".join(s.name for s in _dead_inner641[:3])
        if len(_dead_inner641) > 3:
            _inner_names641 += f" +{len(_dead_inner641) - 3} more"
        lines.append(
            f"dead inner classes: {len(_dead_inner641)} unused nested class(es) ({_inner_names641})"
            f" — leftover nested design; remove to reduce cognitive overhead"
        )

    # S647: Dead mixin/base class — unused class with "Mixin", "Base", or "Abstract" in its name.
    # Mixins and base classes designed for inheritance but never subclassed are
    # orphaned extension points; they represent planned reuse that never materialized.
    _mixin_markers647 = ("mixin", "base", "abstract", "abcmixin", "interfacemixin")
    _dead_mixins647 = [
        s for s in dead
        if s.kind.value == "class"
        and not _is_test_file(s.file_path)
        and any(m in s.name.lower() for m in _mixin_markers647)
    ]
    if _dead_mixins647:
        _mixin_names647 = ", ".join(s.name for s in _dead_mixins647[:3])
        if len(_dead_mixins647) > 3:
            _mixin_names647 += f" +{len(_dead_mixins647) - 3} more"
        lines.append(
            f"dead mixins: {len(_dead_mixins647)} unused Mixin/Base class(es) ({_mixin_names647})"
            f" — planned reuse that never materialized; remove unless extension is imminent"
        )

    # S653: Dead protocol/interface — unused class with "Protocol" or "Interface" in its name.
    # Protocol and Interface classes define behavioral contracts; if unused, the contract
    # was designed but no implementation adopted it — likely abandoned architecture.
    _dead_proto653 = [
        s for s in dead
        if s.kind.value == "class"
        and not _is_test_file(s.file_path)
        and any(m in s.name for m in ("Protocol", "Interface", "ABC", "Abstract"))
        and not any(m in s.name for m in ("Mixin", "Base"))  # covered by S647
    ]
    if _dead_proto653:
        _proto_names653 = ", ".join(s.name for s in _dead_proto653[:3])
        if len(_dead_proto653) > 3:
            _proto_names653 += f" +{len(_dead_proto653) - 3} more"
        lines.append(
            f"dead protocols: {len(_dead_proto653)} unused Protocol/Interface class(es) ({_proto_names653})"
            f" — unimplemented contract; remove or provide at least one concrete implementation"
        )

    # S659: Dead empty class — unused class with no method children (shell class).
    # A class with no methods may be a placeholder, a stub that was never fleshed out,
    # or a configuration class that became dead when the feature was abandoned.
    _dead_empty659 = [
        s for s in dead
        if s.kind.value == "class"
        and not _is_test_file(s.file_path)
        and not graph.children_of(s.id)  # no children at all
    ]
    if _dead_empty659:
        _empty_names659 = ", ".join(s.name for s in _dead_empty659[:3])
        if len(_dead_empty659) > 3:
            _empty_names659 += f" +{len(_dead_empty659) - 3} more"
        lines.append(
            f"dead empty classes: {len(_dead_empty659)} unused class(es) with no children ({_empty_names659})"
            f" — placeholder or abandoned stub; safe to delete"
        )

    # S665: Dead annotated function — dead function with a `->` return type annotation.
    # Functions with explicit return annotations were designed as intentional API;
    # an annotated-but-unused function is a more deliberate artifact than an unannotated one.
    _dead_annotated665 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and "->" in (s.signature or "")
    ]
    if _dead_annotated665:
        _ann_names665 = ", ".join(s.name for s in _dead_annotated665[:3])
        if len(_dead_annotated665) > 3:
            _ann_names665 += f" +{len(_dead_annotated665) - 3} more"
        lines.append(
            f"dead annotated functions: {len(_dead_annotated665)} unused typed function(s) ({_ann_names665})"
            f" — annotated APIs that were never called; intentional design that was abandoned"
        )

    # S671: Dead module — all exported symbols in a non-test file are dead (entire file unused).
    # When every exported symbol in a file is unused, the whole module is a candidate for removal;
    # this is stronger evidence than individual dead symbols scattered across files.
    _dead_files671: dict[str, list[Symbol]] = {}
    for _s671 in dead:
        if not _is_test_file(_s671.file_path) and _s671.parent_id is None:
            _dead_files671.setdefault(_s671.file_path, []).append(_s671)
    _full_dead671 = []
    for _fp671, _dsyms671 in _dead_files671.items():
        _fi671 = graph.files.get(_fp671)
        if _fi671:
            _all_top671 = [
                s for s in graph.symbols_in_file(_fp671)
                if s.parent_id is None
            ]
            if _all_top671 and len(_dsyms671) == len(_all_top671):
                _full_dead671.append(_fp671)
    if _full_dead671:
        _mod_names671 = ", ".join(fp.rsplit("/", 1)[-1] for fp in _full_dead671[:3])
        if len(_full_dead671) > 3:
            _mod_names671 += f" +{len(_full_dead671) - 3} more"
        lines.append(
            f"dead module(s): {len(_full_dead671)} file(s) fully unused ({_mod_names671})"
            f" — entire file has no live consumers; candidate for deletion"
        )

    # S677: Dead overloaded name — 3+ dead symbols share the same name (copy-paste drift).
    # Multiple dead functions with the same name across files indicate widespread duplication
    # that was never activated; none of the copies survived to production use.
    _dead_name_counts677: dict[str, int] = {}
    for _s677 in dead:
        if _s677.kind.value in ("function", "method") and not _is_test_file(_s677.file_path):
            _dead_name_counts677[_s677.name] = _dead_name_counts677.get(_s677.name, 0) + 1
    _overloaded677 = [(name, cnt) for name, cnt in _dead_name_counts677.items() if cnt >= 3]
    if _overloaded677:
        _top677 = sorted(_overloaded677, key=lambda x: -x[1])[:2]
        _label677 = ", ".join(f"'{n}' ×{c}" for n, c in _top677)
        lines.append(
            f"dead overloaded names: {len(_overloaded677)} name(s) dead in 3+ files ({_label677})"
            f" — copy-paste drift; duplicated functions were never called anywhere"
        )

    # S683: Dead long functions — unused functions with 10+ lines (significant speculative work).
    # A large dead function represents substantial development effort that was never activated;
    # the more lines, the more deliberate the original intent — and the higher the removal cost.
    _dead_long683 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and s.line_count >= 10
    ]
    if _dead_long683:
        _long_names683 = ", ".join(s.name for s in _dead_long683[:3])
        if len(_dead_long683) > 3:
            _long_names683 += f" +{len(_dead_long683) - 3} more"
        lines.append(
            f"dead long functions: {len(_dead_long683)} unused function(s) with 10+ lines ({_long_names683})"
            f" — substantial speculative work; review intent before deleting"
        )

    # S689: Dead derived class — dead class that inherits from another class (has a base class).
    # A dead class that was designed to extend a hierarchy represents architectural planning
    # that was never activated; it may indicate an abandoned feature or incomplete refactor.
    _dead_derived689 = [
        s for s in dead
        if s.kind.value == "class"
        and not _is_test_file(s.file_path)
        and s.signature
        and "(" in s.signature
        and ")" in s.signature
        and s.signature.split("(", 1)[1].split(")", 1)[0].strip() not in ("", "object")
    ]
    if _dead_derived689:
        _der_names689 = ", ".join(s.name for s in _dead_derived689[:3])
        if len(_dead_derived689) > 3:
            _der_names689 += f" +{len(_dead_derived689) - 3} more"
        lines.append(
            f"dead derived classes: {len(_dead_derived689)} unused subclass(es) ({_der_names689})"
            f" — abandoned subclass design; verify base class is still the right abstraction"
        )



def _typed_a_fn_specialized(dead: list, lines: list[str]) -> None:
    """S695-S737: test utilities, factory fns, event handlers, serialization, config loaders, async, migration, protocol/interface."""
    # S695: Dead test utility — unused functions in source files with test-utility names.
    # Functions named mock_*, stub_*, fake_*, or *_fixture in non-test files are test helpers
    # that leaked into production modules; they should either be moved or removed.
    _test_util_kws695 = ("mock", "stub", "fake", "fixture")
    _dead_test_util695 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and any(kw in s.name.lower() for kw in _test_util_kws695)
    ]
    if _dead_test_util695:
        _tu_names695 = ", ".join(s.name for s in _dead_test_util695[:3])
        if len(_dead_test_util695) > 3:
            _tu_names695 += f" +{len(_dead_test_util695) - 3} more"
        lines.append(
            f"dead test utilities: {len(_dead_test_util695)} test-utility name(s) in source files ({_tu_names695})"
            f" — test helpers in production code; move to test files or remove"
        )

    # S701: Dead factory functions — unused functions whose names start with create/make/build/factory.
    # Factory functions represent construction logic for features that were never integrated;
    # the naming pattern signals intentional design that was abandoned before wiring.
    _factory_prefixes701 = ("create", "make", "build", "factory")
    _dead_factories701 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and any(s.name.lower().startswith(pfx) for pfx in _factory_prefixes701)
    ]
    if _dead_factories701:
        _fac_names701 = ", ".join(s.name for s in _dead_factories701[:3])
        if len(_dead_factories701) > 3:
            _fac_names701 += f" +{len(_dead_factories701) - 3} more"
        lines.append(
            f"dead factory functions: {len(_dead_factories701)} unused factory function(s) ({_fac_names701})"
            f" — abandoned construction logic; feature was never wired up"
        )

    # S707: Dead event handlers — unused functions with event-handler naming patterns.
    # Functions named on_*, handle_*, or *_handler are written to respond to events;
    # if they're dead, the event they were designed for was removed or never wired up.
    _handler_patterns707 = ("on_", "handle_")
    _handler_suffix707 = ("_handler", "_listener", "_callback")
    _dead_handlers707 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and (
            any(s.name.lower().startswith(pfx) for pfx in _handler_patterns707)
            or any(s.name.lower().endswith(sfx) for sfx in _handler_suffix707)
        )
    ]
    if _dead_handlers707:
        _hdl_names707 = ", ".join(s.name for s in _dead_handlers707[:3])
        if len(_dead_handlers707) > 3:
            _hdl_names707 += f" +{len(_dead_handlers707) - 3} more"
        lines.append(
            f"dead event handlers: {len(_dead_handlers707)} unused handler function(s) ({_hdl_names707})"
            f" — event was removed or never wired; remove or reconnect to event source"
        )

    # S713: Dead serialization functions — unused functions for data format conversion.
    # Serialization functions (serialize/deserialize/encode/decode/marshal/unmarshal) are
    # written for specific data formats; dead ones signal format changes or abandoned integrations.
    _ser_kws713 = ("serialize", "deserialize", "encode", "decode", "marshal", "unmarshal")
    _dead_ser713 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and any(kw in s.name.lower() for kw in _ser_kws713)
    ]
    if _dead_ser713:
        _ser_names713 = ", ".join(s.name for s in _dead_ser713[:3])
        if len(_dead_ser713) > 3:
            _ser_names713 += f" +{len(_dead_ser713) - 3} more"
        lines.append(
            f"dead serialization functions: {len(_dead_ser713)} unused format function(s) ({_ser_names713})"
            f" — data format changed or integration was abandoned"
        )

    # S719: Dead config loaders — unused functions with config-loading name patterns.
    # Config loaders (load_config, parse_settings, read_conf) that are never called indicate
    # abandoned configuration strategies or superseded loading mechanisms.
    _loader_pfx719 = ("load_", "parse_", "read_")
    _cfg_kws719 = ("config", "setting", "conf", "cfg")
    _dead_loaders719 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and any(s.name.lower().startswith(pfx) for pfx in _loader_pfx719)
        and any(kw in s.name.lower() for kw in _cfg_kws719)
    ]
    if _dead_loaders719:
        _ldr_names719 = ", ".join(s.name for s in _dead_loaders719[:3])
        if len(_dead_loaders719) > 3:
            _ldr_names719 += f" +{len(_dead_loaders719) - 3} more"
        lines.append(
            f"dead config loaders: {len(_dead_loaders719)} unused config-loading function(s) ({_ldr_names719})"
            f" — abandoned config strategy or superseded loading mechanism"
        )

    # S725: Dead async functions — unused functions with an async signature.
    # Async functions indicate concurrent or I/O workflows; dead ones signal abandoned parallel
    # execution strategies, superseded event loops, or incomplete async migrations.
    _dead_async725 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and s.signature is not None
        and s.signature.lstrip().startswith("async")
    ]
    if _dead_async725:
        _async_names725 = ", ".join(s.name for s in _dead_async725[:3])
        if len(_dead_async725) > 3:
            _async_names725 += f" +{len(_dead_async725) - 3} more"
        lines.append(
            f"dead async functions: {len(_dead_async725)} unused async function(s) ({_async_names725})"
            f" — abandoned async workflow or incomplete async migration"
        )

    # S731: Dead migration functions — unused functions with migration-related names.
    # Migration functions (migrate, upgrade, downgrade) that are never called indicate
    # abandoned schema migrations, version upgrade paths, or skipped data transformations.
    _mig_kws731 = ("migrate", "upgrade", "downgrade", "rollback", "rollforward")
    _dead_migs731 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and not _is_test_file(s.file_path)
        and any(kw in s.name.lower() for kw in _mig_kws731)
    ]
    if _dead_migs731:
        _mig_names731 = ", ".join(s.name for s in _dead_migs731[:3])
        if len(_dead_migs731) > 3:
            _mig_names731 += f" +{len(_dead_migs731) - 3} more"
        lines.append(
            f"dead migration functions: {len(_dead_migs731)} unused migration function(s) ({_mig_names731})"
            f" — abandoned migration or skipped upgrade path"
        )

    # S737: Dead protocol/interface classes — unused abstract base classes or protocol definitions.
    # ABC and Protocol classes define contracts for implementers; dead ones signal that the
    # abstraction was designed but no concrete implementations are in active use.
    _dead_protos737 = [
        s for s in dead
        if s.kind.value == "class"
        and not _is_test_file(s.file_path)
        and (
            any(kw in s.name for kw in ("ABC", "Protocol", "Abstract", "Interface"))
            or (
                s.signature is not None
                and any(
                    f"({kw})" in s.signature or f"({kw}," in s.signature
                    for kw in ("ABC", "Protocol")
                )
            )
        )
    ]
    if _dead_protos737:
        _proto_names737 = ", ".join(s.name for s in _dead_protos737[:3])
        if len(_dead_protos737) > 3:
            _proto_names737 += f" +{len(_dead_protos737) - 3} more"
        lines.append(
            f"dead protocols: {len(_dead_protos737)} unused abstract/protocol class(es) ({_proto_names737})"
            f" — abstraction designed but no active implementations remain; remove or implement"
        )


def _signals_dead_typed_a(graph: Tempo, scored: list[tuple[Symbol, int]], dead: list[Symbol], lines: list[str], dead_typing_files: list, dead_util_fns: list) -> None:
    """Dead code signals S536-S737: typed patterns batch A — dispatcher."""
    _nt_cls, _nt_fn, _nt_meth, _nt_var = _typed_a_precompute(graph)
    _typed_a_abstract_types(graph, _nt_cls, _nt_fn, _nt_meth, _nt_var, dead_typing_files, lines)
    _typed_a_class_service(graph, _nt_cls, dead, dead_util_fns, lines)
    _typed_a_symbol_patterns(graph, dead, lines)
    _typed_a_fn_specialized(dead, lines)


def _typed_b_fn_name_patterns(graph: Tempo, _d_fn_meth: list, _d_cls: list, lines: list[str]) -> None:
    """S743-S779: fn/method name-keyword patterns + method-only and single-method dead classes."""
    # S743: Dead cache functions — unused functions with caching/memoization names.
    # Caching functions that are never called indicate abandoned performance optimizations
    # or superseded caching strategies; they add complexity without delivering benefit.
    _cache_kws743 = ("cache", "memo", "memoize", "cached", "memoized")
    _dead_cache743 = [
        s for s in _d_fn_meth
        if any(kw in s.name.lower() for kw in _cache_kws743)
    ]
    if _dead_cache743:
        _cache_names743 = ", ".join(s.name for s in _dead_cache743[:3])
        if len(_dead_cache743) > 3:
            _cache_names743 += f" +{len(_dead_cache743) - 3} more"
        lines.append(
            f"dead cache functions: {len(_dead_cache743)} unused caching function(s) ({_cache_names743})"
            f" — abandoned performance optimization or superseded caching strategy"
        )

    # S749: Dead validation functions — unused functions with validation-related names.
    # Validation functions (validate, check, verify, ensure) that are never called indicate
    # abandoned validation strategies or validation removed without cleanup.
    _val_kws749 = ("validate", "check", "verify", "ensure")
    _dead_vals749 = [
        s for s in _d_fn_meth
        if any(kw in s.name.lower() for kw in _val_kws749)
    ]
    if _dead_vals749:
        _val_names749 = ", ".join(s.name for s in _dead_vals749[:3])
        if len(_dead_vals749) > 3:
            _val_names749 += f" +{len(_dead_vals749) - 3} more"
        lines.append(
            f"dead validation functions: {len(_dead_vals749)} unused validation function(s) ({_val_names749})"
            f" — abandoned validation strategy or removed without cleanup"
        )

    # S755: Dead method-only class — unused class whose children are exclusively methods (no instance vars).
    # Classes with only methods are often namespace groupings or utility collections;
    # if the class itself is dead, consider converting methods to module-level functions.
    _dead_static755 = []
    for _s755 in _d_cls:
        _children755 = graph.children_of(_s755.id)
        if _children755 and all(
            c.kind.value in ("function", "method", "classmethod", "staticmethod")
            for c in _children755
        ):
            _dead_static755.append(_s755)
    if _dead_static755:
        _names755 = ", ".join(s.name for s in _dead_static755[:3])
        if len(_dead_static755) > 3:
            _names755 += f" +{len(_dead_static755) - 3} more"
        lines.append(
            f"dead static-only class: {len(_dead_static755)} unused class(es) with only class/static methods ({_names755})"
            f" — namespace classes with no instances; convert methods to module-level functions"
        )

    # S761: Dead event handlers — unused functions with on_/handle_ event naming patterns.
    # Event handlers with on_/handle_ prefixes that are never called indicate abandoned
    # event integrations or removed event sources; they add dead code without any benefit.
    _event_kws761 = ("on_", "handle_", "listener_", "subscriber_")
    _dead_events761 = [
        s for s in _d_fn_meth
        if any(s.name.lower().startswith(kw) for kw in _event_kws761)
    ]
    if _dead_events761:
        _ev_names761 = ", ".join(s.name for s in _dead_events761[:3])
        if len(_dead_events761) > 3:
            _ev_names761 += f" +{len(_dead_events761) - 3} more"
        lines.append(
            f"dead event handlers: {len(_dead_events761)} unused event handler(s) ({_ev_names761})"
            f" — abandoned event integration; the event source was likely removed"
        )

    # S767: Dead getter functions — unused functions with get_/fetch_/load_ prefix patterns.
    # Getter-style functions that are never called indicate abandoned data retrieval logic;
    # they often represent queries or loaders from a feature that was removed or replaced.
    _getter_kws767 = ("get_", "fetch_", "load_", "retrieve_", "find_")
    _dead_getters767 = [
        s for s in _d_fn_meth
        if any(s.name.lower().startswith(kw) for kw in _getter_kws767)
    ]
    if _dead_getters767:
        _g_names767 = ", ".join(s.name for s in _dead_getters767[:3])
        if len(_dead_getters767) > 3:
            _g_names767 += f" +{len(_dead_getters767) - 3} more"
        lines.append(
            f"dead getter functions: {len(_dead_getters767)} unused getter function(s) ({_g_names767})"
            f" — abandoned data retrieval logic; feature or query was removed"
        )

    # S773: Dead single-method class — unused class with exactly one method.
    # A class with only one method usually wraps a single function unnecessarily;
    # if dead, the method can be extracted as a top-level function and the class removed.
    _dead_single773 = []
    for _s773 in _d_cls:
        _methods773 = [
            c for c in graph.children_of(_s773.id)
            if c.kind.value in ("function", "method", "classmethod", "staticmethod")
        ]
        if len(_methods773) == 1:
            _dead_single773.append((_s773, _methods773[0]))
    if _dead_single773:
        _names773 = ", ".join(f"{cls.name}.{mth.name}" for cls, mth in _dead_single773[:3])
        if len(_dead_single773) > 3:
            _names773 += f" +{len(_dead_single773) - 3} more"
        lines.append(
            f"dead single-method class: {len(_dead_single773)} unused single-method class(es) ({_names773})"
            f" — unnecessary wrapping; extract the method as a module-level function"
        )

    # S779: Dead dispatch functions — unused functions with dispatch/route/handle naming.
    # Dispatch and routing functions coordinate control flow; when dead, they indicate
    # an abandoned routing strategy, an unused API endpoint, or a removed feature branch.
    _dispatch_kws779 = ("dispatch", "route_", "handle_request", "process_", "execute_")
    _dead_disp779 = [
        s for s in _d_fn_meth
        if any(s.name.lower().startswith(kw) or s.name.lower() == kw.rstrip("_") for kw in _dispatch_kws779)
    ]
    if _dead_disp779:
        _d_names779 = ", ".join(s.name for s in _dead_disp779[:3])
        if len(_dead_disp779) > 3:
            _d_names779 += f" +{len(_dead_disp779) - 3} more"
        lines.append(
            f"dead dispatch functions: {len(_dead_disp779)} unused dispatch/routing function(s) ({_d_names779})"
            f" — abandoned control-flow logic; endpoint or feature was removed"
        )


def _typed_b_class_patterns(_d_fn_meth: list, _d_cls: list, _d_const: list, lines: list[str]) -> None:
    """S785-S839: dead class shape patterns (subclass, constants cluster, exceptions, API endpoints, mixins, dataclasses, abstract, protocol)."""
    # S791: Dead subclass — dead class that inherits from a named base (not just object).
    # Subclasses carry the burden of the parent's interface; dead subclasses indicate
    # a plugin, strategy, or hook that was never activated or was removed.
    _dead_subclasses791 = []
    for _s791 in _d_cls:
        if (
            _s791.signature is not None
            and "(" in _s791.signature
            and not _s791.signature.rstrip().endswith("()")
            and not _s791.signature.rstrip().endswith("(object)")
        ):
            _dead_subclasses791.append(_s791)
    if _dead_subclasses791:
        _sc_names791 = ", ".join(s.name for s in _dead_subclasses791[:3])
        if len(_dead_subclasses791) > 3:
            _sc_names791 += f" +{len(_dead_subclasses791) - 3} more"
        lines.append(
            f"dead subclass: {len(_dead_subclasses791)} dead class(es) with inheritance ({_sc_names791})"
            f" — unused plugin/strategy/hook; the parent contract is also dead weight"
        )

    # S785: Dead constants cluster — 3+ dead module-level constants in the same file.
    # When multiple constants from the same file are all unused, the file may represent
    # a removed feature's configuration; the entire constants file may be safe to delete.
    _dead_consts785 = [
        s for s in _d_const
        if s.parent_id is None
    ]
    if len(_dead_consts785) >= 3:
        from collections import Counter as _Counter785
        _file_counts785 = _Counter785(s.file_path for s in _dead_consts785)
        _top_file785, _top_count785 = _file_counts785.most_common(1)[0]
        if _top_count785 >= 3:
            _const_names785 = [s.name for s in _dead_consts785 if s.file_path == _top_file785][:3]
            lines.append(
                f"dead constants cluster: {_top_count785} unused constants in"
                f" {_top_file785.rsplit('/', 1)[-1]}"
                f" ({', '.join(_const_names785)}{'...' if _top_count785 > 3 else ''})"
                f" — entire constants file may be safe to remove"
            )

    # S803: Dead exception classes — unused exception/error class definitions.
    # Dead exception classes indicate removed error handling paths or replaced error hierarchies;
    # they create confusion for maintainers who wonder which errors to catch.
    _dead_exc803 = [
        s for s in _d_cls
        if (
            s.name.endswith("Error") or s.name.endswith("Exception")
            or s.name.endswith("Warning") or s.name.endswith("Fault")
        )
    ]
    if _dead_exc803:
        _exc_names803 = ", ".join(s.name for s in _dead_exc803[:3])
        if len(_dead_exc803) > 3:
            _exc_names803 += f" +{len(_dead_exc803) - 3} more"
        lines.append(
            f"dead exception classes: {len(_dead_exc803)} unused exception class(es) ({_exc_names803})"
            f" — removed error handling paths; these exceptions are never raised or caught"
        )

    # S797: Dead API endpoint — dead functions in files named with api/endpoint/view/handler.
    # Dead API handlers indicate removed routes or abandoned API versions;
    # they may still accept requests if routing configuration wasn't updated.
    _api_kws797 = ("api", "endpoint", "endpoints", "view", "views", "handler", "handlers", "route", "routes")
    _dead_api797 = [
        s for s in _d_fn_meth
        if any(kw in s.file_path.replace("\\", "/").rsplit("/", 1)[-1].replace(".py", "").lower()
                for kw in _api_kws797)
    ]
    if _dead_api797:
        _a_names797 = ", ".join(s.name for s in _dead_api797[:3])
        if len(_dead_api797) > 3:
            _a_names797 += f" +{len(_dead_api797) - 3} more"
        lines.append(
            f"dead API endpoints: {len(_dead_api797)} unused function(s) in API/handler files ({_a_names797})"
            f" — removed route handlers; verify routing config no longer references them"
        )

    # S809: Dead mixins — unused mixin classes.
    # Mixin classes extend base class behaviour via multiple inheritance; dead mixins indicate
    # abandoned feature extensions that may leave the inheritance chain with gaps.
    _dead_mixins809 = [
        s for s in _d_cls
        if "Mixin" in s.name
    ]
    if _dead_mixins809:
        _mx_names809 = ", ".join(s.name for s in _dead_mixins809[:3])
        if len(_dead_mixins809) > 3:
            _mx_names809 += f" +{len(_dead_mixins809) - 3} more"
        lines.append(
            f"dead mixins: {len(_dead_mixins809)} unused mixin class(es) ({_mx_names809})"
            f" — abandoned feature extensions; check if any base class still expects them"
        )

    # S821: Dead dataclasses — unused dataclass or TypedDict definitions.
    # Dataclasses and TypedDicts define structured data contracts; dead ones indicate
    # abandoned data shapes that were never wired into the data pipeline.
    _dead_dc821 = [
        s for s in _d_cls
        if (s.name.endswith("Data") or s.name.endswith("Dto") or s.name.endswith("Schema")
             or s.name.endswith("Model") or s.name.endswith("Config") or s.name.endswith("Params")
             or s.name.endswith("TypedDict") or "TypedDict" in s.name)
    ]
    if _dead_dc821:
        _dc_names821 = ", ".join(s.name for s in _dead_dc821[:3])
        if len(_dead_dc821) > 3:
            _dc_names821 += f" +{len(_dead_dc821) - 3} more"
        lines.append(
            f"dead data models: {len(_dead_dc821)} unused data class(es) ({_dc_names821})"
            f" — abandoned data shapes; verify no serialization or API contract depends on them"
        )

    # S815: Dead abstract classes — unused abstract base classes.
    # Abstract classes define contracts for subclasses; a dead abstract class means no
    # concrete implementation is wired in, leaving the design pattern incomplete.
    _dead_abc815 = [
        s for s in _d_cls
        if (s.name.startswith("Abstract") or s.name.startswith("Base") or "Abstract" in s.name[1:])
    ]
    if _dead_abc815:
        _abc_names815 = ", ".join(s.name for s in _dead_abc815[:3])
        if len(_dead_abc815) > 3:
            _abc_names815 += f" +{len(_dead_abc815) - 3} more"
        lines.append(
            f"dead abstract classes: {len(_dead_abc815)} unused abstract/base class(es) ({_abc_names815})"
            f" — no concrete implementation wired in; the design contract is orphaned"
        )

    # S827: Dead migration functions — unused functions with migration/upgrade/rollback naming.
    # Migration functions are critical data-transformation operations; dead ones indicate
    # abandoned upgrade paths or replaced migration strategies that were never cleaned up.
    _mig_prefixes827 = ("migrate_", "upgrade_", "rollback_", "revert_", "downgrade_")
    _dead_mig827 = [
        s for s in _d_fn_meth
        if any(s.name.lower().startswith(p) for p in _mig_prefixes827)
    ]
    if _dead_mig827:
        _mig_names827 = ", ".join(s.name for s in _dead_mig827[:3])
        if len(_dead_mig827) > 3:
            _mig_names827 += f" +{len(_dead_mig827) - 3} more"
        lines.append(
            f"dead migration functions: {len(_dead_mig827)} unused migration/rollback function(s) ({_mig_names827})"
            f" — abandoned data-transformation paths; verify schema changes were completed"
        )

    # S833: Dead CLI commands — unused functions in CLI/command files.
    # Dead CLI commands indicate removed user-facing features; they add noise to
    # help output and may be invocable via routing config that was never updated.
    _cli_kws833 = ("cli", "commands", "command", "cmd", "cmds", "console", "entrypoints")
    _dead_cli833 = [
        s for s in _d_fn_meth
        if any(kw == s.file_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
                or s.file_path.replace("\\", "/").rsplit("/", 1)[-1].rsplit(".", 1)[0].lower().startswith(kw + "_")
                for kw in _cli_kws833)
    ]
    if _dead_cli833:
        _cli_names833 = ", ".join(s.name for s in _dead_cli833[:3])
        if len(_dead_cli833) > 3:
            _cli_names833 += f" +{len(_dead_cli833) - 3} more"
        lines.append(
            f"dead CLI commands: {len(_dead_cli833)} unused function(s) in CLI/command files ({_cli_names833})"
            f" — removed user-facing commands; verify help text and routing are updated"
        )

    # S839: Dead protocol classes — unused classes ending with Protocol.
    # Protocol classes define structural typing contracts (PEP 544); dead protocols
    # indicate abandoned interface contracts that no concrete class is checked against.
    _dead_proto839 = [
        s for s in _d_cls
        if s.name.endswith("Protocol")
    ]
    if _dead_proto839:
        _proto_names839 = ", ".join(s.name for s in _dead_proto839[:3])
        if len(_dead_proto839) > 3:
            _proto_names839 += f" +{len(_dead_proto839) - 3} more"
        lines.append(
            f"dead protocol classes: {len(_dead_proto839)} unused Protocol class(es) ({_proto_names839})"
            f" — abandoned interface contracts; verify no concrete class is checked against them"
        )


def _typed_b_fn_lifecycle(graph: Tempo, dead: list, _d_fn_meth: list, _d_cls: list, _d_const: list, lines: list[str]) -> None:
    """S845-S911: fn/method lifecycle signals (event handlers, validators, factories, singletons, module constants, type aliases, test helpers, middleware, exported symbols, error handlers, thin classes, async)."""
    # S845: Dead event handler functions — unused on_/handle_/_handler functions.
    # Event handlers are wired to event buses, signals, or hooks; dead handlers indicate
    # removed subscriptions where the handler was not cleaned up alongside the subscription.
    _handler_prefixes845 = ("on_", "handle_")
    _handler_suffixes845 = ("_handler", "_callback", "_listener")
    _dead_handlers845 = [
        s for s in _d_fn_meth
        if (
            any(s.name.lower().startswith(p) for p in _handler_prefixes845)
            or any(s.name.lower().endswith(sfx) for sfx in _handler_suffixes845)
        )
    ]
    if _dead_handlers845:
        _handler_names845 = ", ".join(s.name for s in _dead_handlers845[:3])
        if len(_dead_handlers845) > 3:
            _handler_names845 += f" +{len(_dead_handlers845) - 3} more"
        lines.append(
            f"dead event handlers: {len(_dead_handlers845)} unused event handler function(s) ({_handler_names845})"
            f" — removed subscriptions not cleaned up; verify the event or signal was also removed"
        )

    # S851: Dead validation functions — unused validate_/check_/verify_ functions.
    # Dead validators indicate abandoned input sanity checks or replaced validation logic;
    # they may signal that callers no longer validate data they were once required to check.
    _val_prefixes851 = ("validate_", "check_", "verify_", "assert_", "ensure_")
    _dead_val851 = [
        s for s in _d_fn_meth
        if any(s.name.lower().startswith(p) for p in _val_prefixes851)
    ]
    if _dead_val851:
        _val_names851 = ", ".join(s.name for s in _dead_val851[:3])
        if len(_dead_val851) > 3:
            _val_names851 += f" +{len(_dead_val851) - 3} more"
        lines.append(
            f"dead validators: {len(_dead_val851)} unused validation function(s) ({_val_names851})"
            f" — abandoned input checks; verify callers no longer need these validations"
        )

    # S857: Dead factory functions — unused create_/make_/build_/spawn_/new_ functions.
    # Factory functions are construction entry points; dead factories indicate
    # abandoned object creation paths that were replaced without deleting the old code.
    _factory_prefixes857 = ("create_", "make_", "build_", "spawn_", "new_", "construct_")
    _dead_factory857 = [
        s for s in _d_fn_meth
        if any(s.name.lower().startswith(p) for p in _factory_prefixes857)
    ]
    if _dead_factory857:
        _factory_names857 = ", ".join(s.name for s in _dead_factory857[:3])
        if len(_dead_factory857) > 3:
            _factory_names857 += f" +{len(_dead_factory857) - 3} more"
        lines.append(
            f"dead factories: {len(_dead_factory857)} unused factory function(s) ({_factory_names857})"
            f" — abandoned construction paths; verify the object is constructed elsewhere"
        )

    # S863: Dead singleton accessors — unused get_instance/get_singleton/get_current functions.
    # Singleton accessors are global state entry points; dead ones indicate removed singleton
    # patterns where the accessor was not cleaned up alongside the singleton class itself.
    _singleton_patterns863 = {"get_instance", "get_singleton", "get_registry", "instance", "get_current"}
    _dead_singletons863 = [
        s for s in _d_fn_meth
        if s.name.lower() in _singleton_patterns863
    ]
    if _dead_singletons863:
        _singleton_names863 = ", ".join(s.name for s in _dead_singletons863[:3])
        lines.append(
            f"dead singletons: {len(_dead_singletons863)} unused singleton accessor(s) ({_singleton_names863})"
            f" — unused global state accessors; verify no code bypasses through direct attribute access"
        )

    # S869: Dead module-level constants — unused UPPERCASE constant definitions.
    # Module-level constants represent configuration values and feature flags; unused ones
    # indicate removed features or replaced configuration that was never cleaned up.
    _dead_consts869 = [
        s for s in _d_const
        if s.kind.value == "constant" and s.parent_id is None
        and s.name == s.name.upper()
        and len(s.name) >= 2
        and not s.name.startswith("_")
    ]
    if _dead_consts869:
        _const_names869 = ", ".join(s.name for s in _dead_consts869[:3])
        if len(_dead_consts869) > 3:
            _const_names869 += f" +{len(_dead_consts869) - 3} more"
        lines.append(
            f"dead constants: {len(_dead_consts869)} unused module-level constant(s) ({_const_names869})"
            f" — abandoned configuration; verify no code uses these via dynamic attribute lookup"
        )

    # S875: Dead type aliases — unused symbols whose names end with Type or start with T_ or Type_.
    # Type aliases are used for documentation and type-checking; dead type aliases indicate
    # renamed or removed types where the alias was not cleaned up.
    _dead_types875 = [
        s for s in _d_const + _d_cls
        if s.parent_id is None
        and (
            s.name.endswith("Type") or s.name.endswith("Types")
            or s.name.startswith("T_") or s.name.startswith("Type_")
        )
    ]
    if _dead_types875:
        _type_names875 = ", ".join(s.name for s in _dead_types875[:3])
        if len(_dead_types875) > 3:
            _type_names875 += f" +{len(_dead_types875) - 3} more"
        lines.append(
            f"dead type aliases: {len(_dead_types875)} unused type alias(es) ({_type_names875})"
            f" — stale type annotations; verify no type checker or IDE depends on these"
        )

    # S881: Dead test helper functions — unused helpers/fixtures/mocks in test files.
    # Leftover test utilities accumulate as test suites evolve; they add noise and
    # may mislead agents into thinking certain behaviors are tested when they are not.
    _dead_helpers881 = [
        s for s in dead
        if s.kind.value in ("function", "method")
        and s.parent_id is None
        and _is_test_file(s.file_path)
        and any(
            kw in s.name.lower()
            for kw in ("helper", "fixture", "mock", "stub", "fake", "setup", "factory")
        )
    ]
    if _dead_helpers881:
        _helper_names881 = ", ".join(s.name for s in _dead_helpers881[:3])
        if len(_dead_helpers881) > 3:
            _helper_names881 += f" +{len(_dead_helpers881) - 3} more"
        lines.append(
            f"dead test helpers: {len(_dead_helpers881)} unused test helper(s) ({_helper_names881})"
            f" — orphaned test utilities; test coverage may be misleadingly broad"
        )

    # S887: Dead middleware/interceptors — unused functions with middleware/interceptor/filter prefix.
    # Dead middleware indicates removed pipeline steps; leftover stubs may still be registered
    # in framework configs, silently consuming resources or blocking requests.
    _mw_prefixes887 = ("middleware_", "interceptor_", "filter_", "before_", "after_", "pre_", "post_")
    _mw_contains887 = ("middleware", "interceptor")
    _dead_mw887 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and (
            any(s.name.lower().startswith(p) for p in _mw_prefixes887)
            or any(kw in s.name.lower() for kw in _mw_contains887)
        )
    ]
    if _dead_mw887:
        _mw_names887 = ", ".join(s.name for s in _dead_mw887[:3])
        if len(_dead_mw887) > 3:
            _mw_names887 += f" +{len(_dead_mw887) - 3} more"
        lines.append(
            f"dead middleware: {len(_dead_mw887)} unused middleware/interceptor function(s) ({_mw_names887})"
            f" — may still be registered in framework config; verify before deleting"
        )

    # S893: Dead exported symbols — unused functions or classes that are part of the public API.
    # Exported dead symbols may be consumed by external callers not visible in this graph;
    # removing them without checking downstream consumers can break public API contracts.
    _dead_exported893 = [
        s for s in _d_fn_meth + _d_cls
        if s.exported and s.parent_id is None and s.kind.value in ("function", "class")
    ]
    if _dead_exported893:
        _exp_names893 = ", ".join(s.name for s in _dead_exported893[:3])
        if len(_dead_exported893) > 3:
            _exp_names893 += f" +{len(_dead_exported893) - 3} more"
        lines.append(
            f"dead exports: {len(_dead_exported893)} unused exported symbol(s) ({_exp_names893})"
            f" — public API symbols with no internal callers; check external consumers before removing"
        )

    # S899: Dead error handlers — unused functions with error/exception handling naming.
    # Orphaned error handlers indicate removed error recovery paths; they may still be
    # registered in framework configs or expected by callers that are themselves dead.
    _dead_errors899 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(
            kw in s.name.lower()
            for kw in ("error", "exception", "catch", "on_error", "onerror", "handle_exc")
        )
    ]
    if _dead_errors899:
        _err_names899 = ", ".join(s.name for s in _dead_errors899[:3])
        if len(_dead_errors899) > 3:
            _err_names899 += f" +{len(_dead_errors899) - 3} more"
        lines.append(
            f"dead error handlers: {len(_dead_errors899)} unused error handling function(s) ({_err_names899})"
            f" — orphaned error handlers may indicate removed error recovery paths"
        )

    # S905: Dead single-method classes — dead class definitions that contain exactly one method.
    # Single-method classes may be over-engineered; they are candidates for conversion to
    # plain functions, reducing instantiation overhead and simplifying call sites.
    _dead_classes905 = [s for s in _d_cls if s.parent_id is None]
    _single_method_classes905 = []
    for _cls905 in _dead_classes905:
        _methods905 = [
            c for c in graph.children_of(_cls905.id)
            if c.kind.value in ("method", "function")
        ]
        if len(_methods905) == 1:
            _single_method_classes905.append(_cls905)
    if _single_method_classes905:
        _cls_names905 = ", ".join(s.name for s in _single_method_classes905[:3])
        if len(_single_method_classes905) > 3:
            _cls_names905 += f" +{len(_single_method_classes905) - 3} more"
        lines.append(
            f"dead thin classes: {len(_single_method_classes905)} dead single-method class(es) ({_cls_names905})"
            f" — consider converting to plain functions to reduce over-engineering"
        )

    # S911: Dead async functions — unused async/coroutine functions.
    # Dead async functions may be part of event loops or background task registries;
    # removing them may silently drop background processing if dynamically registered.
    _dead_async911 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and s.signature.startswith("async def")
    ]
    if _dead_async911:
        _async_names911 = ", ".join(s.name for s in _dead_async911[:3])
        if len(_dead_async911) > 3:
            _async_names911 += f" +{len(_dead_async911) - 3} more"
        lines.append(
            f"dead async: {len(_dead_async911)} unused async function(s) ({_async_names911})"
            f" — may be registered background tasks; verify no dynamic registration before removing"
        )


def _typed_b_fn_operational(graph: Tempo, _d_fn_meth: list, _d_cls: list, lines: list[str]) -> None:
    """S917-S1019: fn/method operational signals (callbacks, formatters, data classes, exception classes, setup, type guards, serializers, hooks, tasks, converters, SQL, event dispatchers, validators, builders, error handlers, migrations, routers)."""
    # S917: Dead callback functions — unused on_/handle_/callback_/cb_ prefixed functions.
    # Dead callbacks indicate event-driven logic removed from registration; they may still
    # be referenced in configuration files or event registries outside the graph.
    _cb_prefixes917 = ("on_", "handle_", "callback_", "cb_")
    _cb_contains917 = ("callback", "_cb")
    _dead_cbs917 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and (
            any(s.name.lower().startswith(p) for p in _cb_prefixes917)
            or any(kw in s.name.lower() for kw in _cb_contains917)
        )
    ]
    if _dead_cbs917:
        _cb_names917 = ", ".join(s.name for s in _dead_cbs917[:3])
        if len(_dead_cbs917) > 3:
            _cb_names917 += f" +{len(_dead_cbs917) - 3} more"
        lines.append(
            f"dead callbacks: {len(_dead_cbs917)} unused callback function(s) ({_cb_names917})"
            f" — may still be registered in event configs; verify before removing"
        )

    # S923: Dead formatters — unused format_/render_/display_/to_ prefixed functions.
    # Dead formatters often indicate removed presentation paths; the business logic
    # they contained may still be needed for other output formats.
    _fmt_prefixes923 = ("format_", "render_", "display_", "to_", "fmt_", "stringify_")
    _dead_fmts923 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _fmt_prefixes923)
    ]
    if _dead_fmts923:
        _fmt_names923 = ", ".join(s.name for s in _dead_fmts923[:3])
        if len(_dead_fmts923) > 3:
            _fmt_names923 += f" +{len(_dead_fmts923) - 3} more"
        lines.append(
            f"dead formatters: {len(_dead_fmts923)} unused formatter function(s) ({_fmt_names923})"
            f" — removed presentation paths; verify the business logic they contained is no longer needed"
        )

    # S929: Dead data classes — unused classes with no methods (pure data containers).
    # Data classes with no methods are often replaced by dicts, TypedDicts, or dataclasses;
    # orphaned ones may indicate a model layer that was refactored without removing old types.
    _dead_data929 = []
    for _cls929 in _d_cls:
        _children929 = [
            c for c in graph.children_of(_cls929.id)
            if c.kind.value in ("method", "function")
        ]
        if not _children929:
            _dead_data929.append(_cls929)
    if _dead_data929:
        _data_names929 = ", ".join(s.name for s in _dead_data929[:3])
        if len(_dead_data929) > 3:
            _data_names929 += f" +{len(_dead_data929) - 3} more"
        lines.append(
            f"dead data classes: {len(_dead_data929)} unused method-free class(es) ({_data_names929})"
            f" — no-method classes may be leftover models; consider converting to TypedDict or dataclass"
        )

    # S935: Dead exception classes — unused custom exception or error classes.
    # Orphaned exception classes indicate removed error handling paths; the error
    # conditions they represented may be silently suppressed or propagated differently.
    _dead_exc935 = [
        s for s in _d_cls
        if s.parent_id is None
        and (
            s.name.endswith(("Error", "Exception", "Warning", "Fault", "Failure"))
            or "Error" in s.name or "Exception" in s.name
        )
    ]
    if _dead_exc935:
        _exc_names935 = ", ".join(s.name for s in _dead_exc935[:3])
        if len(_dead_exc935) > 3:
            _exc_names935 += f" +{len(_dead_exc935) - 3} more"
        lines.append(
            f"dead exceptions: {len(_dead_exc935)} unused exception class(es) ({_exc_names935})"
            f" — orphaned error types; removed error handling paths may be silently suppressing errors"
        )

    # S941: Dead setup functions — unused configure_/setup_/init_ prefixed functions.
    # Dead setup functions indicate initialization paths that were removed; if they
    # registered resources or side effects, those may now be silently skipped.
    _setup_prefixes941 = ("configure_", "setup_", "init_", "initialize_", "bootstrap_", "register_")
    _dead_setup941 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _setup_prefixes941)
    ]
    if _dead_setup941:
        _setup_names941 = ", ".join(s.name for s in _dead_setup941[:3])
        if len(_dead_setup941) > 3:
            _setup_names941 += f" +{len(_dead_setup941) - 3} more"
        lines.append(
            f"dead setup: {len(_dead_setup941)} unused setup/init function(s) ({_setup_names941})"
            f" — removed initialization paths may leave resources unregistered or unconfigured"
        )

    # S947: Dead type guards — unused is_* boolean predicate functions.
    # Dead type guards often outlive the type annotations or isinstance() calls that replaced them;
    # verify no dynamic dispatch or conditional code still depends on these predicates.
    _dead_typeguards947 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and s.name.lower().startswith("is_")
        and len(s.name) > 3
    ]
    if _dead_typeguards947:
        _tg_names947 = ", ".join(s.name for s in _dead_typeguards947[:3])
        if len(_dead_typeguards947) > 3:
            _tg_names947 += f" +{len(_dead_typeguards947) - 3} more"
        lines.append(
            f"dead type guards: {len(_dead_typeguards947)} unused is_* predicate(s) ({_tg_names947})"
            f" — may have been replaced by type annotations; verify no dynamic dispatch still calls them"
        )

    # S953: Dead serializer functions — unused to_dict/to_json/to_yaml/to_csv/serialize prefixed functions.
    # Dead serializers indicate output paths that were abandoned; callers may still expect
    # serialized output but receive None or raise AttributeError silently.
    _ser_prefixes953 = ("to_dict", "to_json", "to_yaml", "to_csv", "to_xml", "serialize_", "marshal_", "export_")
    _dead_serializers953 = [
        s for s in _d_fn_meth
        if any(s.name.lower().startswith(p) for p in _ser_prefixes953)
    ]
    if _dead_serializers953:
        _ser_names953 = ", ".join(s.name for s in _dead_serializers953[:3])
        if len(_dead_serializers953) > 3:
            _ser_names953 += f" +{len(_dead_serializers953) - 3} more"
        lines.append(
            f"dead serializers: {len(_dead_serializers953)} unused serialization function(s) ({_ser_names953})"
            f" — abandoned output paths; callers expecting serialized output may silently receive None"
        )

    # S959: Dead hook functions — unused functions named hook_* or *_hook.
    # Hook functions are registered with frameworks (lifecycle, plugin, event systems);
    # dead hooks silently skip the event they were meant to intercept.
    _dead_hooks959 = [
        s for s in _d_fn_meth
        if (s.name.lower().startswith("hook_") or s.name.lower().endswith("_hook"))
    ]
    if _dead_hooks959:
        _hook_names959 = ", ".join(s.name for s in _dead_hooks959[:3])
        if len(_dead_hooks959) > 3:
            _hook_names959 += f" +{len(_dead_hooks959) - 3} more"
        lines.append(
            f"dead hooks: {len(_dead_hooks959)} unused hook function(s) ({_hook_names959})"
            f" — unregistered hooks silently skip; the event they were meant to intercept now goes unhandled"
        )

    # S965: Dead scheduled tasks — unused schedule_/cron_/task_/job_ prefixed functions.
    # Dead scheduled tasks indicate removed automation paths; if they produced side effects
    # (data cleanup, notifications, reports), those effects now silently no longer happen.
    _sched_prefixes965 = ("schedule_", "cron_", "task_", "job_", "periodic_", "daily_", "hourly_")
    _dead_tasks965 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _sched_prefixes965)
    ]
    if _dead_tasks965:
        _task_names965 = ", ".join(s.name for s in _dead_tasks965[:3])
        if len(_dead_tasks965) > 3:
            _task_names965 += f" +{len(_dead_tasks965) - 3} more"
        lines.append(
            f"dead tasks: {len(_dead_tasks965)} unused scheduled task(s) ({_task_names965})"
            f" — removed automation paths; verify any data cleanup or notification side effects are still handled"
        )

    # S971: Dead converters — unused convert_/transform_/map_/parse_ prefixed functions.
    # Dead converters indicate data transformation pipelines that were removed or replaced;
    # if callers now skip conversion steps, data format mismatches may silently corrupt output.
    _conv_prefixes971 = ("convert_", "transform_", "map_", "parse_", "translate_", "normalize_")
    _dead_converters971 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _conv_prefixes971)
    ]
    if _dead_converters971:
        _conv_names971 = ", ".join(s.name for s in _dead_converters971[:3])
        if len(_dead_converters971) > 3:
            _conv_names971 += f" +{len(_dead_converters971) - 3} more"
        lines.append(
            f"dead converters: {len(_dead_converters971)} unused data transformation function(s) ({_conv_names971})"
            f" — removed conversion steps may leave callers passing unformatted data silently"
        )

    # S977: Dead SQL functions — unused sql_/query_/select_/fetch_/insert_/delete_ prefixed functions.
    # Dead database query functions indicate removed data access paths; if application
    # logic still attempts to call them, the result is a silent data gap or runtime error.
    _sql_prefixes977 = ("sql_", "query_", "select_", "fetch_", "insert_", "delete_", "update_", "upsert_")
    _dead_sql977 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _sql_prefixes977)
    ]
    if _dead_sql977:
        _sql_names977 = ", ".join(s.name for s in _dead_sql977[:3])
        if len(_dead_sql977) > 3:
            _sql_names977 += f" +{len(_dead_sql977) - 3} more"
        lines.append(
            f"dead sql: {len(_dead_sql977)} unused database query function(s) ({_sql_names977})"
            f" — removed data access paths; callers expecting query results may silently get None or raise"
        )

    # S983: Dead event dispatchers — unused send_/notify_/emit_/dispatch_/publish_/broadcast_ prefixed functions.
    # Dead event publishers leave subscribers waiting for signals that never arrive;
    # if consumers block on these events, they may hang, poll, or process stale state.
    _event_prefixes983 = ("send_", "notify_", "emit_", "dispatch_", "publish_", "broadcast_", "fire_", "trigger_")
    _dead_events983 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _event_prefixes983)
    ]
    if _dead_events983:
        _evt_names983 = ", ".join(s.name for s in _dead_events983[:3])
        if len(_dead_events983) > 3:
            _evt_names983 += f" +{len(_dead_events983) - 3} more"
        lines.append(
            f"dead events: {len(_dead_events983)} unused event dispatch function(s) ({_evt_names983})"
            f" — removed publishers leave subscribers waiting for signals that never arrive"
        )

    # S989: Dead validators — unused validate_/check_/verify_/assert_/ensure_/guard_ prefixed functions.
    # Dead validation functions indicate removed input guards; callers may now pass invalid
    # data that was previously caught, leading to silent corruption or runtime errors.
    _val_prefixes989 = ("validate_", "check_", "verify_", "assert_", "ensure_", "guard_", "is_valid_", "must_")
    _dead_validators989 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _val_prefixes989)
    ]
    if _dead_validators989:
        _val_names989 = ", ".join(s.name for s in _dead_validators989[:3])
        if len(_dead_validators989) > 3:
            _val_names989 += f" +{len(_dead_validators989) - 3} more"
        lines.append(
            f"dead validators: {len(_dead_validators989)} unused validation function(s) ({_val_names989})"
            f" — removed guards may leave callers passing invalid data silently"
        )

    # S995: Dead formatters — unused format_/render_/display_/present_/fmt_ prefixed functions.
    # Dead formatter functions indicate removed output transformation pipelines;
    # callers may receive raw or incorrectly structured data when formatters go missing.
    _fmt_prefixes995 = ("format_", "fmt_", "render_", "display_", "present_", "show_", "print_", "stringify_")
    _dead_formatters995 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _fmt_prefixes995)
    ]
    if _dead_formatters995:
        _fmt_names995 = ", ".join(s.name for s in _dead_formatters995[:3])
        if len(_dead_formatters995) > 3:
            _fmt_names995 += f" +{len(_dead_formatters995) - 3} more"
        lines.append(
            f"dead formatters: {len(_dead_formatters995)} unused output formatting function(s) ({_fmt_names995})"
            f" — removed formatters may leave callers receiving raw or incorrectly structured data"
        )

    # S1001: Dead builders — unused build_/create_/make_/construct_/factory_ prefixed functions.
    # Dead factory functions indicate removed object creation paths; callers may be unable
    # to instantiate objects they expect, causing AttributeErrors or None-type failures.
    _build_prefixes1001 = ("build_", "create_", "make_", "construct_", "new_", "factory_", "produce_", "generate_")
    _dead_builders1001 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _build_prefixes1001)
    ]
    if _dead_builders1001:
        _bld_names1001 = ", ".join(s.name for s in _dead_builders1001[:3])
        if len(_dead_builders1001) > 3:
            _bld_names1001 += f" +{len(_dead_builders1001) - 3} more"
        lines.append(
            f"dead builders: {len(_dead_builders1001)} unused factory/builder function(s) ({_bld_names1001})"
            f" — removed object creation paths may leave callers unable to instantiate expected objects"
        )

    # S1007: Dead error handlers — unused handle_error_/on_error/error_handler_ prefixed functions.
    # Dead error handlers indicate removed exception processing paths; callers may now
    # propagate unhandled exceptions where errors were previously caught and reported.
    _err_prefixes1007 = ("handle_error", "on_error", "error_handler", "handle_exception", "on_exception", "catch_error", "rescue_")
    _dead_errhandlers1007 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _err_prefixes1007)
    ]
    if _dead_errhandlers1007:
        _err_names1007 = ", ".join(s.name for s in _dead_errhandlers1007[:3])
        if len(_dead_errhandlers1007) > 3:
            _err_names1007 += f" +{len(_dead_errhandlers1007) - 3} more"
        lines.append(
            f"dead error handlers: {len(_dead_errhandlers1007)} unused error handler(s) ({_err_names1007})"
            f" — removed exception handlers may leave callers with unhandled errors propagating silently"
        )

    # S1013: Dead migrations — unused migrate_/migration_/upgrade_/downgrade_ prefixed functions.
    # Dead migration functions indicate abandoned schema evolution paths; if these are
    # database migrations the schema version table may be inconsistent with the actual schema.
    _mig_prefixes1013 = ("migrate_", "migration_", "upgrade_", "downgrade_", "rollback_", "apply_migration", "run_migration")
    _dead_migrations1013 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _mig_prefixes1013)
    ]
    if _dead_migrations1013:
        _mig_names1013 = ", ".join(s.name for s in _dead_migrations1013[:3])
        if len(_dead_migrations1013) > 3:
            _mig_names1013 += f" +{len(_dead_migrations1013) - 3} more"
        lines.append(
            f"dead migrations: {len(_dead_migrations1013)} unused migration function(s) ({_mig_names1013})"
            f" — abandoned schema evolution paths; schema version table may be inconsistent with actual schema"
        )

    # S1019: Dead routers — unused route_/register_route/add_route/register_ prefixed functions.
    # Dead routing functions indicate removed URL or RPC endpoint registrations;
    # callers attempting those paths will get 404s or unregistered handler errors.
    _route_prefixes1019 = ("route_", "add_route", "register_route", "register_", "add_url", "add_view", "add_endpoint")
    _dead_routers1019 = [
        s for s in _d_fn_meth
        if s.parent_id is None
        and any(s.name.lower().startswith(p) for p in _route_prefixes1019)
    ]
    if _dead_routers1019:
        _route_names1019 = ", ".join(s.name for s in _dead_routers1019[:3])
        if len(_dead_routers1019) > 3:
            _route_names1019 += f" +{len(_dead_routers1019) - 3} more"
        lines.append(
            f"dead routers: {len(_dead_routers1019)} unused routing function(s) ({_route_names1019})"
            f" — removed endpoint registrations; requests to those paths may now return 404 or unregistered errors"
        )


def _typed_b_precompute(dead: list) -> tuple[list, list, list]:
    """S45-precompute: One classification pass over dead instead of 36 repeated scans.
    Before: 36 passes × 1659 items + 77,973 enum accesses. After: 1659 (1 pass) + bucket subsets.
    Returns (_d_fn_meth, _d_cls, _d_const)."""
    _d_fn_meth = [s for s in dead if s.kind.value in ("function", "method") and not _is_test_file(s.file_path)]
    _d_cls = [s for s in dead if s.kind.value == "class" and not _is_test_file(s.file_path)]
    _d_const = [s for s in dead if s.kind.value in ("constant", "variable") and not _is_test_file(s.file_path)]
    return _d_fn_meth, _d_cls, _d_const


def _signals_dead_typed_b(graph: Tempo, scored: list[tuple[Symbol, int]], dead: list[Symbol], lines: list[str]) -> None:
    """Dead code signals S743-S1019: typed patterns batch B — dispatcher."""
    _d_fn_meth, _d_cls, _d_const = _typed_b_precompute(dead)
    _typed_b_fn_name_patterns(graph, _d_fn_meth, _d_cls, lines)
    _typed_b_class_patterns(_d_fn_meth, _d_cls, _d_const, lines)
    _typed_b_fn_lifecycle(graph, dead, _d_fn_meth, _d_cls, _d_const, lines)
    _typed_b_fn_operational(graph, _d_fn_meth, _d_cls, lines)


def _render_dead_header_stats(
    scored: list[tuple[Symbol, int]],
    dead: list[Symbol],
    graph: Tempo,
) -> tuple[str, str]:
    """Compute removable_header (S98) and dead_ratio_str (S109+S126) for the header line."""
    # S98: Total removable lines — sum of line counts for high+medium confidence dead symbols.
    _removable_lines = sum(sym.line_count for sym, conf in scored if conf >= 40)
    removable_header = ""
    if _removable_lines >= 50:
        removable_header = f" (~{_removable_lines} lines removable)"

    # S109: Dead ratio — fraction of total (non-test) symbols that are dead.
    _total_non_test_syms = sum(
        1 for sym in graph.symbols.values() if not _is_test_file(sym.file_path)
    )
    dead_ratio_str = ""
    if _total_non_test_syms >= 10 and dead:
        _high_conf_dead = sum(1 for sym, conf in scored if conf >= 40)
        _ratio_pct = int(_high_conf_dead / _total_non_test_syms * 100)
        if _ratio_pct >= 5:
            dead_ratio_str = f" [{_ratio_pct}% of {_total_non_test_syms} source symbols]"

    # S126: Exported dead ratio — fraction of exported (public API) symbols that are dead.
    _total_exported_src = sum(
        1 for sym in graph.symbols.values()
        if sym.exported and not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method", "class", "interface")
    )
    if _total_exported_src >= 5:
        _dead_exported = sum(
            1 for sym, conf in scored
            if conf >= 40 and sym.exported and sym.kind.value in ("function", "method", "class", "interface")
        )
        _exp_dead_pct = int(_dead_exported / _total_exported_src * 100)
        if _exp_dead_pct >= 20:
            dead_ratio_str += f" [exported: {_dead_exported}/{_total_exported_src} public symbols dead ({_exp_dead_pct}%)]"

    return removable_header, dead_ratio_str


def _render_dead_insights_a(
    graph: Tempo,
    scored: list[tuple[Symbol, int]],
    high: list[tuple[Symbol, int]],
) -> list[str]:
    """Quick wins, largest dead, complex dead, and dead API summary lines."""
    lines: list[str] = []

    # Quick wins: top files with the most HIGH confidence dead symbols.
    if high:
        _qw_counts: dict[str, int] = {}
        for sym, _ in high:
            _qw_counts[sym.file_path] = _qw_counts.get(sym.file_path, 0) + 1
        _qw_sorted = sorted(_qw_counts.items(), key=lambda x: -x[1])[:2]
        _qw_parts = [
            f"{fp.rsplit('/', 1)[-1]} ({n} high-conf)" for fp, n in _qw_sorted
        ]
        lines.append(f"Quick wins: {', '.join(_qw_parts)}")

    # Largest dead: top 3 dead symbols by line count (high+medium confidence only).
    _ld_candidates = sorted(
        [(sym, conf) for sym, conf in scored if conf >= 40],
        key=lambda x: -x[0].line_count,
    )
    if len(_ld_candidates) >= 2 and _ld_candidates[0][0].line_count >= 20:
        _ld_parts = [
            f"{sym.name} ({sym.line_count}L, conf:{conf})"
            for sym, conf in _ld_candidates[:3]
        ]
        lines.append(f"Largest dead: {', '.join(_ld_parts)}")

    # S92: Complex dead — top dead symbols by cyclomatic complexity (cx >= 5).
    _cx_dead = sorted(
        [(sym, conf) for sym, conf in scored if conf >= 40 and sym.complexity >= 5],
        key=lambda x: -x[0].complexity,
    )
    if len(_cx_dead) >= 2:
        _cd_parts = [
            f"{sym.name} (cx:{sym.complexity}, conf:{conf})"
            for sym, conf in _cx_dead[:3]
        ]
        lines.append(f"Complex dead: {', '.join(_cd_parts)}")

    # S95: Dead API — exported symbols with 0 cross-file callers.
    _dead_api = [
        (sym, conf) for sym, conf in scored
        if sym.exported and conf >= 40
        and not any(c.file_path != sym.file_path for c in graph.callers_of(sym.id))
    ]
    if len(_dead_api) >= 2:
        _da_parts = [f"{sym.name} ({sym.file_path.rsplit('/', 1)[-1]}, conf:{conf})" for sym, conf in _dead_api[:4]]
        _da_str = ", ".join(_da_parts)
        if len(_dead_api) > 4:
            _da_str += f" +{len(_dead_api) - 4} more"
        lines.append(f"Dead API ({len(_dead_api)}): {_da_str} — exported, no callers (verify before deleting)")

    return lines


def _render_dead_insights_b(
    graph: Tempo,
    scored: list[tuple[Symbol, int]],
    dead: list[Symbol],
    dead_sym_ids: set[str],
    touched_cache: dict[str, int | None],
) -> list[str]:
    """Clustered dead, orphan files, recently/stale dead, transitively dead, safe-to-delete."""
    from ..git import file_last_modified_days as _file_last_modified_days  # noqa: PLC0415

    lines: list[str] = []

    # S101: Clustered dead — files with 3+ dead symbols are batch cleanup targets.
    _dead_by_file: dict[str, int] = {}
    for sym, conf in scored:
        if conf >= 40:
            _dead_by_file[sym.file_path] = _dead_by_file.get(sym.file_path, 0) + 1
    _clustered = sorted(
        [(fp, cnt) for fp, cnt in _dead_by_file.items() if cnt >= 3],
        key=lambda x: -x[1],
    )
    if len(_clustered) >= 1:
        _cl_parts = [f"{cnt} in {fp.rsplit('/', 1)[-1]}" for fp, cnt in _clustered[:2]]
        lines.append(f"Clustered dead: {', '.join(_cl_parts)} — batch cleanup targets")

    # S[HFD]: Hot-file dead — dead symbols in currently high-velocity files.
    # Different from "Recently dead" (file touched once in 30d): hot_files means
    # the file is in ACTIVE CHURN (many commits/week). Developers are already here;
    # this is the right moment to remove dead code incrementally with the next PR.
    # Only shown when graph.hot_files is populated and ≥1 medium+ confidence match.
    if graph.hot_files:
        _hot_file_dead = [
            (sym, conf) for sym, conf in scored
            if conf >= 40
            and sym.file_path
            and sym.file_path in graph.hot_files
        ]
        if len(_hot_file_dead) >= 1:
            _hfd_by_file: dict[str, list[str]] = {}
            for _hfd_sym, _ in _hot_file_dead:
                _fname = _hfd_sym.file_path.rsplit("/", 1)[-1]
                _hfd_by_file.setdefault(_fname, []).append(_hfd_sym.name)
            _hfd_sorted = sorted(_hfd_by_file.items(), key=lambda x: -len(x[1]))
            _hfd_parts = [
                f"{fname} ({len(names)} dead)" if len(names) > 1 else f"{names[0]} ({fname})"
                for fname, names in _hfd_sorted[:3]
            ]
            _hfd_str = ", ".join(_hfd_parts)
            if len(_hfd_by_file) > 3:
                _hfd_str += f" +{len(_hfd_by_file) - 3} more files"
            lines.append(
                f"Hot-file debt ({len(_hot_file_dead)}): {_hfd_str}"
                f" — dead code in active files, clean up with next PR"
            )

    # Orphan files: files where ALL exported symbols are dead → delete the whole file.
    _orphan_files: list[tuple[str, int, int]] = []
    for _fp in {sym.file_path for sym, _ in scored}:
        if _is_test_file(_fp):
            continue
        _fi = graph.files.get(_fp)
        if not _fi:
            continue
        _exported = [
            graph.symbols[sid] for sid in _fi.symbols
            if sid in graph.symbols and graph.symbols[sid].exported
        ]
        if _exported and all(sym.id in dead_sym_ids for sym in _exported):
            _orphan_files.append((_fp, len(_exported), sum(sym.line_count for sym in _exported)))
    if _orphan_files:
        _orphan_files.sort(key=lambda x: -x[2])
        _o_parts = [
            f"{fp.rsplit('/', 1)[-1]} ({n} syms, {lc} lines)"
            for fp, n, lc in _orphan_files[:3]
        ]
        lines.append(f"Orphan files (all-dead): {', '.join(_o_parts)}")

    def _file_age(fp: str) -> int | None:
        if fp not in touched_cache:
            touched_cache[fp] = _file_last_modified_days(graph.root, fp)
        return touched_cache[fp]

    # Recently dead: dead symbols in files touched in the last 30 days.
    _recently_dead = [
        (sym, conf) for sym, conf in scored
        if conf >= 40
        and (_file_age(sym.file_path) or 9999) <= 30
    ]
    if len(_recently_dead) >= 2:
        _rd_names = [
            f"{sym.name} ({sym.file_path.rsplit('/', 1)[-1]})"
            for sym, _ in _recently_dead[:4]
        ]
        _rd_str = ", ".join(_rd_names)
        if len(_recently_dead) > 4:
            _rd_str += f" +{len(_recently_dead) - 4} more"
        lines.append(f"Recently dead ({len(_recently_dead)}): {_rd_str}")

    # S106: Stale dead — dead symbols in files untouched for 90+ days.
    _stale_dead = [
        (sym, conf, _file_age(sym.file_path))
        for sym, conf in scored
        if conf >= 40
        and (_file_age(sym.file_path) or 0) >= 90
    ]
    if len(_stale_dead) >= 2:
        _ages = [age for _, _, age in _stale_dead if age]
        _avg_age = int(sum(_ages) / len(_ages)) if _ages else 0
        _sd_names = [f"{sym.name} ({age}d)" for sym, _, age in _stale_dead[:4] if age]
        _sd_str = ", ".join(_sd_names)
        if len(_stale_dead) > 4:
            _sd_str += f" +{len(_stale_dead) - 4} more"
        lines.append(f"Stale dead ({len(_stale_dead)}, avg {_avg_age}d): {_sd_str} — safe to delete")

    # Transitively dead: non-dead symbols whose ALL callers are already dead.
    _transitively_dead: list[Symbol] = []
    for _td_sym in graph.symbols.values():
        if _td_sym.id in dead_sym_ids:
            continue
        if _is_test_file(_td_sym.file_path):
            continue
        _td_callers = graph.callers_of(_td_sym.id)
        if not _td_callers:
            continue  # Already in find_dead_code() results or 0-caller symbol
        if all(c.id in dead_sym_ids for c in _td_callers):
            _transitively_dead.append(_td_sym)
    if len(_transitively_dead) >= 1:
        _trd_names = [
            f"{s.name} ({s.file_path.rsplit('/', 1)[-1]})"
            for s in _transitively_dead[:4]
        ]
        _trd_str = ", ".join(_trd_names)
        if len(_transitively_dead) > 4:
            _trd_str += f" +{len(_transitively_dead) - 4} more"
        lines.append(f"Transitively dead ({len(_transitively_dead)}): {_trd_str} — only called by dead code")

    # S69: Safe-to-delete tier — conf >= 75 symbols.
    _safe_delete = [(sym, conf) for sym, conf in scored if conf >= 75]
    if len(_safe_delete) >= 2:
        _sd_parts = [f"{sym.name} ({sym.file_path.rsplit('/', 1)[-1]}, conf:{conf})" for sym, conf in _safe_delete[:4]]
        _sd_str = ", ".join(_sd_parts)
        if len(_safe_delete) > 4:
            _sd_str += f" +{len(_safe_delete) - 4} more"
        lines.append(f"Safe to delete ({len(_safe_delete)}): {_sd_str}")

    return lines


def _render_dead_tier_block(
    graph: Tempo,
    tier: list[tuple[Symbol, int]],
    label: str,
    max_symbols: int,
    touched_cache: dict[str, int | None],
) -> tuple[list[str], int]:
    """Render one confidence tier (HIGH/MEDIUM/LOW). Returns (lines, total_line_count)."""
    from ..git import file_last_modified_days as _file_last_modified_days  # noqa: PLC0415

    def _format_age(days: int | None) -> str:
        if days is None:
            return ""
        if days >= 365:
            return " [age: 1y+]"
        if days >= 30:
            return f" [age: {days // 30}m]"
        return f" [age: {days}d]"

    def _last_touched(file_path: str) -> str:
        if file_path not in touched_cache:
            touched_cache[file_path] = _file_last_modified_days(graph.root, file_path)
        days = touched_cache[file_path]
        if days is None:
            return ""
        return f" — last touched: {days} days ago"

    def _sym_age(sym: Symbol) -> str:
        if sym.file_path not in touched_cache:
            touched_cache[sym.file_path] = _file_last_modified_days(graph.root, sym.file_path)
        return _format_age(touched_cache[sym.file_path])

    lines: list[str] = []
    total_line_count = 0
    shown = tier[:max_symbols]
    lines.append(f"{label}:")
    lines.append("")
    by_file: dict[str, list[tuple[Symbol, int]]] = {}
    for sym, conf in shown:
        by_file.setdefault(sym.file_path, []).append((sym, conf))
    # Sort files: most dead symbols first (most-contaminated first)
    sorted_files = sorted(by_file.items(), key=lambda x: -len(x[1]))
    for fp, file_syms in sorted_files:
        n = len(file_syms)
        sym_label = f"{n} dead symbol{'s' if n != 1 else ''}"
        _effort = _file_effort_badge(file_syms, graph)
        lines.append(f"  {fp} ({sym_label}){_last_touched(fp)}{_effort}:")
        by_line = sorted(file_syms, key=lambda x: x[0].line_start)
        shown_syms = by_line[:10]
        for sym, conf in shown_syms:
            lc = sym.line_count
            total_line_count += lc
            age = _sym_age(sym)
            # Superseded hint: if name has legacy/old/deprecated suffix, find active replacement.
            _sup_hint = ""
            _STALE_SUFFIXES = ("_old", "_legacy", "_v1", "_v2", "_deprecated", "_backup", "_bak", "_orig")
            _lower = sym.name.lower()
            for _suf in _STALE_SUFFIXES:
                if _lower.endswith(_suf):
                    _base = sym.name[:-(len(_suf))]
                    _replacement = next(
                        (s for s in graph.symbols.values()
                         if s.name.lower() == _base.lower()
                         and s.id != sym.id
                         and graph.callers_of(s.id)),
                        None
                    )
                    if _replacement:
                        _sup_hint = f" → possibly replaced by: {_replacement.name}"
                    break
            lines.append(f"    {sym.kind.value} {sym.qualified_name} (L{sym.line_start}-{sym.line_end}, {lc} lines) [confidence: {conf}]{age}{_sup_hint}")
        if len(by_line) > 10:
            lines.append(f"    ... and {len(by_line) - 10} more")
        lines.append("")
    return lines, total_line_count


def _dead_precompute_early_signals(graph: Tempo) -> tuple[list[str], list]:
    """Pre-compute S569 (dead typing files) and S605 (dead util fns) that fire even when dead=[].

    Returns (dead_typing_files569, dead_util605_pre).
    """
    # S569: Dead typing file — Python file that only contains typing imports (no indexed symbols)
    # and has no importers. Pre-computed here so it can fire even when `dead` is empty.
    dead_typing_files569 = [
        fp for fp, fi in graph.files.items()
        if not _is_test_file(fp)
        and not list(fi.symbols)
        and any("typing" in imp.lower() for imp in fi.imports)
        and not graph.importers_of(fp)
    ]

    # S605: Dead utility function — catches BOTH non-exported private utilities
    # AND exported dead utilities with utility prefix.
    _util_prefixes = ("get_", "make_", "create_", "build_", "generate_", "compute_", "fetch_")
    dead_util605_pre = [
        sym for sym in graph.symbols.values()
        if not _is_test_file(sym.file_path)
        and sym.kind.value in ("function", "method")
        and any(sym.name.startswith(p) for p in _util_prefixes)
        and not graph.callers_of(sym.id)
    ]

    return dead_typing_files569, dead_util605_pre


def _dead_score_and_tier(
    dead: list[Symbol],
    graph: Tempo,
) -> tuple[list[tuple[Symbol, int]], list[tuple[Symbol, int]], list[tuple[Symbol, int]], list[tuple[Symbol, int]]]:
    """Score dead symbols and split into high/medium/low confidence tiers.

    Returns (scored, high, medium, low).
    """
    scored = [(sym, _dead_code_confidence(sym, graph)) for sym in dead]
    scored.sort(key=lambda x: (-x[1], -x[0].line_count))
    high = [(s, c) for s, c in scored if c >= 70]
    medium = [(s, c) for s, c in scored if 40 <= c < 70]
    low = [(s, c) for s, c in scored if c < 40]
    return scored, high, medium, low


def render_dead_code(graph: Tempo, *, max_symbols: int = 50, max_tokens: int = 8000, include_low: bool = False) -> str:
    """Find exported symbols that appear to be unused (never referenced externally).

    include_low: include low-confidence (likely false positive) symbols. Off by default
        to reduce token output (~47% savings). Pass include_low=True to see all tiers.
    """
    dead = graph.find_dead_code()
    dead_typing_files, dead_util_fns = _dead_precompute_early_signals(graph)

    if not dead and not dead_typing_files and not dead_util_fns:
        return "No dead code detected — all exported symbols are referenced."

    scored, high, medium, low = _dead_score_and_tier(dead, graph)
    removable_header, dead_ratio_str = _render_dead_header_stats(scored, dead, graph)
    lines = [f"Potential dead code ({len(dead)} symbols){removable_header}{dead_ratio_str}:"]
    lines.extend(_render_dead_insights_a(graph, scored, high))

    _dead_sym_ids = {sym.id for sym, _ in scored}
    _touched_cache: dict[str, int | None] = {}
    # S40: pre-prime file age cache — one batch git call instead of N per-file calls.
    if graph.root:
        try:
            from ..git import prime_file_age_cache as _prime_dc  # noqa: PLC0415
            _prime_dc(graph.root)
        except Exception:
            pass
    lines.extend(_render_dead_insights_b(graph, scored, dead, _dead_sym_ids, _touched_cache))

    lines.append("")
    total_lines = 0
    tiers = [("HIGH CONFIDENCE (safe to remove)", high),
             ("MEDIUM CONFIDENCE (review before removing)", medium)]
    if include_low:
        tiers.append(("LOW CONFIDENCE (likely false positives)", low))

    for label, tier in tiers:
        if not tier:
            continue
        tier_block_lines, tier_lc = _render_dead_tier_block(graph, tier, label, max_symbols, _touched_cache)
        lines.extend(tier_block_lines)
        total_lines += tier_lc

    _signals_dead_core(graph, scored, dead, lines)
    _signals_dead_patterns_a(graph, scored, dead, lines)
    _signals_dead_patterns_b(graph, scored, dead, lines)
    _signals_dead_typed_a(graph, scored, dead, lines, dead_typing_files, dead_util_fns)
    _signals_dead_typed_b(graph, scored, dead, lines)
    lines.append(f"Total: {len(dead)} unused symbols (~{total_lines:,} lines shown)")
    if include_low:
        lines.append(f"  {len(high)} high, {len(medium)} medium, {len(low)} low confidence")
    else:
        lines.append(f"  {len(high)} high, {len(medium)} medium, {len(low)} low confidence (low hidden — pass include_low=True to show)")

    result = "\n".join(lines)
    if max_tokens and count_tokens(result) > max_tokens:
        truncated: list[str] = []
        token_count = 0
        for line in lines:
            lt = count_tokens(line)
            if token_count + lt > max_tokens - 50:
                truncated.append(f"\n... truncated ({len(dead)} total, use max_tokens to see more)")
                break
            truncated.append(line)
            token_count += lt
        return "\n".join(truncated)
    return result
