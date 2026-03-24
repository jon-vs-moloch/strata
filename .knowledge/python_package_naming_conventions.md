# Python Package Naming Conventions

## Overview
Python packages follow specific conventions regarding name casing, import behavior, and PyPI handling.

## Key Findings

### 1. PyPI Case Sensitivity
- **PyPI is case-insensitive** for package name matching
- When installing with a different case: `pip install SQLAlchemy` installs the same as `pip install sqlalchemy`
- The stored canonical name on PyPI is typically lowercase

### 2. Import Behavior (PEP 8)
```python
# Both work - PEP 8 recommends lowercase
import sqlalchemy           # ✓ Recommended
import SQLAlchemy           # Also works, but discouraged
```

### 3. pip list Output Behavior
- Packages are listed in **case-insensitive sorted order**
- The actual display case may vary by environment and version
- `pip show <package>` always displays the canonical name (usually lowercase)

## SQLAlchemy-Specific Naming Conventions

| Package Name | Import Statement | PyPI Canonical |
|-------------|-----------------|---------------|
| `sqlalchemy` | `import sqlalchemy` | `sqlalchemy` |
| `SQLAlchemy` | `import SQLAlchemy` | `sqlalchemy` |

## Verification Commands

```bash
# Shows canonical (lowercase) name
pip show sqlalchemy

# Lists all packages - case-insensitive matching works
pip list | grep -i sqlalchemy  # Use -i for case-insensitive grep
```

## Best Practices
- Always use lowercase package names in `pip install`/`pip show`
- Use `import <lowercase>` in Python code (PEP 8 compliant)
- When grepping, add `-i` flag to be safe: `grep -i sqlalchemy`
