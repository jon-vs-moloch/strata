# Python Module Check Practices

## Atomic Finding: ImportError vs Syntax Errors

When a module is missing, Python raises `ImportError`. When there's a syntax error in the import statement itself (e.g., missing comma, invalid characters), Python raises `SyntaxError` during parsing.

```python
# Missing module → ImportError
import nonexistent_module  # Raises: ModuleNotFoundError (subclass of ImportError)

# Syntax errors in import → SyntaxError
import non_existent_modle  # Raises: SyntaxError
```

## Atomic Finding: Best Practices for Checking Module Existence

### Method 1: Try/Except Block (Recommended)
```python
try:
    import sqlalchemy
    print(f"SQLAlchemy version: {sqlalchemy.__version__}")
except ImportError as e:
    print(f"Module not found: {e}")
```

### Method 2: Using `importlib.util.find_spec()` (Python 3.4+)
```python
from importlib.util import find_spec

spec = find_spec("sqlalchemy")
if spec is None:
    print("SQLAlchemy module not found")
else:
    # Module exists, but no version attribute on some packages
    try:
        import sqlalchemy
        print(f"SQLAlchemy version: {sqlalchemy.__version__}")
    except AttributeError:
        print("Module exists but has no __version__ attribute")
```

### Method 3: Checking pip packages
```python
import subprocess
result = subprocess.run(["pip", "show", "sqlalchemy"], capture_output=True, text=True)
if result.returncode == 0:
    print("SQLAlchemy is installed")
else:
    print("SQLAlchemy is not installed")
```

## Atomic Finding: SQLAlchemy Version Attribute Location

```python
# Primary location for version
import sqlalchemy
print(sqlalchemy.__version__)

# Alternative locations (may vary by version)
from sqlalchemy import __version__ as sv  # Direct import
from sqlalchemy.version import __version__  # In some versions
```

## Atomic Finding: Common Import Patterns

| Pattern | Behavior |
|---------|----------|
| `import sqlalchemy` | Imports entire package, exposes all submodules in `sqlalchemy.*` namespace |
| `from sqlalchemy import create_engine` | Only imports specific name, requires explicit qualification |
| `from sqlalchemy import __version__` | Directly accesses version string (if available) |

## Atomic Finding: ModuleNotFoundError Details

- In Python 3.6+, `ModuleNotFoundError` is a subclass of `ImportError`
- The error message contains the missing module name
- Catching as `ImportError` still works for compatibility

```python
try:
    import sqlalchemy
except (ImportError, ModuleNotFoundError) as e:  # Python 3.6+
    print(f"Cannot import sqlalchemy: {e}")
```

---

**Related:** [[importlib_find_spec]] | [[pip_show_usage]]