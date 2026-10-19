"""
@module core.env_settings
@purpose Read operator-tunable numeric settings from environment variables.
"""

import logging
import math
import os

logger = logging.getLogger(__name__)


def env_seconds(name: str, default: float) -> float:
    """
    @summary Read a positive number of seconds from an environment variable.
    @inputs name: environment variable, default: value used when unset or invalid
    @outputs float number of seconds
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s is not a number; using default %g", name, default)
        return default
    if not math.isfinite(value) or value <= 0:
        logger.warning("%s must be a positive finite number; using default %g", name, default)
        return default
    return value


def env_int(name: str, default: int) -> int:
    """
    @summary Read a non-negative integer from an environment variable (0 is allowed).
    @inputs name: environment variable, default: value used when unset or invalid
    @outputs int count/limit, 0 meaningful
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s is not an integer; using default %d", name, default)
        return default
    if value < 0:
        logger.warning("%s must be a non-negative integer; using default %d", name, default)
        return default
    return value
