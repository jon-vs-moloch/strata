---
title: apt sources.list.d Directory Command
subjects: [Linux, Debian, Ubuntu, Package Management]
tags: [command-line, verification, repositories]
---

# Checking `/etc/apt/sources.list.d/` Configuration

## Purpose
The directory `/etc/apt/sources.list.d/` contains repository source configurations for apt on Debian/Ubuntu systems.

## Verification Command
```bash
ls /etc/apt/sources.list.d/
```

## Expected Contents (Typical)
- `sources.list.d/01-main.sources`
- `sources.list.d/02-backports.sources`
- `sources.list.d/pypy-pip-installed.sources`
- `sources.list.d/python-minimal.sources`
- Various third-party PPA repositories

## Key Points
- Files are named with numeric prefixes (lower number = higher priority)
- Each file contains a list of repository URLs to fetch package metadata from
- The directory may also be empty on minimal installations
- Check for validity using: `sudo apt update` followed by `apt list --installed`
