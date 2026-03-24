---
title: grep -i Flag Reference
subjects: [linux, shell, text-filtering]
tag: atomic-fact
---

# `grep` Command with `-i` Flag

## Syntax
```bash
grep [options] <pattern> <input>
```

## The `-i` (Case-Insensitive) Flag

### Purpose
Makes pattern matching case-insensitive, allowing matches regardless of letter casing.

### Equivalent to running:
```
grep -iE '[A-Za-z]*' <file>
```

### Common use cases:
| Use Case | Command |
|----------|--------|
| Find "SQL" anywhere | `grep -i SQL file.txt` |
| Match any capitalization | `grep -i sqlalchemy` |
| Search multiple lines | `cat largefile.txt | grep -i pattern` |

### Pattern matching behavior:
- Matches: `sqlalchemy`, `SQLAlchemy`, `SQA LALICHA`
- Does NOT match (unless using `-E` or other flags): `sQaLaLiChA` with spaces

---

[[grep_filtering]]
