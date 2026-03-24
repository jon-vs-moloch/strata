# SQLAlchemy Installation Methods

## Official Installation Command

```bash
pip install sqlalchemy
```

This is the standard, recommended method for installing SQLAlchemy.

---

## Verification Commands

After installation, verify the package:

```bash
# Display detailed package information
pip show sqlalchemy

# Print version number from import
python -c "import sqlalchemy; print(sqlalchemy.__version__)"
```

### Expected Output
- `pip show` displays metadata including:
  - Name: SQLAlchemy
  - Version: X.X.X
  - Location: <installation_path>
  - Summary: Database abstraction library

---

## Installation Variants

| Command | Purpose |
|---------|---------|
| `pip install sqlalchemy` | Standard installation |
| `pip install "sqlalchemy[extra]"` | With extras (e.g., `postgresql`, `mysql`) |
| `pip install --upgrade sqlalchemy` | Upgrade existing version |

---

## Related Documentation

- [[pydantic_validation.md]] - Pydantic for data validation
- [[fastapi_application.md]] - FastAPI application structure
