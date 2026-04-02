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

Runtime dependencies are listed in `requirements.txt`.  Install them with:

```bash
pip install -r requirements.txt
```
