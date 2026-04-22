# ipyflow Architecture

This document describes the architecture of ipyflow, a reactive Python kernel that tracks fine-grained dataflow relationships between variables in Jupyter notebooks.

## Overview

ipyflow uses **pyccolo** to instrument running Python code, capturing variable assignments, usages, and mutations. This information builds a **dataflow graph** that tracks dependencies between:
- **Cells** - notebook code cells
- **Statements** - individual AST statements within cells
- **Symbols** - variables and their values

The dataflow graph enables features like:
- Detecting stale/out-of-date variables
- Computing execution schedules (which cells need re-running)
- Program slicing (extracting code needed to reproduce a value)
- Reactive execution (automatically re-running dependent cells)

## Directory Structure

```
ipyflow/
├── core/ipyflow/           # Main Python package
│   ├── __init__.py         # Entry point, extension loaders
│   ├── flow.py             # NotebookFlow - central state manager
│   ├── comm_manager.py     # Frontend-kernel communication
│   ├── frontend.py         # Execution schedule computation
│   ├── config.py           # Settings and enums
│   ├── singletons.py       # Singleton pattern implementations
│   │
│   ├── data_model/         # Core data structures
│   │   ├── cell.py         # Cell class
│   │   ├── symbol.py       # Symbol class
│   │   ├── statement.py    # Statement class
│   │   ├── namespace.py    # Namespace (object attributes/items)
│   │   ├── scope.py        # Scope (variable symbol table)
│   │   └── timestamp.py    # Timestamp (cell_num, stmt_num)
│   │
│   ├── tracing/            # Pyccolo instrumentation
│   │   ├── ipyflow_tracer.py    # DataflowTracer - main tracer
│   │   ├── symbol_resolver.py   # Resolves values to Symbols
│   │   ├── flow_ast_rewriter.py # AST transformations
│   │   └── external_calls/      # Library-specific handlers
│   │
│   ├── analysis/           # Static analysis
│   │   ├── live_refs.py    # Liveness analysis
│   │   ├── symbol_ref.py   # Symbol reference resolution
│   │   └── resolved_symbols.py
│   │
│   ├── slicing/            # Program slicing
│   │   ├── mixin.py        # SliceableMixin for cells/statements
│   │   └── context.py      # Static vs dynamic slicing context
│   │
│   ├── kernel/             # IPykernel integration
│   │   └── kernel.py       # IPyflowKernel
│   │
│   ├── shell/              # IPython integration
│   │   └── interactiveshell.py  # IPyflowInteractiveShell
│   │
│   ├── api/                # Public Python API
│   │   ├── lift.py         # deps(), users(), code(), etc.
│   │   └── cells.py        # Cell reproduction functions
│   │
│   └── models.py           # Query interface (Cells, Symbols, etc.)
│
├── frontend/               # JupyterLab extension (TypeScript)
└── core/test/              # Test suite
```

## Core Concepts

### Timestamp

A `Timestamp` is a tuple `(cell_num, stmt_num)` that uniquely identifies an execution point:
- `cell_num`: The cell execution counter (increments each time any cell runs)
- `stmt_num`: The statement index within that cell execution

Timestamps enable precise tracking of when values were created/modified.

### Symbol

A `Symbol` represents a variable binding and tracks:
- **name**: The variable name (or subscript key)
- **obj**: Reference to the actual Python object
- **timestamp**: When it was last updated
- **parents**: Symbols this one depends on (dataflow edges)
- **children**: Symbols that depend on this one
- **containing_scope**: Where it's defined (global, function, namespace)

Symbols form the nodes of the dataflow graph.

### Namespace

A `Namespace` represents an object's attributes or items:
- Extends `Scope` to act as a symbol table for object members
- Tracks `obj_id` to identify the underlying object
- Created lazily when attributes/items are accessed
- Examples: `df.columns`, `my_dict["key"]`, `obj.attr`

### Scope

A `Scope` is a symbol table mapping names to Symbols:
- **Global scope**: Top-level notebook variables
- **Function scope**: Local variables within functions
- **Namespace scope**: Attributes/items of objects

### Cell

