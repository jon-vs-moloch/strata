---
title: pip show Command Reference
subjects: ["Python", "Pip", "Package Management"]
tags: ["command-line", "package-info", "version-checking"]
---

# `pip show` Command Reference

## Syntax
```
pip show <name>
```

or equivalently:
```
pip3 show <name>
```

## Purpose
Displays detailed information about an installed Python package, including:
- Package name and version number
- Installation location (site-packages path)
- Dependency requirements
- Metadata from `PKG-INFO` file

## Example Output for SQLAlchemy
```
sName: sqlalchemy
Version: 2.0.36
Summary: Database API and adapter layer for Python
Home-page: https://www.sqlalchemy.org/
Author: The SQLAlchemy team
License: MIT
Location: /home/user/.venv/lib/python3.11/site-packages/sqlalchemy
Requires:
    greenlet, typing-extensions
top-level-projects: sqlalchemy
```

## Exit Codes
| Code | Meaning |
|------|---------|
| 0 | Package found and displayed |
| 2 | Package not found or error occurred |

---

**Related**: [[virtual_env_detection]] (for checking active virtual environment)
**Related**: [[python_version_checking]] (for verifying interpreter version)
