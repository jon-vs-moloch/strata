# Sub-task 2: Understanding the `grep` Command

**Title**: grep - Linux Text Filtering Command  
**Subjects**: Unix, Bash, Shell, Text Processing  
**Tags**: linux, bash, shell, text-processing, grep, filtering  

## What is `grep`?

The `grep` (General Regular Expression) command searches for pattern matches in files or standard input.

### Basic Syntax
```
grep <pattern> [flags] <file>
```

### Common Options and Flags
| Flag | Description |
|------|-------------|
| `-r`, `--recursive` | Search directories recursively |
| `-i`, `--ignore-case` | Case-insensitive matching |
| `-v`, `--invert-match` | Invert match (show non-matches) |
| `-q`, `--quiet` | Silent mode, only output matches |

### Output Format
```
<matched_line_1>
<matched_line_2>
...<matched_line_n>
```

## grep and pip list Integration

When combining with `grep`:
```bash
grep <pattern> pip_list_output
```

Example: Finding all SQLAlchemy-related entries
```bash
pip list | grep sqlalchemy
```

This command pipeline:
1. `pip list` - generates full package list output (sent to stdout)
2. `grep sqlalchemy` - filters lines containing "sqlalchemy"
3. Result - only lines mentioning SQLAlchemy are displayed

## Common Patterns in Codebases
- Finding dependencies: `grep -r "import.*" .`
- Finding configuration entries: `grep "DATABASE_URL" config/

---

[[subtask_01_pip_list_command]]  
[[subtask_03_sqlalchemy_package_info]]