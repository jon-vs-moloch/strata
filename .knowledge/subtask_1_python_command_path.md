---
title: "Python Command Path Verification Methods"
subjects: ["python", "verification", "command-line"]
tags: ["system-administration", "deployment", "debugging"]
date_created: "$date"
---

# Verification Method 1: `which` command (Linux/macOS)
```
which python
which python3
```
- Returns the full path to the executable
- Example output: `/usr/bin/python3.12`

# Verification Method 2: `where` command (Windows CMD)
```
where python
where py
```
- Shows all locations where Python can be found
- Useful when multiple versions exist

# Verification Method 3: PowerShell Path Check (Windows)
```
powershell -Command "(Get-Command python).Source"
```
- Returns the executable source path

# Verification Method 4: `type` command (Linux/macOS BSD)
```
type python
```
- Similar to `which`, shows command location

---

**Related:** [[subtask_2_sqlalchemy_python_versions]] - For cross-referencing version requirements