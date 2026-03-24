# Sub-task 1: Understanding the `pip list` Command

**Title**: pip list - Python Package List Command  
**Subjects**: Python, CLI, Package Management  
**Tags**: python, pip, command-line, package-manager, installation  

## What is `pip list`?

The `pip list` command displays all installed packages in a Python environment.

### Syntax and Options
```
pip list              # List all installed packages
pip list --format    # Specify output format (json, freeze, etc.)
pip list --user      # Show only user-installed packages
```

### Basic Output Format
```
Package           Version  Location                               Installed?
----------------- -------- ------------------------------------- --------------
numpy             1.24.0   /usr/lib/python3/dist-packages       YES
pandas            2.0.0    /usr/lib/python3/dist-packages       YES
scipy             1.10.0   /usr/lib/python3/dist-packages       YES
```

### Key Characteristics:
- Shows package name, version, and installation location
- Indicates whether the package is installed (`YES`/`NO`)
- Lists packages from all sources (pip, apt-get, easy_install, etc.)

## Related Commands
- `pip install <package>` - Install a new package  
- `pip uninstall <package>` - Remove an installed package  
- `pip show <package>` - Show detailed info about specific package  

---

[[subtask_02_grep_command]]  
[[subtask_03_sqlalchemy_package_info]]