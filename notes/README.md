# Tempograph Field Notes — Agent Protocol

This folder collects usage observations from AI agents using tempograph across Elmo's projects. The goal: build a feedback loop that improves tempograph over time without burning tokens.

## When to Write a Note

Write a note when you:
- Used tempograph and it **helped** find something non-obvious
- Used tempograph and it **failed** or was unhelpful for a specific task
- Discovered a **pattern** in a codebase that tempograph should detect but doesn't
- Have a concrete **feature request** based on real usage
- Ran a **benchmark or large scan** with measurable results

**Don't** write a note for:
- Routine `overview` calls that just confirmed what you already knew
- Sessions where tempograph wasn't relevant
- Observations already covered in existing notes (grep first)

## File Format

```
notes/YYYY-MM-DD_<short-slug>.md
```

Examples: `2026-03-12_texty-debug-scan.md`, `2026-03-15_fastapi-refactor.md`

## Entry Structure

Keep entries **under 50 lines**. Use this skeleton:

```markdown
# <Title> — YYYY-MM-DD

## Context
<1-2 lines: what project, what task, which tempograph modes used>

## Findings
<What tempograph revealed or missed. Be specific — include mode, query, result.>

## Recommendations
<Concrete feature requests or improvements. Numbered list.>

## Token Cost
<Optional. Note agent token usage if you tracked it.>
```

## Before Writing

1. `grep -rl "<your-keyword>" /Users/elmoaidali/Desktop/tempograph/notes/` — check if the observation already exists
2. If it does, only add if you have **new data** (e.g. same issue in a different codebase confirms the pattern)
3. Append to existing note if it's the same project/topic on the same day

## Token Budget Guidelines

The notes loop should cost **<1% of session tokens**. Rules of thumb:
- `overview` at session start: ~500 output tokens. Always worth it.
- `focus`/`blast` during work: ~200-500 tokens each. Use when changing shared code.
- `dead`/`hotspots`: ~1000-2000 tokens. Use once per scan/refactor session, not per edit.
- Writing a note: ~300-500 tokens. Skip if nothing new to report.
- Reading existing notes: grep first (~50 tokens), only read matching files.

## What Makes a Good Note

**Good**: "tempograph `dead` found 812 unused symbols in texty but can't rank by confidence. Exported-but-unused-internally needs separate handling from truly-dead private functions. Feature request: add `--confidence` flag."

**Bad**: "Used tempograph overview on the project. It showed files and symbols. Worked as expected."

## Aggregation

Over time, recurring themes in these notes should inform tempograph's roadmap. Look for:
- Feature requests mentioned 3+ times across different sessions
- Modes that are consistently unhelpful for certain codebase shapes
- Token cost patterns that suggest optimization opportunities
