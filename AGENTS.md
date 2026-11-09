# Agent Guidance for Find-Overlays

This file tells AI coding agents (Copilot, Claude, Codex, GPT, and similar)
how to work in this repository. Human contributors may find it useful too.

## Read before changing anything

1. **[ACCESSIBILITY.md](ACCESSIBILITY.md)** — the project's accessibility
   contract. It is binding for console output, CSV output, and documentation.
2. **[PYTHON_GUIDANCE.md](PYTHON_GUIDANCE.md)** — coding standards.
3. **[README.md](README.md)** — usage, output formats, and limitations.
4. **[SBOM.md](SBOM.md)** — dependency and licence inventory.

If this file and `ACCESSIBILITY.md` disagree about output or documentation,
`ACCESSIBILITY.md` wins.

## What this project is

`find-overlay.py` scans a list of URLs (a local CSV/XML/text file or a remote
sitemap/CSV) and reports which known accessibility-overlay products are
referenced in the returned HTML. It writes a timestamped CSV, can write JSON,
and feeds a static results site published with GitHub Pages from `site/`.

It is **not** an accessibility test. Never describe a scan as an audit,
certification, conformance claim, or proof that a site is accessible or
inaccessible. Results are indicators, and false negatives are expected.

There is no runtime AI and no telemetry. The public site is static HTML/CSS/JS
with no third-party scripts.

## Setup and commands

Dependencies are managed with `uv`:

```bash
uv sync
uv run find-overlay.py samples/sample-domains.csv --limit 5 --no-csv
uv run pytest
uv run flake8 find-overlay.py scripts tests
uv run bandit -c pyproject.toml -r find-overlay.py scripts
uv audit
```

- Tests must stay offline. Mock `requests.get`; never add live-network tests.
- Do not commit `.venv/`, `__pycache__/`, `scan.json`, or `*-overlays-*.csv`
  scan output.
- `site/index.html` and `site/data/` are generated. Change
  `site/template.html`, `site/styles.css`, `site/app.js`, or
  `scripts/build_site.py` instead, then rebuild.
- If `uv` cannot write its cache, set `UV_CACHE_DIR` to a writable directory
  rather than changing project files.

## Python standards

The project requires Python 3.10 or newer; `.python-version` pins 3.12 for
development.

Follow **[PYTHON_GUIDANCE.md](PYTHON_GUIDANCE.md)**. Key points:

- Module, class, and function docstrings are required, including `main` and
  private helpers. Use Google style.
- Type annotations are required on every function signature. Prefer precise
  types (`dict[str, object]` or a `TypedDict`) over bare `dict`.
- Target 50 lines or fewer per function, excluding the docstring. `main()` is
  currently well over this limit; extract helpers when you touch it.
- Keep lines at or under 88 characters (enforced by `.flake8`).
- Catch specific exceptions. Never use a bare `except:` and avoid `except
  Exception` where a narrower type is available.
- Run `uv run flake8 find-overlay.py scripts tests` and `uv run pytest`
  before finishing.

## Accessibility requirements

The full contract is in [ACCESSIBILITY.md](ACCESSIBILITY.md). In short:

- Console output stays plain text: no ANSI colour, spinners, progress bars, or
  `\r` line rewriting. Status and overlay information must be words, never
  colour alone.
- Keep the `|` field separators in result lines. Column padding is cosmetic;
  meaning must not depend on alignment.
- Keep the interactive prompt optional. Passing a source argument must remain
  fully non-interactive, with no timeout.
- Keep CSV output UTF-8 with a header row and stable column names:
  `url,status,detected_overlay,overlay_versions,other_widgets`, or
  `url,detected_overlay` with `--simple`. Document any schema change in
  `README.md` and `ACCESSIBILITY.md`.
- Error messages should name the source and the next step. Do not print raw
  HTML, stack traces, or untrusted content as instructions.
- Documentation uses plain language, one H1, no skipped heading levels, and
  descriptive link text. Add `alt` text to any future image.
- Never claim screen-reader or assistive-technology testing that was not
  performed. Record manual results in `ACCESSIBILITY.md`.
- If you add an HTML report, use a real `<table>` with `<caption>` and
  `<th scope>`, run axe-core, and do a keyboard and screen-reader pass.

