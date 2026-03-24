---
title: "Python Module Import Verification Methods"
subjects: ["python", "import-testing", "module-verification"]
tags: ["development", "debugging", "environment-check"]
date_created: "$date"
---

# Method 1: Basic Import with Version Check
```bash
python -c "
import sys
print('Python version:', sys.version)

try:
    import sqlalchemy
    print('SQLAlchemy imported successfully')
    print('SQLAlchemy version:', sqlalchemy.__version__)
except ImportError as e:
    print(f'Import failed: {e}')
"```

# Method 2: Interactive Python Session
```bash
python << 'EOF'
import sys
print(f"Python executable path: {sys.executable}")
print(f"Python version info: {sys.version_info}")
print()

try:
    import sqlalchemy as sa
    print("✓ SQLAlchemy imported successfully")
    print(f"  Version: {sa.__version__}")
except ImportError as e:
    print(f"✗ Import failed: {e}")
EOF
```

# Method 3: Python Module Info (Detailed)
```bash
python -m pydoc sqlalchemy | head -20
```

# Method 4: Check for Multiple Python Versions
```bash
ls -la /usr/bin/python*  # Linux/macOS
where python*            # Windows CMD
py --version             # Windows (may show multiple versions)
```

---

**Related:** [[subtask_1_python_command_path]] - For command path verification
**Related:** [[subtask_2_sqlalchemy_python_versions]] - For version compatibility reference