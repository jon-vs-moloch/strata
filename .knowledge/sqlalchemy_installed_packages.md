# SQLAlchemy Installed Packages Research

## Summary
Found that `pip list | grep sqlalchemy` may miss variants due to case sensitivity and related packages.

## Key Findings

### 1. Case Sensitivity in pip list output
- `pip list` is case-insensitive for package names but displays them with original casing
- A package installed as "sqlalchemy" will display as "SQLAlchemy"
- Using grep without the `-i` flag may miss matches if filenames differ from package registry naming

### 2. SQLAlchemy Component Packages
The main components are separate packages:
| Package | Description |
|---------|-------------|
| `SQLAlchemy` (or `sqlalchemy`) | The ORM layer |
| `SQLAlchemy-core` (or `sqlalchemy-core`) | The Core SQL construction system and DBAPI interaction layer |

### 3. Related Dialect Packages
Common SQLAlchemy dialect packages that may be installed:
- `snowflake-sqlalchemy`
- `mysqlclient` or `PyMySQL` with SQLAlchemy support
- `psycopg2` with SQLAlchemy integration
- Other database-specific SQLAlchemy adapters

## Recommended Commands

### Option 1: Enhanced pip list with case-insensitive grep
```bash
pip list | grep -i sqlalchemy
```
This catches both "SQLAlchemy" and "sqlalchemy" variants.

### Option 2: Show detailed information for each package
```bash
pip show -f SQLAlchemy
# or 
pip show -f sqlalchemy-core
```
The `-f` flag shows full installation path and all files in editable mode.

### Option 3: List all packages with version info (no grep)
```bash
pip list --format=columns | grep -i sqlalchemy
```
Displays package name, location, version, and editable status.

## Conclusion
To reliably find ALL SQLAlchemy variants:
1. Use `pip show` for each known SQLAlchemy package name to get complete details
2. Combine with case-insensitive search or list all packages first then filter manually
3. Check for both "SQLAlchemy" and "sqlalchemy-core" as separate packages