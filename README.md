# Find Overlays

## What this tool does

**Find-Overlays** is a small, open-source scanning tool that detects the use of so-called *accessibility overlays* and *widgets* across a list of websites.

Given a set of domains or URLs, the tool scans pages and reports whether known accessibility overlay technologies are present. This includes commercial widgets that inject JavaScript, toolbars, or UI layers claiming to “fix” accessibility automatically.

The output is designed to help organizations:

- Understand **where overlays are deployed**
- Identify **which overlay vendors are in use**
- Track overlay usage **across large portfolios of sites**
- Support **policy, procurement, and remediation decisions**

This tool **does not test accessibility** and **does not score compliance**. It answers a narrower, more actionable question:

> Are accessibility overlays being used on our sites, and where?

## Public results site

Results from the maintained scan list in `data/sites.csv` are published at
<https://mgifford.github.io/Find-Overlays/>. The site shows current findings,
the overlay versions it can see, and a change log with the date of each change.

The same site also publishes one-time [requested scans](site/reports/index.html).
A request does not add a site to the recurring monitoring list.

## Request an overlay scan

Anyone with a GitHub account can request an automated scan of publicly
available websites. No maintainer approval or label is required.

1. Open the [scan request form](https://github.com/mgifford/Find-Overlays/issues/new?template=scan-request.yml).
2. Enter up to 100 websites, one per line. Bare domains such as
   `example.gov` and full URLs are both accepted.
3. Submit the issue. Its title must begin with `SCAN:`.
4. GitHub Actions validates the request and scans the accepted sites.
5. Results are posted as a comment and published under
   [`site/reports/`](site/reports/).
6. Reopen the issue to request a fresh scan.

Requests and results are public. The scanner makes read-only HTTP requests to
public sites. It does not execute JavaScript, crawl linked pages, or perform a
WCAG audit.

---

## Why this exists

Accessibility overlays remain controversial for well-documented reasons.

Across the accessibility community, including people with disabilities, practitioners, standards bodies, and legal experts, there is broad agreement on several points:

- Overlays do **not** make inaccessible sites accessible
- Automated fixes cannot replace semantic HTML, proper interaction design, or testing with assistive technologies
- Overlay usage has been associated with **increased legal risk**, not reduced risk

Despite this, overlays continue to be adopted inside organizations because they:

- Appear to offer a fast fix
- Are marketed as compliance shortcuts
- Are sometimes added by third parties without central oversight

Find-Overlays exists to make overlay usage **visible**, especially in environments where:

- Many teams manage many sites
- Procurement and accessibility governance are decentralized
- Leadership needs factual data before setting policy

You can find more about [concerns with Accessibility Overlays](https://overlayfactsheet.com/) here and from the [2024 Web Almanac](https://almanac.httparchive.org/en/2024/accessibility#user-personalization-widgets-and-overlay-remediation).

---

## Why an organization might want to use this

### 1. Inventory and governance

Most large organizations cannot answer a basic question:

> Are accessibility overlays deployed anywhere in our web estate?

This tool provides a repeatable way to inventory overlay usage so that accessibility, legal, and digital teams can share a factual baseline.

### 2. Risk management

Many overlay vendors claim legal protection or compliance benefits. Those claims are not supported by W3C standards, and overlays have repeatedly appeared in demand letters and lawsuits.

Knowing where overlays are deployed allows organizations to:

- Assess legal exposure
- Align with internal accessibility standards
- Remove or avoid tools that conflict with policy

### 3. Accessibility maturity

Organizations working toward higher accessibility maturity often discover overlays late, after investing in training, audits, and remediation.

Finding overlays early helps ensure that:

- Teams focus on **structural accessibility fixes**
- Budgets are not diverted toward ineffective tooling
- Accessibility work aligns with WCAG and WAI guidance

### 4. Procurement and vendor oversight

Overlays are sometimes bundled into third-party products, CMS themes, or marketing tools.

This tool helps procurement and platform teams verify:

- Whether vendors are introducing overlays without disclosure
- Whether accessibility claims align with actual implementation

---

## How the code works (high level)

The scanner works by:

1. Fetching pages from a provided list of URLs or domains
2. Inspecting the HTML returned for each page (the tool does not run JavaScript
   or fetch every subresource)
3. Matching detected scripts, patterns, and domains against a maintained list of known overlay technologies
4. Extracting overlay versions from script URLs where the page exposes them
5. Reporting results as CSV or JSON for review, follow-up, and the public site

The detection approach is intentionally conservative. The goal is **signal**, not speculation.

---

## Installation and usage

> **Note:**
> The installation and usage instructions below use `uv` for dependency and environment management.


## Requirements
- Python 3.10+ (3.12 is the tested development version)
- Works on macOS/Linux/Windows. For macOS/Linux, use a virtual environment.

## Setup
Install and sync dependencies with `uv`.

```bash
# From the project root
uv sync
```

## Usage
You can pass a local file path (`.csv`/`.xml`/plain list) or a remote URL to a CSV/Sitemap.

```bash
uv run find-overlay.py <source>

# Examples
uv run find-overlay.py current-full.csv
uv run find-overlay.py https://example.com/sitemap.xml

# Simple two‑column output (url, detected_overlay)
uv run find-overlay.py current-full.csv --simple

# Stats only, no CSV generated
uv run find-overlay.py current-full.csv --no-csv

# Custom output filename
uv run find-overlay.py current-full.csv --output my-report.csv

# Full JSON output, including overlay versions and evidence URLs
uv run find-overlay.py current-full.csv --json scan.json --no-csv
```

### Sample Data
A small sample CSV is included:

```text
samples/sample-domains.csv
```

Run against the sample:

```bash
uv run find-overlay.py samples/sample-domains.csv
uv run find-overlay.py samples/sample-domains.csv --limit 100
```

### Arguments
- `source`: Required. Path to local file (`.csv`, `.xml`, or text) or a URL.
- `--output`: Custom output filename. Defaults to `<source>-overlays-YYYY-MM-DD.csv`.
- `--simple`: Output only `url` and `detected_overlay` columns.
- `--json PATH`: Write full results, including overlay versions and evidence
  URLs, as JSON.
- `--no-csv`: Do not write a CSV file.
- `--limit <N>`: Limit the number of unique URLs scanned to `N` (applied after
  deduplication). Useful for sampling large lists.
- `--time-budget <SECONDS>`: Stop starting new scans after a total budget;
  remaining URLs are recorded as skipped.
- `--delay <SECONDS>`: Pause between requests (the issue workflow uses 0.25).

## Input Formats
The command-line tool accepts:
- **Structured CSV**: a `url`, `website`, `site`, `domain`, or `Domain name`
  column. It adds `https://` when a scheme is missing and removes duplicates.
- **XML sitemaps**: `<loc>` values are read with a hardened XML parser.
- **Simple list**: one URL or domain per line, or comma/space-separated text.

Issue requests are limited to 100 distinct addresses. The command-line tool can
scan larger lists. Unicode hostnames are converted to IDNA (punycode); malformed
hostnames, credentials, unsupported schemes, and non-default ports are rejected.

## Output
When CSV generation is enabled, the default columns are:
- `url`: Scanned URL
- `status`: HTTP status code (e.g., 200, 404), or a short failure label such as
  `Timeout`, `DNS Error`, `Blocked`, or `Blocked Redirect`
- `detected_overlay`: Name(s) of overlay(s) found; `None Found` only when the
  HTML was inspected; `Not determined` when the scan failed or returned a
  non-2xx response
- `overlay_versions`: Best-effort version(s) read from asset URLs, or `unknown`
- `other_widgets`: Detected common widgets (e.g., Intercom, Zendesk)

The console also prints a summary with status code counts, overlay counts, and the percentage of domains with overlays.

Issue-report CSV files use
`url,status,outcome,detected_overlay,overlay_versions,other_widgets,error`.
They include rejected entries and distinguish a failed scan from a clean one.

## Tips
- Large sources: prefer sitemaps or deduplicated CSVs to reduce scan time.
- Network errors/timeouts are counted and shown in the final stats.

### Rate Limiting, Timeouts, and Bounds

- The scanner uses a 5-second connection timeout and a 10-second read timeout.
  These are inactivity limits, not a total scan deadline.
- Responses are capped at 10 MB. At most five redirects are followed, and each
  redirect destination is validated before it is requested.
- Requests are sequential. The issue workflow adds a short delay between
  requests and a 20-minute scan budget; remaining sites are marked as skipped.
- For very large lists, split the input into smaller batches. Do not add
  concurrency without a documented reason.

## What this tool intentionally does *not* do

- It does **not** claim a site is accessible or inaccessible
- It does **not** recommend or endorse overlay vendors
- It does **not** perform automated WCAG audits
- It does **not** add issue-requested sites to the recurring monitoring list
- It does **not** track users or collect analytics

This tool is about **visibility and accountability**, not automated compliance.

---

## Typical use cases

- Accessibility teams inventorying overlay use across a large portfolio of sites
- Government or enterprise organizations setting overlay policies
- Researchers tracking overlay adoption trends
- Platform teams validating CMS or theme behavior

---

## Privacy and ethics

Find-Overlays:

- Is fully open source
- Runs locally for command-line scans, and in GitHub Actions for issue requests
- Publishes issue-requested results publicly on GitHub Pages and in the issue
- Does not send scan results to analytics, advertising, or other third parties

This aligns with the broader accessibility principle that tools should not introduce additional risk, surveillance, or data leakage.


## Security checks

Run the same checks locally:

```bash
uv run flake8 find-overlay.py overlay_safety.py scripts tests
uv run pytest
uv run bandit -c pyproject.toml -r find-overlay.py overlay_safety.py scripts
uv audit
```

GitHub Actions runs the test suite, linting, and the checks below on every
push and pull request. The [Argus](https://github.com/huntridge-labs/argus)
security scanners also run on a weekly schedule:

- **Bandit** for Python security issues
- **Gitleaks** for committed secrets
- **OSV-Scanner** for vulnerable dependencies
- **Dependency review** for vulnerable or disallowed licences in pull requests

The test workflow is defined in
[`.github/workflows/tests.yml`](.github/workflows/tests.yml).

Findings are uploaded to the repository's Security tab when code scanning is
available. The workflow is defined in
[`.github/workflows/security.yml`](.github/workflows/security.yml).

## Site automation

### Recurring monitoring

- [`data/sites.csv`](data/sites.csv) is the curated weekly monitoring list.
  Maintainers edit it manually; issue requests never modify it.
- [`.github/workflows/scan-and-publish.yml`](.github/workflows/scan-and-publish.yml)
  scans weekly, on scan-list changes, and on manual dispatch. It commits
  history snapshots and deploys the Pages site.
- [`scripts/build_site.py`](scripts/build_site.py) merges the latest monitoring
  scan with the previous snapshot and renders the static site.
- [`data/providers.json`](data/providers.json) holds sourced provider context
  and links to known concerns.

### Requested issue scans

- [`.github/workflows/scan-request.yml`](.github/workflows/scan-request.yml)
  reacts to `SCAN:` issues, validates the submitted addresses, scans them in a
  read-only job, and publishes results from a separate write-enabled job.
- [`scripts/issue_scan.py`](scripts/issue_scan.py) extracts, normalises, and
  validates submitted addresses. It also applies the 100-address limit and
  request fingerprint.
- [`scripts/render_issue_report.py`](scripts/render_issue_report.py) builds the
  issue report, JSON, CSV, run history, and the public requested-scans index.
- Issue reports live under `site/reports/issues/issue-<number>/`. They never
  write to `site/data/latest.json` or `data/sites.csv`.

`site/index.html`, `site/data/`, and `site/reports/` are generated. Edit
[`site/template.html`](site/template.html), [`site/styles.css`](site/styles.css),
[`scripts/build_site.py`](scripts/build_site.py), or
[`scripts/render_issue_report.py`](scripts/render_issue_report.py) instead.

## Accessibility of this tool

Find-Overlays has a command-line scanner and a static results site, and both
need to be usable. The project's accessibility commitment — including console,
CSV, and Pages output rules, known limitations, and how to report a barrier — is
documented in [ACCESSIBILITY.md](ACCESSIBILITY.md).

Contributions must keep terminal, CSV, and site output screen-reader friendly
and must not rely on colour alone. Issue reports use semantic tables, captions,
scoped headers, a skip link, visible focus, and no JavaScript requirement.

The scanner reports overlay use; it does not evaluate or certify the
accessibility of the sites it scans.

## Status and limitations

Overlay vendors change implementations frequently. Detection patterns may require updates over time.

False negatives are possible. Results should be treated as **indicators**, not proof of absence.

Contributions and improvements are welcome.


## License

Open source. See the repository license for details.

## AI Disclosure

Yes. AI was used in creating this tool. There be dragons!



