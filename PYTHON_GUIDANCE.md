# Python Coding Guidance & Best Practices

This document outlines the core principles for writing high-quality,
maintainable, and "Pythonic" code. Use these guidelines when drafting scripts
or reviewing AI-generated Python code.

## 1. Core Philosophy (The Zen of Python)
* **Explicit is better than implicit:** Code should clearly state what it is doing. Avoid "magic" behavior.
* **Readability counts:** Code is read much more often than it is written. Write for the human reader.
* **Simple is better than complex:** If a task can be done simply, don't over-engineer it.
* **Flat is better than nested:** Avoid deep nesting of loops and conditionals; use guard clauses to return early.

## 2. Style and Formatting (PEP 8)

Automated linters (`flake8`, `ruff`) enforce these rules.  Run them before
every commit and fix all warnings before merging.

* **Indentation:** Always use 4 spaces per indentation level (no tabs).
* **Naming Conventions:**
    * `snake_case` for functions, variables, and modules.
    * `PascalCase` for classes.
    * `UPPER_CASE_SNAKE` for constants.
* **Line Length:** Keep every line at or under 88 characters.  For strings or
  comments that would exceed this limit, break them across multiple lines using
  implicit string concatenation or a backslash continuation.
* **Blank Lines Must Be Empty:** Never leave trailing whitespace on blank lines
  inside or between functions.  Most editors can be configured to strip
  trailing whitespace on save (`W293` / `W291` in flake8).
* **F-strings need placeholders:** Only use an `f"..."` string when it actually
  interpolates a variable.  Plain strings (`"layout: default"`) should not be
  prefixed with `f` (`F541` in flake8).
* **Whitespace around operators:** Use a single space on each side of binary
  operators (`x = 1`, not `x=1`), except inside keyword arguments and default
  parameter values (`def f(x=1)`).

### Common flake8 codes to watch for
| Code | Meaning | Fix |
|------|---------|-----|
| `E501` | Line too long | Break the line |
| `W291` | Trailing whitespace on a non-blank line | Remove trailing spaces |
| `W293` | Trailing whitespace on a blank line | Make the blank line truly empty |
| `F541` | f-string without placeholders | Remove the `f` prefix |
| `E302` | Expected 2 blank lines between top-level definitions | Add blank lines |

## 3. Type Hinting
* **Use Type Annotations:** Always annotate function signatures to improve clarity and catch bugs early.
    * *Example:* `def process_data(items: list[str]) -> int:`
* **Leverage `typing`:** Use `Optional`, `Union`, and `Any` only when necessary, favoring specific types whenever possible.

## 4. Documentation

Every module, class, and *public* function must have a docstring.  This
includes short utility functions and `main()`.  Missing docstrings are one of
the most common reasons a code review scores below an A.

### Required docstring locations
* Top of every `.py` file (module docstring).
* Every `class` definition.
* **Every `def` statement**, including `main()`, `parse_date()`, small helpers,
  and private functions prefixed with `_`.

### Preferred style – Google docstrings
```python
def parse_date(value: str) -> date | None:
    """Parse a date string in YYYY-MM-DD or MM/DD/YYYY format.

    Args:
        value: Raw date string from SAM.gov data.

    Returns:
        A ``datetime.date`` on success, or ``None`` if the value is
        empty or cannot be parsed.
    """
```

* **Comment "Why", not "How":** The code should explain *what* is happening;
  comments should explain the *reasoning* behind non-obvious logic.

## 5. Code Structure

### Function length
* **Target ≤ 50 lines per function** (excluding docstring).  If a function
  exceeds this, split it into focused helper functions with their own
  docstrings.
* Each function should do *one thing* (Single Responsibility Principle).

### Example – splitting a long function
Instead of one 100-line `write_page()` function that extracts fields, builds
front matter, formats contacts, and writes the file, extract helpers:

```python
def _extract_row_fields(row: dict) -> dict[str, str]: ...
def _build_front_matter(fields: dict[str, str]) -> list[str]: ...
def _build_contacts_section(fields: dict[str, str]) -> list[str]: ...
def _build_links_section(sam_link: str, pdf_link: str) -> list[str]: ...

def write_page(row: dict, output_dir: Path) -> bool:
    """Orchestrate field extraction and markdown assembly."""
    fields = _extract_row_fields(row)
    lines  = _build_front_matter(fields)
    lines += _build_contacts_section(fields)
    lines += _build_links_section(fields["sam_link"], fields["pdf_link"])
    (output_dir / "index.md").write_text("\n".join(lines))
    return True
```

* **Comprehensions:** Use list, dictionary, and set comprehensions for simple
  transformations, but revert to for-loops if the logic becomes too complex to
  read in one line.
* **Context Managers:** Use the `with` statement for resource management
  (files, network connections, database sessions) to ensure proper closing.
* **Don't Reinvent the Wheel:** Use the Python Standard Library (e.g.,
  `pathlib` for paths, `itertools` for efficient looping, `collections` for
  specialized data types).

## 6. Error Handling
* **Be Specific:** Never use a bare `except:`. Always catch specific exceptions (e.g., `ValueError`, `KeyError`).
* **Fail Fast:** Let errors happen early rather than masking them with `try-except` blocks that hide the root cause.

## 7. Tooling & Environment
* **Formatting:** Use automated tools like `black` or `ruff` to enforce style consistently.
* **Linting:** Run `flake8 scripts/` (or `ruff check scripts/`) before every
  commit.  A clean linter run with zero warnings is a prerequisite for an A
  grade.
* **Dependencies:** Always define dependencies in a `requirements.txt` or `pyproject.toml` file.

## 8. Testing
* **Unit Tests:** Write tests for individual components using `pytest`.
* **Small Functions:** Keep functions small (Single Responsibility Principle)
  to make them easier to test and reuse.  A function that is hard to test is
  usually a sign that it needs to be split.

## Quick checklist before submitting code

- [ ] Every function has a docstring (including `main` and private helpers).
- [ ] `flake8 scripts/` reports zero warnings.
- [ ] No function is longer than ~50 lines (docstring excluded).
- [ ] All type annotations are present on function signatures.
- [ ] No bare `f"..."` strings without an interpolated variable.
- [ ] Blank lines inside functions contain no trailing whitespace.
