# SQLAlchemy Import Error Handling Pattern

## Atomic Finding: Capturing Complete Traceback on SQLAlchemy Import Failure

### The Problem
When a Python package like SQLAlchemy isn't installed or fails to import, the error message may be truncated. To capture the **complete exception traceback**, you must use Python's `traceback` module.

### Solution Pattern

```python
import traceback  # Import the traceback module

try:
    import sqlalchemy  # Attempt to import SQLAlchemy
except ImportError as e:
    # Capture and print FULL traceback including call stack
    full_traceback = traceback.format_exc()
    
    # Option A: Print immediately (development/debugging)
    print("SQLAlchemy Import Failed!")
    print(full_traceback)
    
    # Option B: Store in variable for logging to file/external service
    error_message = {
        "error_type": type(e).__name__,
        "message": str(e),
        "full_traceback": full_traceback,
        "timestamp": __import__('time').time()
    }
    # Log or store `error_message` here
```

### Key Components

| Component | Purpose |
|-----------|---------|
| `traceback.format_exc()` | Returns the complete exception traceback as a string (including where it was raised) |
| `traceback.print_exc()` | Prints the traceback directly to stdout/stderr |
| `ImportError` | The specific exception type for missing modules/packages |

### Why This Matters for SQLAlchemy Installation Verification

1. **Complete Context**: The traceback shows not just "No module named 'sqlalchemy'" but also:
   - Which file triggered the import
   - What code preceded the problematic line
   - Any dependencies that failed first (e.g., `psycopg2` or `pyodbc`)

2. **Hidden Dependencies**: SQLAlchemy may fail to import due to missing database driver dependencies. The full traceback reveals this chain.

3. **Platform-Specific Errors**: On Windows, SQLAlchemy might depend on `pyodbc`. On Linux/Mac, it might need `psycopg2`. These dependency errors only appear in the complete traceback.

### Verification Code Template

```python
#!/usr/bin/env python3
"""Verify SQLAlchemy installation with complete error capture."""

import sys
import traceback

def verify_sqlalchemy_installation():
    """Attempt to import SQLAlchemy and capture any errors completely."""
    
    try:
        # Attempt the import - this will fail if not installed
        sqlalchemy = __import__('sqlalchemy')
        
        # If we get here, installation was successful!
        print("✓ SQLAlchemy imported successfully!")
        print(f"  Version: {sqlalchemy.__version__}")
        return True
        
    except ImportError as e:
        # Capture COMPLETE traceback including call stack
        full_traceback = traceback.format_exc()
        
        print("✗ FAILED to import SQLAlchemy")
        print("=" * 60)
        print(full_traceback)
        print("=" * 60)
        
        return False

if __name__ == "__main__":
    success = verify_sqlalchemy_installation()
    sys.exit(0 if success else 1)
```

### Expected Output Examples

**Case 1: SQLAlchemy Not Installed**
```python
✗ FAILED to import SQLAlchemy
============================================================
Traceback (most recent call last):
  File "verify_sqlalchemy.py", line 36, in <module>
    success = verify_sqlalchemy_installation()
  File "verify_sqlalchemy.py", line 20, in verify_sqlalchemy_installation
    sqlalchemy = __import__('sqlalchemy')
ModuleNotFoundError: No module named 'sqlalchemy'
============================================================
```

**Case 2: Missing Database Driver Dependency (Linux)**
```python
✗ FAILED to import SQLAlchemy
============================================================
Traceback (most recent call last):
  File "verify_sqlalchemy.py", line 36, in <module>
    success = verify_sqlalchemy_installation()
  File "verify_sqlalchemy.py", line 20, in verify_sqlalchemy_installation
    sqlalchemy = __import__('sqlalchemy')
  File "/usr/lib/python3.11/site-packages/sqlalchemy/__init__.py", line 95
    from packaging.version import Version
ModuleNotFoundError: No module named 'packaging'
============================================================
```

---

## Related References
- [[python_try_except_full_traceback]] - How to capture complete exception tracebacks
- [[sqlalchemy_exception_handling]] - SQLAlchemy-specific error handling patterns
