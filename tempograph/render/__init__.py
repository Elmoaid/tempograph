"""Facade: re-export all render_* functions from submodules.

Backward-compatible: any code doing ``from tempograph.render import X`` continues
to work without changes.
"""
from __future__ import annotations

from ._utils import count_tokens, _is_test_file, _MONOLITH_THRESHOLD  # noqa: F401
from .overview import (  # noqa: F401
    _find_entry_points,
    render_overview,
    _detect_noisy_dirs,
    render_map,
    render_symbols,
)
from .focused import (  # noqa: F401
    _extract_focus_files,
    _is_docs_branch_task,
    _suggest_alternatives,
    _cochange_orbit,
    _find_orbit_seeds,
    _collect_seeds,
    _sym_importance,
    _bfs_expand,
    _handle_overflow,
    _render_cochange_section,
    _render_all_callers_section,
    _render_hot_callers_section,
    _render_dependency_files_section,
    _render_recent_changes_section,
    _render_volatility_section,
    _render_cochange_orbit_section,
    _render_blast_risk_section,
    _render_related_files_section,
    _render_file_context_section,
    _render_monolith_section,
    _build_symbol_block_lines,
    render_focused,
    _monolith_neighborhood,
    _find_related_files,
)
from ..keywords import _extract_cl_keywords  # noqa: F401 (backward compat re-export)
from .lookup import _extract_name_from_question, render_lookup  # noqa: F401
from .blast import render_blast_radius  # noqa: F401
from .diff import render_diff_context  # noqa: F401
from .hotspots import (  # noqa: F401
    _classify_file,
    _file_blast_info,
    _file_blast_count,
    render_hotspots,
)
from .arch import render_dependencies, render_architecture  # noqa: F401
from .dead import (  # noqa: F401
    _dead_code_confidence,
    render_dead_code,
)
from .skills import render_skills  # noqa: F401
