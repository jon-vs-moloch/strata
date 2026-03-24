# Subtask: Verify Pip Tool Installation

## Objective
Check if pip package manager is installed and accessible from command line.

## Verification Commands

### 1. Check Pip Binary Existence (System Pip)
```
pip --version          # Standard pip check
pip3 --version         # Python 3 specific (common on Linux/macOS)
which pip              # Linux/macOS path
where pip               # Windows CMD path
pip3 --help             # Detailed help output
```

### 2. Alternative: Check via Python Module
```python
import sys
sys.path.insert(0, '/usr/lib/python3/dist-packages')
import pkg_resources
print(pkg_resources.distribution('pip').version)
```

### 3. pipx Installation (if applicable)
```
pipx --help
which pipx
type pipx              # PowerShell
```

## Atomic Findings

| Check | Expected Result |
|-------|----------------|
| `pip` command exists in PATH | True/False |
| `pip3` command exists (if different) | True/False |
| `pip --version` returns version | True/False |
| pip can list installed packages (`pip list`) | True/False |
| pip can show help output | True/False |

---
[[subtask_python_installation_check]] 
[[subtask_python_path_verification]]
[[subtask_pipx_installation_check]]
