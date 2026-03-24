# Sub-task 1: Verify Python Availability

## Objective
Confirm that a Python interpreter exists in the environment before attempting pip operations or SQLAlchemy import.

---

## Command to Execute
```bash
python --version
```

**Alternative commands:**
- `python3 --version` (if python is aliased)
- `/usr/bin/python3 --version` (Linux path)
- `C:\Python3x\python.exe --version` (Windows path)

---

## Expected Successful Output
```
Python 3.11.x
```
Or any valid Python version format:
- `Python 2.7.18`
- `Python 3.9.7`
- `Python 3.12.0`

---

## Expected Failed Output (if Python not found)
```
The system cannot find the command 'python'
```
Or on Windows:
```
'python' is not recognized as an internal or external command...
```

---

## Success Criteria
- Command returns a version number string
- No error messages about missing interpreter
- Exit code: 0 (success)

---

## Notes for Next Step
If Python is confirmed available, proceed to **Sub-task 2** (Verify pip Availability) immediately.
