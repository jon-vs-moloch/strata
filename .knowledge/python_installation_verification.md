# Python Installation Verification

## Common Causes of Import Failures

### 1. Wrong Python Interpreter Being Used
```bash
# Check which Python is active
which python      # Linux/MacOS
where python      # Windows
type python       # Unix systems
python --version  # Verify version

# On Mac/Linux, check PATH carefully
ls -la /usr/bin/python*
ls -la ~/bin/python*
```

### 2. Virtual Environment Not Activated
```bash
# Check active venv
which pip          # Should show venv path
pip --version      # Should confirm virtual env
source venv/bin/activate    # Mac/Linux
venv\Scripts\activate       # Windows
```

### 3. SQLAlchemy Not Installed in Active Environment
```bash
# Check pip location
where pip          # Windows
type pip           # Linux/MacOS

# Install if missing
pip install sqlalchemy
pip3 install sqlalchemy  # Python 3 specific
```

## Diagnostic Commands Hierarchy

| Priority | Command | Purpose |
|----------|---------|--------|
| 1 | `which python` / `type python` | Find interpreter path |
| 2 | `python --version` | Verify version matches requirements |
| 3 | `pip show sqlalchemy` | Check if package exists in current env |
| 4 | `python -c "import sys; print(sys.executable)"` | Confirm exact executable used |

## Troubleshooting Checklist

- [ ] Active virtual environment confirmed?
- [ ] Python version matches SQLAlchemy's requirements (≥2.7, <3.12 for most versions)
- [ ] pip path matches python path (`which pip` should match `which python` directory)
- [ ] No PATH pollution from multiple Pythons
- [ ] Run command as same user who installed package

---

*Related: [[python_installation_verification]] (self-reference)*