# SQLAlchemy Installation Command

## Primary Installation Command

```bash
pip install -q sqlalchemy
```

**Breakdown:**
- `pip`: Python package installer (comes pre-installed with Python)
- `install`: Action to add a package
- `-q`: Quiet flag that suppresses verbose output during installation
- `sqlalchemy`: Target package name

## Verification Methods

### 1. Basic Import Test
```python
import sqlalchemy
print("SQLAlchemy version:", sqlalchemy.__version__)
```

### 2. Check Available Commands
```bash
dbfsync -l
# Returns: Installed packages including sqlalchemy
```

---

**References:** [[pip_install_sqlalchemy_syntax]]
