# Python Version Check Guide

## Purpose
Verify the active Python interpreter exists and has access to standard system paths before running package management commands.

---

## Command Syntax
```bash
python --version
```

**Equivalent variants:**
- `python3 --version` (explicit version 3)
- `py --version` (Python launcher on Windows)
- `%PYTHONHOME%` / `$PYTHONHOME` (environment variable lookup)

---

## Expected Output Format

### Typical Output:
```
Python 3.11.6 (main, Dec 27 2024, 08:59:38) [MSC v.1916 64 bit (AMD64)] on win32
Type "help", "copyright", "credits" and "version" for more information.
```

### Key Information Provided:
| Component | Meaning |
|------------|---------|
| `Python X.Y.Z` | Version number |
| `main, Date Time` | Build timestamp (release candidate) |
| `[Build Info]` | Compiler details |
| `on [Platform]` | Operating system and architecture |

---

## Platform-Specific Behaviors

### Windows
```
python --version
```
- Uses `py.exe` launcher when available
- May return: `Python 3.11.6 on win32`
- Alternative: `%PYTHONHOME%` environment variable

### macOS (Homebrew/builtin)
```
python3 --version
```
- May use `/usr/bin/python3` or Homebrew path
- Check with: `which python3`

### Linux (Debian/Ubuntu)
```
python3 --version
```
- Typically at `/usr/bin/python3`
- Check with: `readelf -h $(which python3)`

---

## Verification Checklist

Before running pip/pipx commands, confirm:

1. ✅ **Interpreter exists** — Command returns version string without error
2. ✅ **Version meets requirements** — Compare major/minor against project specs
3. ✅ **Platform compatibility** — Verify `on [platform]` line matches expected OS
4. ✅ **Path accessibility** — Ensure working directory contains needed dependencies
5. ✅ **Virtual environment active** — Check `(venv)` prefix if using virtualenv

---

## Troubleshooting

| Issue | Possible Cause | Solution |
|-------|----------------|----------|
| `command not found` | Python not in PATH | Add to PATH or use full path: `/usr/bin/python3 --version` |
| Multiple versions detected | Multiple installations exist | Use explicit version: `python3.11 --version` |
| Permission denied | Running as non-root | Use `sudo python3 --version` (Linux) |
| Empty output | Corrupted interpreter | Reinstall Python or use alternative path |

---

## Best Practices

### 1. Always verify before package installation
```bash
python --version     # Confirm interpreter exists
pip --version        # Verify pip is accessible to this Python
```

### 2. Use version-specific commands when needed
```bash
python3.10 -m venv venv-310  # Create isolated environment
source venv-310/bin/activate
python --version            # Should show Python 3.10.x inside venv
```

### 3. Record baseline for reproducibility
```bash
echo "Python Version: $(python --version)" > .python_env_marker
```

---

## References
- [Official Python Download](https://www.python.org/downloads/) — Verified interpreter binaries
- [PEP 508](https://peps.python.org/pep-0508/) — Package metadata standards
