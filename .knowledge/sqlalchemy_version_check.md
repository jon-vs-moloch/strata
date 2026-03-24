# SQLAlchemy Version Check Command

## Primary Method: Import and Print

```python
import sqlalchemy
print(sqlalchemy.__version__)
```

Or using the `sqlalchemy` module directly:

```python
from sqlalchemy import __version__
print(__version__)
```

---

## Alternative Methods

### Method 1: Using inspect()

```python
import sqlalchemy as sa
inspector = sa.inspect(engine)
print("Version:", inspector.server_version)
```

### Method 2: Try/Except Pattern (for error capture)

```python
def check_sqlalchemy():
    try:
        import sqlalchemy
        print(f"✓ SQLAlchemy {sqlalchemy.__version__} installed")
        return True
    except ImportError as e:
        print(f"✗ ImportError: {e}")
    except ModuleNotFoundError as e:
        print(f"✗ ModuleNotFoundError: {e}")
    return False
```

---

## Common Error Scenarios

| Error Type | Description | Fix |
|------------|-------------|-----|
| `ModuleNotFoundError` | SQLAlchemy not in PATH or requirements.txt missing | Run `pip install sqlalchemy` |
| `ImportError: cannot import name 'create_engine'` | Syntax version mismatch (SQLAlchemy 1.4+) | Use `.create_engine()` instead of standalone function |
| `AttributeError: module has no attribute '__version__'` | SQLAlchemy installed without metadata | Run `pip install --force-reinstall sqlalchemy` |

---

## Verification Commands

```bash
# Check if package is installed
python -c "import sqlalchemy; print(sqlalchemy.__version__)"

# Alternative with pip
pip show sqlalchemy | grep Version
```
