---
title: pip show Command Semantics
subjects: [Python, pip, command-line, package-management]
tags: [pip-show, version-checking, output-format, python-package]
---

# pip show Command Reference

## Basic Syntax
```
pip show <package-name>
```

For SQLAlchemy specifically:
```bash
pip show sqlalchemy
```

## Output Format

The `pip show` command returns a **key-value pair** format:

| Field | Description |
|-------|-------------|
| Name | Package name (as installed) |
| Version | Installed version string |
| Location | Installation path |
| Requires | Dependencies list |
| Required-by | Packages requiring this one |
| Summary | Brief description |

## Example Output for SQLAlchemy

```
Name: sqlalchemy
Version: 2.0.36
Summary: Database Library and ORM
Home-page: https://www.sqlalchemy.org/
Author: Mike Bayer
Author-email: mikefaylor@gmail.com
License: MIT / Postgres License (see LICENSE file)
Location: /path/to/venv/lib/python3.x/site-packages/sqlalchemy
Requires: greenlet, typing-extensions
Required-by: your-package-name
```

## Parsing the Output

### Extracting Version Number
```bash
# Using grep and cut
grep "^Version:" pip show sqlalchemy | sed 's/.*://' 

# Or with awk
awk '/^Version:/ {print $2}' <(pip show sqlalchemy)
```

## Common Use Cases

1. **Verify installation**: Run `pip show sqlalchemy` to confirm package exists
2. **Get exact version**: Extract the Version field for compatibility checks
3. **Check dependencies**: View what packages are required by SQLAlchemy
4. **Location verification**: Confirm where the package is installed (useful for debugging path issues)
---

**Related**: [[virtual_environment_detection]] - Check if we're in a virtual environment before running pip commands.