## URL and data-handling pitfalls

These are known sharp edges in the current code. Do not copy the pattern into
new code, and fix it when a change touches the area.

- `value.startswith("http")` is **not** a scheme check. Hosts such as
  `httpbin.org`, and schemes such as `ftp://`, are misclassified. Use
  `urllib.parse.urlsplit` and check `.scheme`.
- Lower-casing a whole URL (`get_full_url`) can break case-sensitive paths and
  query strings. Lower-case only the scheme and host.
- `list(set(...))` destroys input order and makes `--limit` select an
  arbitrary subset. Use `dict.fromkeys(...)` to deduplicate while preserving
  order.
- `timeout=10` on `requests.get` is an inactivity timeout, not a total
  deadline. Do not claim a scan is time-bounded.
- Remote CSV/XML is untrusted. Bound response size, keep TLS verification on,
  do not resolve XML entities, and prefer `defusedxml` if XML handling grows.
- The public-host guard checks the initial URL only; `requests` still follows
  redirects. Do not treat it as a complete SSRF defence for untrusted input.
- Only scan domains you are authorised to test. Respect robots.txt, site terms,
  and rate limits; do not add concurrency without a documented reason.
- Identify the tool honestly in the User-Agent when changing request headers,
  rather than impersonating a browser version.

## Site and issue automation

- `data/sites.csv` is the public scan queue. Only add public http(s) URLs.
- Scan requests arrive through `.github/ISSUE_TEMPLATE/scan-request.yml` and
  are queued only after a maintainer applies the `approved` label. Never
  auto-approve, and never scan private, internal, or non-public hosts.
- `.github/workflows/queue-scan-request.yml` validates submissions, appends to
  the queue, commits, and dispatches the scan workflow.
- `.github/workflows/scan-and-publish.yml` scans, writes history, commits
  `site/`, and deploys GitHub Pages.
- `data/providers.json` holds sourced provider context. Do not add an
  allegation without a credible source; label context as third-party and not
  legal advice.
- Keep the site usable without JavaScript. The findings table is rendered at
  build time; JavaScript only filters it.
- Preserve table captions, `scope` attributes, skip link, visible focus, and
  text equivalents for status and version information.

## Security and supply-chain practices

- Treat all scanned input and remote responses as untrusted.
- Never commit secrets, tokens, credentials, private domain lists, or scan
  results.
- Keep dependencies pinned in `uv.lock`; update intentionally and review the
  diff.
- Do not disable TLS verification, remove timeouts, or add arbitrary code
  execution to parse input.
- Update [SBOM.md](SBOM.md) when dependencies change, and keep its version
  numbers in sync with `uv.lock` and `requirements.txt`.
- Run Bandit and the dependency audit locally before committing:
  - `uv run bandit -c pyproject.toml -r find-overlay.py scripts`
  - `uv audit`
- Do not add `# nosec` without a comment explaining the false positive. Prefer
  fixing the finding; for example, untrusted XML is parsed with `defusedxml`,
  not `xml.etree.ElementTree`.
- Do not disable or weaken the scanners in
  [`.github/workflows/security.yml`](.github/workflows/security.yml) without a
  documented reason.
- Run linting and tests before committing:
  - `uv run flake8 find-overlay.py scripts tests`
  - `uv run pytest`

## Review checklist

- [ ] Public functions have docstrings and type annotations.
- [ ] No function exceeds ~50 lines (docstring excluded) or 88 characters per
      line.
- [ ] `uv run flake8 find-overlay.py scripts tests` and `uv run pytest` pass.
- [ ] `uv run bandit -c pyproject.toml -r find-overlay.py scripts` reports no issues.
- [ ] `uv audit` reports no known vulnerabilities.
- [ ] Console output remains plain text and colour-independent.
- [ ] CSV column names and encoding are unchanged, or the change is documented.
- [ ] New edge cases have tests (scheme parsing, input order, empty source,
      write failures).
- [ ] No secrets, private data, or generated scan files are included.
- [ ] `ACCESSIBILITY.md` and `SBOM.md` are updated when scope or dependencies
      change.
- [ ] Generated `site/index.html` and `site/data/` were rebuilt from the
      template and scan data.
