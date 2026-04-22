# ipyflow Development Setup

Create and activate a virtual environment (e.g., `ipyflow-dev`), then install dependencies (run from repo root):

```bash
# Install runtime and dev dependencies
make devdeps

# Build the frontend extension
make build

# Link the extension for development
make extlink

# Install the ipyflow kernel
make kernel

# Or do all of the above in one command:
make dev
```

## Makefile Targets for Setup

| Target | Description |
|--------|-------------|
| `make dev` | Full dev setup: devdeps + build + extlink + kernel |
| `make devdeps` | Install package in editable mode with dev dependencies |
| `make build` | Clean and build the frontend extension |
| `make extlink` | Symlink extension for development |
| `make kernel` | Install the ipyflow kernel |
| `make clean` | Remove build artifacts |
