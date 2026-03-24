# SQLAlchemy Installation Verification Code

## Atomic Finding: Complete Working Python Script for Import Verification

### One-Liner Command (Quick Test)

```bash
python -c "
import traceback
try:
    import sqlalchemy
    print('✓ SUCCESS: SQLAlchemy imported!')
except ImportError as e:
    print('✗ FAILED: Cannot import SQLAlchemy')
    traceback.print_exc()
" 2>&1 || true
```

**Why this works:** The `|| true` at the end ensures bash doesn't exit with error code even if Python prints output.

---

### Complete Script File (Recommended)

```python
#!/usr/bin/env python3
"""
SQLAlchemy Installation Verification Script

This script attempts to import SQLAlchemy and captures the complete 
exception traceback to identify why installation verification fails.
"""

import sys
import os
import traceback  # Critical: For full traceback capture

def verify_sqlalchemy():
    """Attempt to import SQLAlchemy with complete error capture."""
    
    print("Attempting to import SQLAlchemy...")
    print("-" * 50)
    
    try:
        # Attempt the import
        import sqlalchemy
        
        if success:
            print(f"✓ SUCCESS: SQLAlchemy version {sqlalchemy.__version__} imported!")
            return True
        else:
            print("✗ FAILED: Cannot import SQLAlchemy")
            traceback.print_exc()
            return False
            
    except ImportError as e:
        # Capture COMPLETE traceback including call stack
        full_traceback = traceback.format_exc()
        
        print(full_traceback)
        
        # Exit with error code for automated scripts
        sys.exit(1)

def main():
    """Entry point."""
    verify_sqlalchemy()

if __name__ == "__main__":
    main()
```

---

### Installation Solutions (Based on Traceback Analysis)

| Error Pattern | Solution Command |
|---------------|------------------|
| `No module named 'sqlalchemy'` | `pip install sqlalchemy` |
| `No module named 'psycopg2'` | `pip install psycopg2-binary` |
| `No module named 'pyodbc'` | `pip install pyodbc` |
| `No module named 'sqlite3'` | Usually built-in; ignore |

---

## Related References
- [[sqlalchemy_import_error_handling]] - Theory and pattern explanation
