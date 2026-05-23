# Agent Guidance for Find-Overlays

This file provides guidance for AI coding agents (e.g. GitHub Copilot, Claude,
GPT) working in this repository.

## Python

Follow the project's Python coding standards when writing or reviewing any
`.py` file:

👉 **[PYTHON_GUIDANCE.md](PYTHON_GUIDANCE.md)**

Key points:

* All functions (including `main` and private helpers) must have docstrings.
* Use type annotations on every function signature.
* Keep functions at or under ~50 lines (excluding docstring).
* Run `flake8 find-overlay.py` (or `ruff check find-overlay.py`) before
  committing and fix all warnings.
* Catch specific exceptions — never use a bare `except:`.

## Project Overview

`find-overlay.py` scans a list of domains (supplied as a local CSV/XML file or
a remote URL) and reports which accessibility-overlay products are loaded on
each site.  Results are written to a timestamped CSV file.

## Dependencies

Dependencies are managed with `uv`. Install and sync them with:

```bash
uv sync
```

## Security and Supply Chain Best Practices

This project is public, but contributors should still follow basic security and
legal-risk hygiene:

* Treat all scanned input data and remote responses as untrusted.
* Never commit secrets, tokens, credentials, or private data.
* Keep dependencies pinned via `uv.lock`; update intentionally and review
  changes before merging.
* Run linting and tests before committing:
  * `uv run flake8 find-overlay.py`
  * `uv run pytest`
* Maintain and review [`SBOM.md`](SBOM.md) so software versions and licenses are
  documented for supply-chain and compliance tracking.
