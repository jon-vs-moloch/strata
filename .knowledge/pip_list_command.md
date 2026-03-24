---
title: pip list Command Reference
subjects: [python, package-management, command-line]
tag: atomic-fact
---

# `pip list` Command Documentation

## Syntax
```bash
pip list [options]
```

## Purpose
Display all installed Python packages along with their version numbers in the current environment.

## Output Format
Standard pip listing uses this format:
```
<package-name>==<version>
```

### Example Output
```
Package           Version
--------------- -------
absl-py           2.1.0
aiohttp           3.9.0
arbitrary-package==1.2.3
numpy             1.24.0
sqlalchemy        2.0.25
```

## Key Properties
- **Case-sensitive**: Package names are matched exactly as installed
- **Sorted alphabetically** (by default)
- Shows all packages including those with no dependencies

---

[[pip_list_command]]
