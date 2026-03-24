---
title: "SQLAlchemy Python Version Requirements"
subjects: ["sqlalchemy", "python-versions", "compatibility"]
tags: ["database-framework", "version-checking", "requirements"]
date_created: "$date"
---

# SQLAlchemy 2.0+ Interpreter Requirements

## Modern SQLAlchemy (1.4+) Supports:
```
Python 3.8, 3.9, 3.10, 3.11, 3.12
```
- PEP 572: `match` statements supported
- Type hints fully functional
- Async support via `aiofiles`, `anyio`, or standard asyncio

## SQLAlchemy 1.x (Legacy) Supports:
```
Python 2.7, 3.4 - 3.10
```
- Deprecated Python 2 support as of SQLAlchemy 2.0 RC1

# Minimum Recommended Versions
| SQLAlchemy | Min Python |
|------------|------------|
| 1.4.x      | 3.8        |
| 2.0+       | 3.9+ (recommended)

# Verification Commands to Confirm:
```bash
python --version          # Check installed version
python -c "import sys; print(sys.version)"   # Full output
python -c "import sqlalchemy; print(sqlalchemy.__version__)"
```

---

**Related:** [[subtask_3_python_module_import_test]] - For verifying SQLAlchemy can be imported at runtime