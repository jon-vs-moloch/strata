---
title: Check Pip Availability After Venv
subjects: [Python, Shell Scripting]
tag: pip, python-package-manager, verification
---

## Task 02: Check Pip Availability After Venv Confirmed

### Objective
Verify that `pip` is available after confirming a virtual environment exists.

### Command Reference
```bash
# Primary command to show pip version
pip --version

# Alternative if pip3 exists separately
pip3 --version

# Check pip location (similar to which)
pip which
```

### Expected Outputs
| Scenario | Output |
|----------|--------|
| Pip Available | `pip X.Y.Z from /path/to/venv/lib/pythonX.Y/site-packages` |
| Pip Unavailable | "command not found" error |

---

## Task 03: Check Python Version (Optional but Recommended)

### Command Reference
```bash
python --version
python3 --version
```

This confirms the Python interpreter version being used.

---

## Summary Flow

```
┌─────────────┐
│ Step 01     │
│ Verify Venv │───├── No venv → Create & Activate
│ Exists      │   └── Yes → Continue to next step
└─────────────┘         ↓
                        ↓
           ┌──────────────────────────┐
           │ Step 02: Check pip       │
           │    availability          │
           └──────────────────────────┘
                            ↓
                    (Optional) Python version check
```

---

## Notes
- Running `pip show` requires pip to be installed and the virtual environment to be activated
- The venv should automatically use `pip` from its own site-packages directory
