# pip show verbose syntax

## Atomic Research Finding

### Command Syntax for Verbose Output with `pip show`

The standard way to enable verbose output with `pip show` is using the `-v` or `--verbose` flag.

```
bash
# Basic verbose output
pip show sqlalchemy -v

# Or equivalently
pip show sqlalchemy --verbose
```

### Alternative Verbose Flag Syntax

| Flag | Description |
|------|-------------|
| `-v` | Short form of `--verbose` |
| `--verbose` | Long form, enables verbose output |
| `-vv` or `--very-verbose` | Some versions support extended verbosity |

---

### Verification Method: pip help show

The most reliable way to confirm the correct syntax is by consulting pip's built-in help:

```bash
pip show --help
```

This will display all available options including verbose flags.

---

### Expected Output Format (verbose mode)

When `--verbose` is enabled, `pip show sqlalchemy` typically returns:

| Field | Description |
|-------|-------------|
| Name | Package name (`sqlalchemy`) |
| Version | Installed version number |
| Location | Path to package installation directory |
| Requires | List of dependencies with versions (verbose shows full dependency tree) |
| Required-by | Packages that depend on sqlalchemy (expanded in verbose mode) |

---

### Error Handling: Common Issues

If `pip show sqlalchemy` fails, common causes include:

1. **Package not installed**: `pip install sqlalchemy`
2. **Permission denied**: Run with elevated privileges (`sudo pip show sqlalchemy -v`)
3. **Corrupted cache**: Clear pip cache and retry

---

**Related Documents:** 
- [[pip_installation_commands]] (for installing sqlalchemy if needed)
- [[python_dependency_management]] (broader context on package management)
