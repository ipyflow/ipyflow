# ipyflow

## Running Tests

Use `./scripts/runtests.sh` which sets up the proper environment (initializes `IPyflowInteractiveShell` before pytest).

Always use the most specific filter applicable to changes under test, e.g. `./scripts/runtests.sh magics` when changing `line_magics.py`.

```bash
# Run all tests
./scripts/runtests.sh

# Run tests matching a pattern (matches test/*{pattern}*.py)
# IMPORTANT: Don't include "test_" prefix - just use the unique part
./scripts/runtests.sh reactivity      # runs test_reactivity.py
./scripts/runtests.sh symbols         # runs test_*symbols*.py (3 files, 17 tests)
./scripts/runtests.sh frontend        # runs test_frontend_checker.py
./scripts/runtests.sh cell_dag        # runs test_cell_dag.py

# WARNING: If the pattern matches nothing, ALL tests run!
./scripts/runtests.sh test_symbols    # WRONG: matches nothing, runs all 371 tests
./scripts/runtests.sh symbols         # RIGHT: matches 3 files

# Pass pytest flags after the pattern
./scripts/runtests.sh reactivity -x   # stop on first failure
./scripts/runtests.sh reactivity -v   # verbose output
./scripts/runtests.sh reactivity -k "test_name"  # filter by test name

# Run a specific test file (use full path from core/)
./scripts/runtests.sh test/test_cell_dag.py

# Run with coverage
./scripts/runtests.sh --coverage
```

## Type Checking

```bash
make typecheck    # runs mypy on core/ipyflow
```

## Linting and Formatting

```bash
# Check formatting (doesn't modify files)
make blackcheck   # isort --check-only + black --check

# Check for lint errors
make lint         # ruff check ./core

# Auto-format code
make black        # isort + black
```

## Full Validation

```bash
# Run everything: eslint + blackcheck + lint + typecheck + tests
make check

# Run tests only (no type checking) - used in CI
make check_no_typing

# Run tests with coverage report
make coverage
```
