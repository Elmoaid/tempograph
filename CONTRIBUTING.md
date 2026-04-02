# Contributing to Tempograph

Thanks for wanting to contribute! Here's how to get going.

## Setup

```bash
git clone https://github.com/Elmoaid/tempograph.git
cd tempograph
pip install -e ".[dev]"
pytest  # make sure everything passes
```

Python 3.11+ required.

## Making changes

1. Fork the repo and create a branch from `main`
2. Make your changes
3. Run `pytest` and make sure all tests pass (there are 4300+, they should all be green)
4. Open a PR with a clear description of what you changed and why

## What we're looking for

- Bug fixes with a test that reproduces the issue
- Performance improvements with before/after numbers
- New language handlers in `parser.py` (follow the existing `_handle_*` pattern)
- MCP tool improvements
- Documentation fixes

## Code style

- Keep it simple. Small functions, clear names.
- Tests go in `tests/`. Match the existing patterns.
- Don't add dependencies unless absolutely necessary.

## Tests

```bash
pytest                    # full suite
pytest tests/test_foo.py  # single file
pytest -x                 # stop on first failure
```

The test suite is the source of truth. If tests pass, you're probably good.

## Reporting bugs

Use the [bug report template](https://github.com/Elmoaid/tempograph/issues/new?template=bug_report.yml). Include your tempograph version, Python version, and OS.

## License

By contributing, you agree that your contributions will be licensed under the same [AGPL-3.0](LICENSE) as the rest of the project.
