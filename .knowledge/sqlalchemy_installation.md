---
title: SQLAlchemy Installation Guide
subjects: [Python, Database ORM, pip]
tags: [database, orm, installation, version-checking]
---

## SQLAlchemy Installation

### Prerequisite Package
- `pip` (Python package installer)

### Install Command
```bash
pip install sqlalchemy
```

**Notes:**
- No specific version number is required; the latest stable release will be installed automatically
- Python 3.7+ is recommended for full support

---

## Version Check Script

### Method 1: Using `importlib.metadata` (Python 3.8+)
```python
from importlib.metadata import version
print(f"SQLAlchemy version: {version('sqlalchemy')}")
```

### Method 2: Using pkg_resources (pip >= 6)
```python
from pkg_resources import get_distribution
print(get_distribution("sqlalchemy").version)
```

### Method 3: Using `importlib.metadata` with fallback
```python
try:
    from importlib.metadata import version as get_version
except ImportError:
    from importlib_metadata import version as get_version

print(f"SQLAlchemy version: {get_version('sqlalchemy')}")
```

### Method 4: Using `pip show` (no Python code needed)
```bash
pip show sqlalchemy
# Output includes: Version: X.Y.Z
```

---

## Atomic Findings References
- [[python_package_installation]] - General pip install patterns
- [[importlib_metadata_api]] - Details on importlib.metadata API
