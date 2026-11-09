# SBOM and License Tracking

This document tracks the software used by this repository, including versions,
licenses, and version-control references to support legal and security review.

## Scope

* Runtime dependencies used by `find-overlay.py`
* Development/test tooling used in this repository
* Build-system dependency declared in `pyproject.toml`

## Version-Control Snapshot

* Repository: `mgifford/Find-Overlays`
* Branch at last update: record output of `git rev-parse --abbrev-ref HEAD`
* Commit at last update: record output of `git rev-parse HEAD`
* Lock source: `uv.lock`
* Requirements export source: `requirements.txt`

## Software Inventory

| Component | Role | Version | License | Version Source |
|---|---|---:|---|---|
| find-overlays | Project package | 0.1.0 | AGPL-3.0 (see `LICENSE`) | `pyproject.toml` |
| Python | Runtime | 3.12 (project requires >=3.10) | PSF-2.0 | `.python-version` / `pyproject.toml` |
| requests | Runtime dependency | 2.34.2 | Apache-2.0 | `uv.lock` / metadata |
| urllib3 | Runtime transitive dependency | 2.8.0 | MIT | `uv.lock` / metadata |
| certifi | Runtime transitive dependency | 2026.10.23 | MPL-2.0 | `uv.lock` / metadata |
| charset-normalizer | Runtime transitive dependency | 3.5.2 | MIT | `uv.lock` / metadata |
| idna | Runtime transitive dependency | 3.20 | BSD-3-Clause | `uv.lock` / metadata |
| flake8 | Dev dependency | 7.5.0 (3.11+); 7.4.1 (3.10) | MIT | `uv.lock` / metadata |
| pyflakes | Dev transitive dependency | 4.1.0 (3.11+); 4.0.3 (3.10) | MIT | `uv.lock` / metadata |
| pycodestyle | Dev transitive dependency | 2.15.0 | MIT | `uv.lock` / metadata |
| mccabe | Dev transitive dependency | 0.7.0 | Expat | `uv.lock` / metadata |
| pytest | Dev dependency | 9.2.0 | MIT | `uv.lock` / metadata |
| pytest-cov | Dev dependency | 7.1.0 | MIT | `uv.lock` / metadata |
| coverage | Dev transitive dependency | 7.17.2 (3.11+); 7.16.2 (3.10) | Apache-2.0 | `uv.lock` / metadata |
| packaging | Dev transitive dependency | 26.3 | Apache-2.0 OR BSD-2-Clause | `uv.lock` / metadata |
| pluggy | Dev transitive dependency | 1.7.0 | MIT | `uv.lock` / metadata |
| iniconfig | Dev transitive dependency | 2.3.1 | MIT | `uv.lock` / metadata |
| pygments | Dev transitive dependency | 2.21.0 | BSD-2-Clause | `uv.lock` / metadata |
| colorama | Dev transitive dependency (Windows) | 0.4.6 | BSD-3-Clause | `uv.lock` / metadata |
| exceptiongroup | Dev transitive dependency (3.10) | 1.3.1 | MIT | `uv.lock` / metadata |
| tomli | Dev transitive dependency (3.10) | 2.5.0 | MIT | `uv.lock` / metadata |
| typing-extensions | Dev transitive dependency (3.10) | 4.16.0 | PSF-2.0 | `uv.lock` / metadata |
| setuptools | Build system | >=61.0 | MIT | `pyproject.toml` (`build-system`) |

## Maintenance Process

1. After dependency updates, run `uv sync`.
2. Re-generate runtime lock/export data as needed:
   * `uv lock`
   * `uv export --no-dev --no-emit-project --no-hashes --format requirements-txt -o requirements.txt`
3. Audit the resolved dependency set:
   * `uv audit`
4. Update this file's version-control snapshot (`branch`, `commit`) and changed
   versions/licenses.
5. Re-run validations:
   * `uv run flake8 find-overlay.py`
   * `uv run pytest`

`requirements.txt` is a generated export. Do not edit it by hand; regenerate it
from `uv.lock` with the command above.

## Notes

* License fields come from package metadata when available and should be
  re-verified during major dependency updates.
* This file is a lightweight operational SBOM for project governance; if a
  standardized SBOM format is required later (for example SPDX or CycloneDX),
  this inventory can be used as the source reference.
