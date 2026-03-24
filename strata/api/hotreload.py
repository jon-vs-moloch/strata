"""
@module api.hotreload
@purpose Safe promotion of experimental module files to live, with validation and auto-rollback.
@owns file discovery, validation pipeline, snapshot/swap/rollback, server restart signal
@does_not_own server process management beyond SIGHUP/exec, business logic
@key_exports HotReloader, ValidationResult, PromotionResult
"""

import os
import sys
import signal
import shutil
import logging
import py_compile
import subprocess
import asyncio
import glob
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EXPERIMENTAL_SUFFIX = ".experimental.py"
BACKUP_SUFFIX = ".live.bak"
HEALTH_ENDPOINT = "http://localhost:8000/admin/test"
HEALTH_TIMEOUT_S = 15


@dataclass
class ValidationResult:
    passed: bool
    stages: dict = field(default_factory=dict)  # stage_name → {ok, message}
    error: Optional[str] = None


@dataclass
class PromotionResult:
    success: bool
    module: str
    validation: Optional[ValidationResult] = None
    rolled_back: bool = False
    message: str = ""


class HotReloader:
    """
    @summary Manages safe live/experimental file promotion with rollback.
    @inputs base_dir: project root path
    @invariants Live files are never replaced without a successful validation pass.
    """

    def __init__(self, base_dir: str):
        self.base_dir = Path(base_dir)

    # ── Discovery ──────────────────────────────────────────────────────────────

    def list_experimental(self) -> list[dict]:
        """
        @summary Find all .experimental.py files under base_dir.
        @outputs list of {module, experimental_path, live_path, has_backup}
        """
        results = []
        pattern = str(self.base_dir / "**" / f"*{EXPERIMENTAL_SUFFIX}")
        for exp_path in glob.glob(pattern, recursive=True):
            exp = Path(exp_path)
            # Derive live path: strip .experimental and replace .py
            live_name = exp.name.replace(EXPERIMENTAL_SUFFIX, ".py")
            live_path = exp.parent / live_name
            bak_path = exp.parent / (live_name + BACKUP_SUFFIX)
            # Compute module dotted name relative to base_dir
            rel = exp.relative_to(self.base_dir)
            module = str(rel).replace(EXPERIMENTAL_SUFFIX, "").replace("/", ".").replace("\\", ".")
            results.append({
                "module": module,
                "experimental_path": str(exp),
                "live_path": str(live_path),
                "has_backup": bak_path.exists(),
                "backup_path": str(bak_path),
            })
        return results

    # ── Validation ─────────────────────────────────────────────────────────────

    def validate(self, experimental_path: str) -> ValidationResult:
        """
        @summary Run all validation stages on an experimental file.
        @inputs experimental_path: absolute path to the .experimental.py file
        @outputs ValidationResult with per-stage detail
        """
        stages = {}

        # Stage 1: Syntax check
        try:
            py_compile.compile(experimental_path, doraise=True)
            stages["syntax"] = {"ok": True, "message": "Syntax OK"}
        except py_compile.PyCompileError as e:
            stages["syntax"] = {"ok": False, "message": str(e)}
            return ValidationResult(passed=False, stages=stages, error=f"Syntax error: {e}")

        # Stage 2: Import check in isolated subprocess
        check_script = (
            f"import importlib.util, sys; "
            f"spec = importlib.util.spec_from_file_location('_check', {repr(experimental_path)}); "
            f"mod = importlib.util.module_from_spec(spec); "
            f"spec.loader.exec_module(mod); "
            f"print('import_ok')"
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.base_dir)
        try:
            result = subprocess.run(
                [sys.executable, "-c", check_script],
                capture_output=True, text=True, timeout=10, env=env
            )
            if result.returncode == 0 and "import_ok" in result.stdout:
                stages["import"] = {"ok": True, "message": "Import OK"}
            else:
                err = result.stderr.strip() or result.stdout.strip()
                stages["import"] = {"ok": False, "message": err}
                return ValidationResult(passed=False, stages=stages, error=f"Import failed: {err}")
        except subprocess.TimeoutExpired:
            stages["import"] = {"ok": False, "message": "Import check timed out (10s)"}
            return ValidationResult(passed=False, stages=stages, error="Import timed out")

        return ValidationResult(passed=True, stages=stages)

    # ── Promotion ──────────────────────────────────────────────────────────────

    async def promote(self, module: str) -> PromotionResult:
        """
        @summary Full promotion pipeline: validate → snapshot → swap → reload → health-check → rollback on failure.
        @inputs module: dotted module name matching list_experimental() output
        @outputs PromotionResult
        """
        # Find the matching entry
        candidates = [e for e in self.list_experimental() if e["module"] == module]
        if not candidates:
            return PromotionResult(success=False, module=module, message="Module not found in experimental files")

        entry = candidates[0]
        exp_path = entry["experimental_path"]
        live_path = entry["live_path"]
        bak_path = entry["backup_path"]

        # 1. Validate
        validation = self.validate(exp_path)
        if not validation.passed:
            return PromotionResult(
                success=False, module=module,
                validation=validation,
                message=f"Validation failed: {validation.error}"
            )

        # 2. Snapshot live → bak (only if live exists)
        if Path(live_path).exists():
            shutil.copy2(live_path, bak_path)
            logger.info(f"Snapshot: {live_path} → {bak_path}")

        # 3. Promote experimental → live
        shutil.copy2(exp_path, live_path)
        logger.info(f"Promoted: {exp_path} → {live_path}")

        # 4. Trigger graceful reload
        self._signal_reload()

        # 5. Health check with retry (server will be briefly unavailable during restart)
        healthy = await self._wait_healthy()

        if healthy:
            # Clean up the experimental file on success
            try:
                os.remove(exp_path)
            except OSError:
                pass
            return PromotionResult(
                success=True, module=module,
                validation=validation,
                message="Promotion successful — server healthy"
            )
        else:
            # Rollback
            rolled_back = self._rollback(live_path, bak_path)
            if rolled_back:
                self._signal_reload()
            return PromotionResult(
                success=False, module=module,
                validation=validation,
                rolled_back=rolled_back,
                message="Health check failed after promotion — rolled back to backup"
            )

    def rollback(self, module: str) -> PromotionResult:
        """
        @summary Manually restore the .live.bak for a module.
        """
        candidates = [e for e in self.list_experimental() if e["module"] == module]
        if not candidates:
            # Try to find backup even without experimental
            return PromotionResult(success=False, module=module, message="Module entry not found")
        entry = candidates[0]
        rolled = self._rollback(entry["live_path"], entry["backup_path"])
        if rolled:
            self._signal_reload()
        return PromotionResult(
            success=rolled, module=module,
            message="Rolled back and signalled reload" if rolled else "No backup found"
        )

    # ── Internals ──────────────────────────────────────────────────────────────

    def _signal_reload(self):
        """Send SIGHUP to this process — uvicorn --reload picks it up."""
        try:
            os.kill(os.getpid(), signal.SIGHUP)
            logger.info("SIGHUP sent — waiting for uvicorn reload")
        except (OSError, AttributeError):
            # Windows doesn't have SIGHUP; fall back to stdout notification
            logger.warning("SIGHUP unavailable — manual restart required")

    async def _wait_healthy(self) -> bool:
        """Poll /admin/test until it returns 200 or timeout."""
        import httpx
        deadline = asyncio.get_event_loop().time() + HEALTH_TIMEOUT_S
        await asyncio.sleep(2)   # give reload a head start
        while asyncio.get_event_loop().time() < deadline:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(HEALTH_ENDPOINT, timeout=2.0)
                    if resp.status_code == 200:
                        return True
            except Exception:
                pass
            await asyncio.sleep(1)
        return False

    def _rollback(self, live_path: str, bak_path: str) -> bool:
        if not Path(bak_path).exists():
            logger.warning(f"No backup to restore: {bak_path}")
            return False
        shutil.copy2(bak_path, live_path)
        logger.info(f"Rolled back: {bak_path} → {live_path}")
        return True
