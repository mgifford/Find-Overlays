# Accessibility Overlay Scanner

Scan a list of URLs (from a sitemap, CSV file, or remote URL) to detect the presence of accessibility overlays (e.g., AccessiBe, UserWay, AudioEye) and common third‑party widgets. Produces a CSV report and prints a statistical summary. You can find more about [concerns with Accessibility Overlays](https://overlayfactsheet.com/) here and from the [2024 Web Almanc](https://almanac.httparchive.org/en/2024/accessibility#user-personalization-widgets-and-overlay-remediation). 

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

## AI Disclosure

Yes. AI was used in creating this tool. There be dragons! 




