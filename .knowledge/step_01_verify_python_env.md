---
title: Verify Python Virtual Environment Exists
subjects: [Python, Shell Scripting]
tag: venv, python-environment, verification
---

## Task 01: Verify Python Virtual Environment Exists

### Objective
Determine if a Python virtual environment is available and activated in the current shell session.

### Command Reference
```bash
# Check for active virtual environment
which python3
which pip3

# Alternative verification methods
echo $VIRTUAL_ENV  # Will be set when venv is activated
python -m site     # Shows site-packages path if venv active
```

### Expected Outputs
| Scenario | Output |
|----------|--------|
| Venv Active | Path to virtual env (e.g., `/path/to/venv/bin/python3`) |
| No Venv | Command not found error or empty output |

### Next Step
If venv is active → [[step_02_check_pip_availability]]
Else → Create and activate virtual environment first.
```bash
python3 -m venv myvenv
source myvenv/bin/activate  # Linux/macOS
myvenv\Scripts\activate     # Windows
```

### Validation Criteria
- Command `which python` returns a valid path
- No "command not found" errors for `pip` or `python3`
