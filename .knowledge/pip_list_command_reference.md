---
title: pip list Command Reference
subjects: [Python, Package Management]
tag: python-pip, command-reference, system-administration
---

# pip list Command - Output Format & Usage

## Basic Syntax

```bash
pip list           # List all installed packages with full names
pip list --full   # Show both short and long package names
pip list -v       # Verbose output (includes version, location)
pip list --format json  # JSON format for machine parsing
```

## Output Format Example

```bash
$ pip list
Package           Version      Location
----------------- -------------- ---------
sqlalchemy        2.0.35       /home/user/.local/lib/python3.11/site-packages/
certifi           2024.7.1     ...
pkg_resources     64.1.0       ...
```

## Key Columns

| Column | Description |
|--------|-------------|
| **Package Name** | Short name (e.g., `sqlalchemy`) |
| **Version** | Installed version number |
| **Location** | Installation path on disk |

## Verification Checklist for SQLAlchemy

When checking if SQLAlchemy is installed:

1. Run: `pip list`
2. Search output for the line containing `sqlalchemy` (or `SQLAlchemy`)
3. Verify it shows a version number (not "NOT INSTALLED" or empty)
4. Optionally verify location path exists on disk

---

**Related:** [[python_import_test_syntax]]