A `Cell` represents a notebook code cell and tracks:
- **cell_id**: Unique identifier from the notebook frontend
- **cell_ctr**: Execution counter when it ran
- **content**: The cell's source code
- **raw_parents/raw_children**: Edges to other cells (via Symbols)
- **position**: Order in the notebook (for in-order execution)

### Statement

A `Statement` represents a single AST statement and tracks:
- **stmt_node**: The AST node
- **timestamp**: When it executed
- **raw_parents/raw_children**: Edges to other statements (via Symbols)

Statements enable finer-grained slicing than cell-level.

## Key Components

### NotebookFlow (`flow.py`)

The central singleton that holds all execution state:

```python
class NotebookFlow:
    # Symbol storage
    namespaces: Dict[int, Namespace]  # obj_id -> Namespace
    aliases: Dict[int, Set[Symbol]]   # obj_id -> all Symbols for that object
    global_scope: Scope               # Top-level symbol table

    # Execution state
    active_cell_id: Optional[IdType]
    updated_symbols: Set[Symbol]
    updated_reactive_symbols: Set[Symbol]

    # Settings
    settings: DataflowSettings        # Immutable settings
    mut_settings: MutableDataflowSettings  # Per-session settings

    # Communication
    comm_manager: CommManager
```

Key methods:
- `add_data_dep()`: Records a dataflow dependency between timestamps
- `check_and_link_multiple_cells()`: Computes execution schedule
- `gc()`: Garbage collects unused symbols

### DataflowTracer (`tracing/ipyflow_tracer.py`)

Extends pyccolo's `BaseTracer` to instrument code execution:

```python
class DataflowTracer(SingletonBaseTracer):
    ast_rewriter_cls = DataflowAstRewriter

    # Uses @pyc.register_raw_handler decorators:
    # - pyc.call / pyc.return_ : Track function entry/exit
    # - Custom handlers for assignments, attribute access, subscripts
```

The tracer:
1. Rewrites AST to insert tracing calls
2. Captures variable assignments and creates/updates Symbols
3. Captures variable usages and records dataflow edges
4. Handles external library calls specially (pandas, numpy, etc.)

### CommManager (`comm_manager.py`)

Manages bidirectional communication with the JupyterLab frontend:

```python
class CommManager:
    def register_comm_target(kernel): ...
    def handle(request, comm): ...  # Dispatch to handlers

    # Handlers for each message type:
    def handle_compute_exec_schedule(request): ...
    def handle_notify_content_changed(request): ...
    def handle_change_active_cell(request): ...
    # etc.
```

Message flow:
```
Frontend                          Kernel
   |                                 |
   |-- compute_exec_schedule ------->|
   |                                 |-- check_and_link_multiple_cells()
   |<-- FrontendCheckerResult -------|
   |                                 |
   |-- notify_content_changed ------>|
   |                                 |-- recompute AST for cells
   |<-- updated schedule ------------|
```

### FrontendCheckerResult (`frontend.py`)

Computes and formats execution schedule information:

```python
class FrontendCheckerResult:
    ready_cells: Set[IdType]      # Can be executed now
    waiting_cells: Dict[IdType, List[str]]  # Waiting + why
    stale_cells: Dict[IdType, List[str]]    # Stale + which symbols
    fresh_cells: Set[IdType]      # Up to date
```

## Dataflow Tracking

### How Dependencies Are Captured

1. **AST Rewriting**: `DataflowAstRewriter` transforms code to insert tracing hooks

2. **Execution Tracing**: When code runs, pyccolo handlers fire:
   - On assignment: Create/update Symbol, record dependencies
   - On load: Record usage, add dataflow edge
   - On call/return: Track scope changes

3. **Edge Creation**: `flow.add_data_dep(child_ts, parent_ts, symbol)` creates edges between:
   - Statements (fine-grained)
   - Cells (coarse-grained)

### Static vs Dynamic Slicing

ipyflow supports two slicing modes:
- **Dynamic slicing**: Edges from actual runtime execution
- **Static slicing**: Edges from code analysis (liveness)

The `SlicingContext` context manager controls which mode is active.

## Configuration

### Execution Modes

```python
class ExecutionMode(Enum):
    LAZY = "lazy"        # Manual execution only
    REACTIVE = "reactive"  # Auto-execute dependent cells
```

