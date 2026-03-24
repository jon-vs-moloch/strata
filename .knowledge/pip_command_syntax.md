# Pip Command Syntax Reference

## YAML Metadata
```
---
title: Pip Command Syntax
subjects: [python, pip, command-line]
tags: [cli, python-package-manager]
related: []
```

## Overview
Python's `pip` package manager is installed by default with Python 3.x and provides several commands for managing packages.

## Key Commands

| Command | Purpose |
|---------|--------|
| `pip show <package>` | Display detailed information about a specific installed package including version |
| `pip list` / `pip freeze` | List all installed packages |
| `pip install --dry-run <package>` | Show what would be installed without actually installing (useful for checking availability) |
| `python -c "import pkg; print(pkg.__version__)"` | Import and print version programmatically (NOT recommended per task requirements) |

## The Target Command: `pip show sqlalchemy`

This command returns a list of key-value pairs about the installed package:
```
Name: sqlalchemy
Version: X.X.X.X
...
```

The **Version** line contains the specific version string we need.

---

[[pip_command_syntax]]
[[installation_verification_methods]]
