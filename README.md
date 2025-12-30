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

You can find more about [concerns with Accessibility Overlays](https://overlayfactsheet.com/) here and from the [2024 Web Almanc](https://almanac.httparchive.org/en/2024/accessibility#user-personalization-widgets-and-overlay-remediation). 

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
2. Inspecting page content and loaded resources
3. Matching detected scripts, patterns, and domains against a maintained list of known overlay technologies
4. Reporting results in a machine-readable format suitable for review or follow-up

The detection approach is intentionally conservative. The goal is **signal**, not speculation.

---

## Installation and usage

> **Note:**  
> The installation and usage instructions below are intentionally preserved from the original README.  
> They describe how to install dependencies, configure inputs, and run scans.


## Requirements
- Python 3.8+
- Works on macOS/Linux/Windows. For macOS/Linux, use a virtual environment.

## Setup
Create and activate a virtual environment, then install dependencies.

```bash
# From the project root
python3 -m venv venv
source venv/bin/activate  # macOS/Linux
# .\venv\Scripts\activate  # Windows (PowerShell or CMD)

pip install requests
```

## Usage
You can pass a local file path (`.csv`/`.xml`/plain list) or a remote URL to a CSV/Sitemap.

```bash
python find-overlay.py <source>

# Examples
python find-overlay.py current-full.csv
python find-overlay.py https://example.com/sitemap.xml

# Simple two‑column output (url, detected_overlay)
python find-overlay.py current-full.csv --simple

# Stats only, no CSV generated
python find-overlay.py current-full.csv --no-csv

# Custom output filename
python find-overlay.py current-full.csv --output my-report.csv
```

### Sample Data
A small sample CSV is included:

```text
samples/sample-domains.csv
```

Run against the sample:

```bash
python find-overlay.py samples/sample-domains.csv
python find-overlay.py samples/sample-domains.csv --limit 100
```

### Arguments
- `source`: Required. Path to local file (`.csv`, `.xml`, or text) or a URL.
- `--output`: Custom output filename. Defaults to `<source>-overlays-YYYY-MM-DD.csv`.
- `--simple`: Output only `url` and `detected_overlay` columns.
- `--no-csv`: Do not write a CSV; print stats only.
 - `--limit <N>`: Limit the number of unique domains scanned to `N` (applied after deduplication). Useful for sampling large CSVs.

## Input Formats
The tool accepts two common formats:
- **Structured CSV**: If headers exist, it looks for a column containing “domain” (e.g., `Domain name`). Automatically cleans data by ignoring email addresses, adding `https://` when missing, and deduplicating domains.
- **Simple list**: One URL/domain per line, or comma/space‑separated text.

## Output
When CSV generation is enabled, the default columns are:
- `url`: Scanned URL
- `status`: HTTP status code (e.g., 200, 404)
- `detected_overlay`: Name(s) of overlay(s) found or `None Found`
- `other_widgets`: Detected common widgets (e.g., Intercom, Zendesk)

The console also prints a summary with status code counts, overlay counts, and the percentage of domains with overlays.

## Tips
- Large sources: prefer sitemaps or deduplicated CSVs to reduce scan time.
- Network errors/timeouts are counted and shown in the final stats.

### Rate Limiting & Timeouts
- The scanner uses a per‑request timeout of 10 seconds. Slow or non‑responsive sites will be marked as `Timeout` or `Conn Error` in the results.
- There is no built‑in rate limiting or concurrency; requests are made sequentially to avoid stressing servers.
- For very large scans, consider splitting your input file and running multiple smaller batches, or pausing between runs.
- If you need stricter pacing, you can add a small sleep between requests in `scan_domain` (e.g., `time.sleep(0.25)` after each request).

## Deactivate
Exit the virtual environment when you’re done:

```bash
deactivate
```

## What this tool intentionally does *not* do

- It does **not** claim a site is accessible or inaccessible
- It does **not** recommend or endorse overlay vendors
- It does **not** perform automated WCAG audits
- It does **not** track users or collect analytics

This tool is about **visibility and accountability**, not automated compliance.

---

## Typical use cases

- Accessibility teams auditing a large portfolio of sites
- Government or enterprise organizations setting overlay policies
- Researchers tracking overlay adoption trends
- Platform teams validating CMS or theme behavior

---

## Privacy and ethics

Find-Overlays:

- Is fully open source
- Runs locally
- Does not transmit scan results to third parties

This aligns with the broader accessibility principle that tools should not introduce additional risk, surveillance, or data leakage.


## Status and limitations

Overlay vendors change implementations frequently. Detection patterns may require updates over time.

False negatives are possible. Results should be treated as **indicators**, not proof of absence.

Contributions and improvements are welcome.


## License

Open source. See the repository license for details.

## AI Disclosure

Yes. AI was used in creating this tool. There be dragons! 