### Execution Schedules

```python
class ExecutionSchedule(Enum):
    DAG_BASED = "dag_based"         # Based on runtime dependencies
    LIVENESS_BASED = "liveness"     # Based on static liveness analysis
    HYBRID_DAG_LIVENESS_BASED = "hybrid"  # Combined approach
```

### Highlights

```python
class Highlights(Enum):
    NONE = "none"
    EXECUTED = "executed"    # Show what ran
    REACTIVE = "reactive"    # Show reactive updates
    ALL = "all"
```

## Frontend Communication Protocol

### Message Types (Frontend → Kernel)

| Type | Purpose |
|------|---------|
| `change_active_cell` | Set currently active cell |
| `compute_exec_schedule` | Request execution schedule |
| `notify_content_changed` | Cell content was edited |
| `reactivity_cleanup` | Clear reactive state |
| `refresh_symbols` | Force symbol refresh |
| `upsert_symbol` | Create/update a symbol |
| `get_code` | Get code slice for symbol |
| `bump_timestamp` | Update tracked timestamp |

### Response Format

```json
{
  "type": "compute_exec_schedule",
  "success": true,
  "ready_cells": ["cell-id-1", "cell-id-2"],
  "waiting_cells": {"cell-id-3": ["x", "y"]},
  "stale_cells": {"cell-id-4": ["z"]},
  "exec_mode": "lazy",
  "highlights": "executed"
}
```

## Key Data Flows

### Cell Execution

```
1. User executes cell
2. IPyflowKernel.do_execute() called
3. Cell object created/updated
4. DataflowTracer instruments code
5. Code executes with tracing
6. Symbols created/updated for assignments
7. Dataflow edges recorded for usages
8. FrontendCheckerResult computed
9. Response sent to frontend
```

### Liveness Check

```
1. Frontend sends compute_exec_schedule
2. For each cell:
   a. Parse cell AST
   b. Compute live/dead symbol refs
   c. Resolve to actual Symbols
   d. Check if any dependencies are stale
3. Categorize cells: ready/waiting/stale/fresh
4. Return FrontendCheckerResult
```

## Extension Points

### External Call Handlers

Custom handlers for library functions that affect dataflow:

```python
# In tracing/external_calls/
class PandasHandler(ExternalCallHandler):
    def handle_DataFrame_merge(self, ...): ...
    def handle_DataFrame_groupby(self, ...): ...
```

### Annotations Compiler

Compile type annotations for better dataflow tracking:

```python
# In annotations/compiler.py
compile_handlers_for_already_imported_modules(...)
```

## Development

- **Setup**: See [INSTALL.md](INSTALL.md)
- **Testing, linting, type checking**: See [CLAUDE.md](CLAUDE.md)

## Common Patterns

### Accessing Singletons

```python
from ipyflow.singletons import flow, shell, tracer

flow_ = flow()  # NotebookFlow instance
shell_ = shell()  # IPyflowInteractiveShell instance
tracer_ = tracer()  # DataflowTracer instance
```

### Accessing Cells/Symbols/Statements

```python
from ipyflow.models import cells, symbols, statements

# Get current cell
cell = cells().current_cell()

# Get cell by ID or counter
cell = cells().from_id(cell_id)
cell = cells().at_counter(ctr)

# Iterate cells
for cell in cells().current_cells_for_each_id():
    ...
```

### Working with Timestamps

```python
from ipyflow.data_model.timestamp import Timestamp

ts = Timestamp.current()  # Current execution point
ts = Timestamp(cell_num=5, stmt_num=0)  # Specific point
ts.is_initialized  # Check if valid
```

## Glossary

| Term | Definition |
|------|------------|
| **Alias** | Multiple Symbols pointing to the same object |
| **Cascading reactive** | Propagates reactivity to downstream cells |
| **Cell counter** | Monotonically increasing execution counter |
| **Liveness** | Static analysis of which symbols are used |
| **Namespace** | Symbol table for object attributes/items |
| **Scope** | Symbol table for a lexical scope |
| **Stale** | A symbol whose dependencies have been updated |
| **Timestamp** | (cell_num, stmt_num) execution point |
| **Waiting** | A cell with stale dependencies |
