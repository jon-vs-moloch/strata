

```python
#!/usr/bin/env python3
"""
Configuration Security Validator
A comprehensive tool for scanning configuration files (.env, .config.json, Dockerfiles) 
for security vulnerabilities, deprecated options, and best practice violations.

Author: Senior Implementation Engineer
"""

import os
import re
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class Severity(Enum):
    """Severity levels for validation findings."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


@dataclass
class Finding:
    """Represents a security finding in a configuration file."""
    severity: Severity
    message: str
    file_path: str
    line_number: Optional[int] = None
    code_snippet: Optional[str] = None
    remediation: str = ""


@dataclass
class ValidationResult:
    """Container for validation results."""
    files_checked: int = 0
    total_findings: int = 0
    findings: List[Finding] = field(default_factory=list)
    
    def add_finding(self, finding: Finding):
        self.findings.append(finding)
        self.total_findings += 1


class ConfigValidator:
    """Main configuration validator class."""
    
    # AWS and GitHub related patterns (CRITICAL)
    AWS_PATTERNS = [
        r'^AWS_ACCESS_KEY_ID\s*[=:]\s*(AKIA[A-Z0-9]{16}|ASIA[A-Z0-9]{16})',
        r'(?i)[Aa]ws[_\-]?access[_\-]?key[_\-]?id[^\n]*=',
    ]
    
    GITHUB_TOKEN_PATTERNS = [
        r'^GITHUB_TOKEN\s*[=:]\s*(gh[pousr][A-Za-z0-9_]{36,})',
        r'(?i)[Gg]ithub[_\-]?token[^\n]*=',
    ]
    
    # General secret patterns (CRITICAL)
    SECRET_PATTERNS = [
        r'(?:password|passwd|pwd)\s*[=:]\s*["\']?\S+["\']?',
        r'(?i)(api[_\-]?key|apikey)[^\n]*=',
        r'(?i)(secret[_\-]?key|secretkey)[^\n]*=',
    ]
    
    # Debug mode patterns (MEDIUM)
    DEBUG_PATTERNS = [
        r'(?i)(debug|DEBUG)\s*[=:]\s*true',
        r'(?i)(debug_mode|debugmode)\s*[=:]\s*(?:1|yes|true)',
        r'(?i)(log_level|LOG_LEVEL)\s*[=:]\s*(?:DEBUG|TRACE)'
    ]
    
    # Dockerfile security patterns
    DOKERFILE_PATTERNS = {
        'insecure_copy_l': [
            (Severity.HIGH, 
             "Insecure COPY -L directories: Should use --chmod=755 or explicit permissions",
             r'COPY\s+.*\s+-L\s+\S+'),
            
            (Severity.MEDIUM,
             "COPY with -L flag may expose file system contents to build context",
             r'COPY\s+-L\b'),
        ],
        
        'env_secrets': [
            (Severity.HIGH,
             "ENV instructions for secrets should use ARG or docker secrets instead",
             r'^\s*ENV\s+[A-Z_]+[_\-]?SECRET[^\n]*=',
             
             'Use ARG in Dockerfile: ARG SECRET_FILE=/path/to/file\nCOPY --chmod=600 /run/secrets/secret:/app/secret',
             r'^(?:#|\/)\s*(ENV|ARG|SECRETS)')
        ]
    }
    
    def __init__(self, base_path: str = "."):
        self.base_path = os.path.abspath(base_path)
        self.validator = ValidationResult()
        
    def scan_directory(self) -> None:
        """Scan the directory and all subdirectories for configuration files."""
        for root, dirs, files in os.walk(self.base_path):
            # Skip hidden directories and common build artifacts
            dirs[:] = [d for d in dirs if not d.startswith('.') or d in ['git', 'node_modules']]
            
            for filename in files:
                self.scan_file(os.path.join(root, filename))
                
    def scan_file(self, file_path: str) -> None:
        """Scan a single file for security issues."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
                lines = content.split('\n')
                
            self._scan_env_file(file_path, lines)
            if '.json' in file_path or '.config.json' == file_path:
                self._scan_json_config(file_path, json.loads(content))
            elif 'Dockerfile' in file_path:
                self._scan_dockerfile(file_path, lines)
                
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"⚠️  Warning: Could not process {file_path}: {e}")

    def _scan_env_file(self, file_path: str, lines: List[str]) -> None:
        """Scan .env files for security issues."""
        if not file_path.endswith('.env'):
            return
            
        for i, line in enumerate(lines):
            line_num = i + 1
            stripped = line.strip()
            
            # Skip comments and empty lines
            if not stripped or stripped.startswith('#') or stripped.startswith(';'):
                continue
                
            # Check AWS credentials (CRITICAL)
            for pattern in self.AWS_PATTERNS:
                if re.search(pattern, stripped):
                    finding = Finding(
                        severity=Severity.CRITICAL,
                        message="AWS_ACCESS_KEY_ID found - Exposed credentials in plain text",
                        file_path=file_path,
                        line_number=line_num,
                        code_snippet=f"{stripped[:80]}...",
                        remediation="Use AWS Secrets Manager or environment variable from secure source. Consider: aws secretsmanager get-secret-value"
                    )
                    self.validator.add_finding(finding)
                    
            # Check GitHub tokens (CRITICAL)
            for pattern in self.GITHUB_TOKEN_PATTERNS:
                if re.search(pattern, stripped):
                    finding = Finding(
                        severity=Severity.CRITICAL,
                        message="GitHub token found - Exposed authentication credentials",
                        file_path=file_path,
                        line_number=line_num,
                        code_snippet=f"{stripped[:80]}...",
                        remediation="Use GitHub Actions secrets or AWS Secrets Manager. Consider: gh token --help"
                    )
                    self.validator.add_finding(finding)
                    
            # Check general secrets (CRITICAL)
            for pattern in self.SECRET_PATTERNS:
                if re.search(pattern, stripped):
                    finding = Finding(
                        severity=Severity.CRITICAL,
                        message="Plaintext password/secret found - Security risk",
                        file_path=file_path,
                        line_number=line_num,
                        code_snippet=f"{stripped[:80]}...",
                        remediation="Use environment variables from secure sources (Vault, AWS Secrets Manager). Never hardcode secrets."
                    )
                    self.validator.add_finding(finding)
                    
            # Check debug mode (MEDIUM)
            for pattern in self.DEBUG_PATTERNS:
                if re.search(pattern, stripped):
                    finding = Finding(
                        severity=Severity.MEDIUM,
                        message="Debug mode enabled - May expose sensitive information",
                        file_path=file_path,
                        line_number=line_num,
                        code_snippet=f"{stripped[:80]}...",
                        remediation="Disable debug mode in production. Use proper logging levels instead."
                    )
                    self.validator.add_finding(finding)

    def _scan_json_config(self, file_path: str, data: Dict[str, Any]) -> None:
        """Scan JSON configuration files for security issues."""
        if not isinstance(data, dict):
            return
            
        json_file = os.path.join(file_path).replace('.json', '')
        
        # Check top-level and nested keys for secrets (CRITICAL)
        def check_nested(obj: Any, path: str = "") -> None:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    full_path = f"{path}.{key}" if path else key
                    
                    # Check for secret-related keys
                    if any(secret_keyword in key.lower() 
                           for keyword in ['password', 'secret', 'token', 'api_key']):
                        if isinstance(value, str) and len(value.strip()) > 0:
                            finding = Finding(
                                severity=Severity.CRITICAL,
                                message=f"Secret found in {full_path} - Plaintext credential",
                                file_path=file_path,
                                line_number=None,
                                code_snippet=value[:100] if isinstance(value, str) else json.dumps(value)[:100],
                                remediation="Use environment variables or external secret management (HashiCorp Vault, AWS Secrets Manager)"
                            )
                            self.validator.add_finding(finding)
                    
                    # Recursively check nested objects
                    check_nested(value, full_path)
            elif isinstance(obj, str):
                # Check string values for debug mode (MEDIUM)
                if any(debug_keyword in obj.lower() 
                       for keyword in ['debug', 'trace', 'verbose']):
                    finding = Finding(
                        severity=Severity.MEDIUM,
                        message=f"Debug mode enabled at {full_path}",
                        file_path=file_path,
                        line_number=None,
                        code_snippet=obj[:100],
                        remediation="Use appropriate log levels for production environments"
                    )
                    self.validator.add_finding(finding)

        check_nested(data)

    def _scan_dockerfile(self, file_path: str, lines: List[str]) -> None:
        """Scan Dockerfiles for security best practices."""
        if not file_path.endswith('Dockerfile'):
            return
            
        docker_file = os.path.join(file_path).replace('/Dockerfile', '')
        
        # Check insecure COPY -L usage (HIGH)
        for pattern, message in self.DOKERFILE_PATTERNS['insecure_copy_l']:
            if re.search(pattern, '\n'.join(lines)):
                finding = Finding(
                    severity=Severity.HIGH,
                    message=message,
                    file_path=file_path,
                    line_number=None,
                    code_snippet="COPY ... -L",
                    remediation="Use explicit permissions: COPY --chmod=755 /path/to/dir:/app/dir"
                )
                self.validator.add_finding(finding)

        # Check ENV for secrets (HIGH)
        for pattern, message, remediation in self.DOKERFILE_PATTERNS['env_secrets']:
            if re.search(pattern, '\n'.join(lines)):
                finding = Finding(
                    severity=Severity.HIGH,
                    message=message,
                    file_path=file_path,
                    line_number=None,
                    code_snippet="ENV SECRET=value",
                    remediation=remediation
                )
                self.validator.add_finding(finding)

    def get_summary(self) -> Dict[str, Any]:
        """Get a summary of all findings."""
        by_severity = {}
        for finding in self.validator.findings:
            sev = finding.severity.value
            by_severity[sev] = by_severity.get(sev, 0) + 1
            
        return {
            'total_files_scanned': self.validator.files_checked,
            'total_findings': self.validator.total_findings,
            'findings_by_severity': by_severity,
            'critical_count': len([f for f in self.validator.findings if f.severity == Severity.CRITICAL])
        }

    def print_report(self) -> None:
        """Print a formatted validation report."""
        summary = self.get_summary()
        
        print("=" * 60)
        print("CONFIGURATION SECURITY VALIDATION REPORT")
        print("=" * 60)
        
        # Overall status
        if summary['critical_count'] > 0:
            status = "⚠️  CRITICAL ISSUES DETECTED"
            print(f"\n{status}")
        elif summary['total_findings'] > 0:
            status = "⚡  ISSUES FOUND"
            print(f"\n{status}")
        else:
            status = "✅ PASSED - No security issues found"
            print(f"\n{status}")

        # Findings by severity
        print("\n" + "-" * 60)
        for sev in [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]:
            count = summary['findings_by_severity'].get(sev.value, 0)
            if count > 0:
                emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}[sev.value]
                print(f"  {emoji} {sev.value}: {count}")

        # Detailed findings
        if self.validator.findings:
            print("\n" + "-" * 60)
            print("DETAILED FINDINGS:")
            
            for finding in sorted(self.validator.findings, 
                                  key=lambda x: (x.severity == Severity.CRITICAL, x.severity))[:20]:
                emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "🔵"}[finding.severity.value]
                print(f"\n  {emoji} [{finding.severity.value}]")
                print(f"    File:   {finding.file_path}")
                if finding.line_number:
                    print(f"    Line:   {finding.line_number}")
                print(f"    Message: {finding.message}")
                if finding.code_snippet:
                    print(f"    Code:    {finding.code_snippet}")
                if finding.remediation:
                    print(f"    Fix:     {finding.remediation}")


def main():
    """Main entry point."""
    validator = ConfigValidator()
    validator.scan_directory()
    validator.print_report()
    
    return validator.validator


if __name__ == "__main__":
    result = main()
```