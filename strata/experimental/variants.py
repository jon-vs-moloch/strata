"""
@module experimental.variants
@purpose Version, attribute, and rate mutable prompt/parameter bundles.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional


VARIANT_INDEX_KEY = "variant_registry_index"
VARIANT_INDEX_DESCRIPTION = "Registry of versioned prompt, parameter, and model bundle variants."
VARIANT_ITEM_PREFIX = "variant_registry:item"
VARIANT_RATINGS_KEY = "variant_registry_ratings"
VARIANT_MATCHUPS_KEY = "variant_registry_matchups"
OPERATIONAL_VARIANT_POLICY_KEY = "variant_registry_operational_policy"
MAX_VARIANT_INDEX = 400
MAX_VARIANT_MATCHUPS = 600
DEFAULT_RATING = 1500.0
DEFAULT_K = 24.0
DEFAULT_OPERATIONAL_VARIANT_POLICY = {
    "min_pool_size_for_pruning": 5,
    "drop_bottom_count": 1,
    "keep_top_k": 3,
    "max_synthesis_variants": 2,
    "max_stage_variants": 3,
    "exploit_top_n": 3,
    "exploit_sample_count": 2,
    "explore_pair_count": 2,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonicalize(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value.keys(), key=str)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def canonical_variant_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return _canonicalize(dict(payload or {}))


def variant_signature(kind: str, payload: Dict[str, Any]) -> str:
    canonical = {
        "kind": str(kind or "").strip(),
        "payload": canonical_variant_payload(payload),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def _variant_item_key(variant_id: str) -> str:
    return f"{VARIANT_ITEM_PREFIX}:{variant_id}"


def _load_variant_index(storage) -> list[Dict[str, Any]]:
    rows = storage.parameters.peek_parameter(VARIANT_INDEX_KEY, default_value=[]) or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _store_variant_index(storage, rows: list[Dict[str, Any]]) -> None:
    storage.parameters.set_parameter(
        VARIANT_INDEX_KEY,
        rows[-MAX_VARIANT_INDEX:],
        description=VARIANT_INDEX_DESCRIPTION,
    )


def get_variant(storage, variant_id: str) -> Optional[Dict[str, Any]]:
    if not variant_id:
        return None
    payload = storage.parameters.peek_parameter(_variant_item_key(variant_id), default_value=None)
    return dict(payload) if isinstance(payload, dict) else None


def build_stage_scope(*, component: str, process: str = "generic", step: str = "default") -> str:
    return ".".join(
        [
            str(component or "generic").strip() or "generic",
            str(process or "generic").strip() or "generic",
            str(step or "default").strip() or "default",
        ]
    )


def get_operational_variant_policy(storage) -> Dict[str, int]:
    payload = storage.parameters.peek_parameter(
        OPERATIONAL_VARIANT_POLICY_KEY,
        default_value=DEFAULT_OPERATIONAL_VARIANT_POLICY,
    ) or DEFAULT_OPERATIONAL_VARIANT_POLICY
    merged = dict(DEFAULT_OPERATIONAL_VARIANT_POLICY)
    if isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, int):
                merged[key] = value
    return merged


def _metadata_status(payload: Dict[str, Any]) -> str:
    metadata = dict(payload.get("metadata") or {})
    return str(metadata.get("status") or "").strip().lower()


def variant_is_downstream_validated(payload: Dict[str, Any]) -> bool:
    metadata = dict(payload.get("metadata") or {})
    if bool(metadata.get("downstream_validated")):
        return True
    return _metadata_status(payload) in {"validated", "active", "favored", "default"}


def variant_is_retired(payload: Dict[str, Any]) -> bool:
    return _metadata_status(payload) in {"retired", "frozen", "rejected"}


def classify_pool_pruning(storage, *, pool_size: int) -> Dict[str, int]:
    policy = get_operational_variant_policy(storage)
    safe_pool_size = max(0, int(pool_size or 0))
    if safe_pool_size < int(policy.get("min_pool_size_for_pruning", 5) or 5):
        drop_count = 0
    else:
        drop_count = min(
            int(policy.get("drop_bottom_count", 1) or 1),
            max(0, safe_pool_size - max(1, int(policy.get("keep_top_k", 3) or 3))),
        )
    keep_count = max(0, safe_pool_size - drop_count)
    return {
        "pool_size": safe_pool_size,
        "drop_count": drop_count,
        "keep_count": keep_count,
    }


def list_variants_for_scope(
    storage,
    *,
    family: str,
    stage_scope: str,
    domain: Optional[str] = None,
    include_generic: bool = True,
    limit: Optional[int] = None,
) -> list[Dict[str, Any]]:
    family_key = str(family or "").strip()
    scope_key = str(stage_scope or "").strip()
    index = _load_variant_index(storage)
    ratings = _load_ratings(storage)
    domain_bucket = {}
    if domain:
        domain_bucket = dict((ratings.get("by_domain") or {}).get(str(domain), {}) or {})
    candidates: list[Dict[str, Any]] = []
    generic_prefix = ""
    scope_parts = scope_key.split(".")
    if len(scope_parts) >= 1:
        generic_prefix = f"{scope_parts[0]}."
    for row in index:
        if str(row.get("family") or "").strip() != family_key:
            continue
        payload = get_variant(storage, str(row.get("variant_id") or "")) or dict(row)
        metadata = dict(payload.get("metadata") or {})
        item_scope = str(metadata.get("stage_scope") or "").strip()
        if item_scope == scope_key:
            scope_match = "exact"
        elif include_generic and item_scope and generic_prefix and item_scope.startswith(generic_prefix):
            scope_match = "generic"
        elif include_generic and item_scope == "generic":
            scope_match = "generic"
        else:
            continue
        rating_entry = dict(domain_bucket.get(str(payload.get("variant_id") or "")) or {})
        payload["scope_match"] = scope_match
        payload["domain_rating"] = float(rating_entry.get("rating", DEFAULT_RATING) or DEFAULT_RATING)
        payload["domain_matches"] = int(rating_entry.get("matches", 0) or 0)
        payload["downstream_validated"] = variant_is_downstream_validated(payload)
        payload["retired"] = variant_is_retired(payload)
        candidates.append(payload)
    candidates = [item for item in candidates if not item.get("retired")]
    candidates.sort(
        key=lambda item: (
            0 if item.get("scope_match") == "exact" else 1,
            -float(item.get("domain_rating", DEFAULT_RATING) or DEFAULT_RATING),
            0 if item.get("downstream_validated") else 1,
            -int(item.get("use_count", 0) or 0),
            str(item.get("created_at") or ""),
        )
    )
    hard_limit = int(limit or get_operational_variant_policy(storage).get("max_stage_variants", 3) or 3)
    return candidates[: max(1, hard_limit)] if candidates else []


def _weight_from_rating(rating: float, baseline: float) -> float:
    centered = max(-200.0, min(200.0, float(rating or DEFAULT_RATING) - float(baseline or DEFAULT_RATING)))
    return max(0.05, 1.0 + centered / 400.0)


def _select_exploit_pool(
    variants: list[Dict[str, Any]],
    *,
    default_variant_id: str,
    top_n: int,
    sample_count: int,
) -> list[Dict[str, Any]]:
    validated = [
        item for item in variants
        if item.get("downstream_validated") and str(item.get("variant_id") or "") != default_variant_id
    ]
    validated.sort(key=lambda item: -float(item.get("domain_rating", DEFAULT_RATING) or DEFAULT_RATING))
    pool = validated[: max(0, int(top_n or 0))]
    if not pool:
        return []
    default_rating = float(pool[0].get("domain_rating", DEFAULT_RATING) or DEFAULT_RATING)
    pool.sort(
        key=lambda item: (
            -_weight_from_rating(float(item.get("domain_rating", DEFAULT_RATING) or DEFAULT_RATING), default_rating),
            int(item.get("domain_matches", 0) or 0),
        )
    )
    return pool[: max(0, int(sample_count or 0))]


def _pair_information_score(left: Dict[str, Any], right: Dict[str, Any], *, default_variant_id: str) -> float:
    left_rating = float(left.get("domain_rating", DEFAULT_RATING) or DEFAULT_RATING)
    right_rating = float(right.get("domain_rating", DEFAULT_RATING) or DEFAULT_RATING)
    closeness = 1.0 / (1.0 + abs(left_rating - right_rating) / 50.0)
    coverage = 1.0 / (1.0 + int(left.get("domain_matches", 0) or 0) + int(right.get("domain_matches", 0) or 0))
    default_pressure = 0.5 if default_variant_id in {str(left.get("variant_id") or ""), str(right.get("variant_id") or "")} else 0.0
    novelty = 0.25 if (not left.get("downstream_validated") or not right.get("downstream_validated")) else 0.0
    return closeness + coverage + default_pressure + novelty


def _select_explore_pair(
    variants: list[Dict[str, Any]],
    *,
    default_variant_id: str,
) -> list[Dict[str, Any]]:
    if len(variants) < 2:
        return []
    best_pair: list[Dict[str, Any]] = []
    best_score = -1.0
    for idx, left in enumerate(variants):
        for right in variants[idx + 1:]:
            score = _pair_information_score(left, right, default_variant_id=default_variant_id)
            if score > best_score:
                best_score = score
                best_pair = [left, right]
    return best_pair


def build_variant_execution_plan(
    storage,
    *,
    family: str,
    stage_scope: str,
    domain: str,
    safe_mode: bool = False,
    include_generic: bool = True,
) -> Dict[str, Any]:
    policy = get_operational_variant_policy(storage)
    variants = list_variants_for_scope(
        storage,
        family=family,
        stage_scope=stage_scope,
        domain=domain,
        include_generic=include_generic,
        limit=max(
            int(policy.get("max_stage_variants", 3) or 3),
            int(policy.get("exploit_top_n", 3) or 3) + 2,
        ),
    )
    validated = [item for item in variants if item.get("downstream_validated")]
    default_variant = validated[0] if validated else (variants[0] if variants else None)
    if not default_variant:
        return {
            "mode": "safe" if safe_mode else "adaptive",
            "default": None,
            "exploit_pool": [],
            "explore_pair": [],
            "selected_variants": [],
        }
    default_variant_id = str(default_variant.get("variant_id") or "")
    if safe_mode:
        return {
            "mode": "safe",
            "default": default_variant,
            "exploit_pool": [],
            "explore_pair": [],
            "selected_variants": [default_variant],
        }
    exploit_pool = _select_exploit_pool(
        variants,
        default_variant_id=default_variant_id,
        top_n=int(policy.get("exploit_top_n", 3) or 3),
        sample_count=int(policy.get("exploit_sample_count", 2) or 2),
    )
    explore_pair = _select_explore_pair(variants, default_variant_id=default_variant_id)
    selected: list[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in [default_variant, *exploit_pool, *explore_pair]:
        variant_id = str((item or {}).get("variant_id") or "")
        if not variant_id or variant_id in seen:
            continue
        selected.append(item)
        seen.add(variant_id)
    return {
        "mode": "adaptive",
        "default": default_variant,
        "exploit_pool": exploit_pool,
        "explore_pair": explore_pair,
        "selected_variants": selected,
    }


def record_ranked_variant_matchups(
    storage,
    *,
    domain: str,
    ranked_variant_ids: list[str],
    context: Optional[Dict[str, Any]] = None,
) -> list[Dict[str, Any]]:
    ordered = [str(item).strip() for item in ranked_variant_ids if str(item).strip()]
    snapshots: list[Dict[str, Any]] = []
    for left_index, left_variant_id in enumerate(ordered):
        for right_variant_id in ordered[left_index + 1:]:
            snapshot = record_variant_matchup(
                storage,
                domain=domain,
                left_variant_id=left_variant_id,
                right_variant_id=right_variant_id,
                left_score=1.0,
                context=dict(context or {}),
            )
            if snapshot:
                snapshots.append(snapshot)
    return snapshots


def ensure_variant(
    storage,
    *,
    kind: str,
    payload: Dict[str, Any],
    label: Optional[str] = None,
    family: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    canonical_payload = canonical_variant_payload(payload)
    signature = variant_signature(kind, canonical_payload)
    index = _load_variant_index(storage)
    existing = next((row for row in index if str(row.get("signature")) == signature), None)
    timestamp = _now()
    if existing:
        variant_id = str(existing.get("variant_id"))
        current = get_variant(storage, variant_id) or {}
        current["last_used_at"] = timestamp
        current["use_count"] = int(current.get("use_count", 0) or 0) + 1
        if label and not current.get("label"):
            current["label"] = label
        if family and not current.get("family"):
            current["family"] = family
        if metadata:
            merged = dict(current.get("metadata") or {})
            merged.update(dict(metadata))
            current["metadata"] = merged
        storage.parameters.set_parameter(
            _variant_item_key(variant_id),
            current,
            description="Variant registry item.",
        )
        for row in index:
            if str(row.get("variant_id")) == variant_id:
                row["last_used_at"] = timestamp
                row["use_count"] = int(row.get("use_count", 0) or 0) + 1
                if label and not row.get("label"):
                    row["label"] = label
                if family and not row.get("family"):
                    row["family"] = family
        _store_variant_index(storage, index)
        return current

    short_sig = signature[:12]
    variant_id = f"{str(kind).strip() or 'variant'}_{short_sig}"
    record = {
        "variant_id": variant_id,
        "kind": str(kind).strip() or "variant",
        "family": str(family or kind or "variant").strip(),
        "label": str(label or variant_id).strip(),
        "signature": signature,
        "payload": canonical_payload,
        "metadata": dict(metadata or {}),
        "created_at": timestamp,
        "last_used_at": timestamp,
        "use_count": 1,
    }
    index.append(
        {
            "variant_id": variant_id,
            "kind": record["kind"],
            "family": record["family"],
            "label": record["label"],
            "signature": signature,
            "created_at": timestamp,
            "last_used_at": timestamp,
            "use_count": 1,
        }
    )
    storage.parameters.set_parameter(
        _variant_item_key(variant_id),
        record,
        description="Variant registry item.",
    )
    _store_variant_index(storage, index)
    return record


def _load_ratings(storage) -> Dict[str, Any]:
    payload = storage.parameters.peek_parameter(
        VARIANT_RATINGS_KEY,
        default_value={"by_domain": {}},
    ) or {"by_domain": {}}
    payload.setdefault("by_domain", {})
    return payload


def _expected_score(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def record_variant_matchup(
    storage,
    *,
    domain: str,
    left_variant_id: str,
    right_variant_id: str,
    left_score: float,
    context: Optional[Dict[str, Any]] = None,
    k_factor: float = DEFAULT_K,
) -> Dict[str, Any]:
    if not left_variant_id or not right_variant_id or left_variant_id == right_variant_id:
        return {}
    domain_key = str(domain or "global").strip() or "global"
    ratings = _load_ratings(storage)
    by_domain = ratings.setdefault("by_domain", {})
    domain_bucket = dict(by_domain.get(domain_key) or {})

    def _entry(variant_id: str) -> Dict[str, Any]:
        return dict(domain_bucket.get(variant_id) or {"rating": DEFAULT_RATING, "matches": 0})

    left_entry = _entry(left_variant_id)
    right_entry = _entry(right_variant_id)
    left_rating = float(left_entry.get("rating", DEFAULT_RATING) or DEFAULT_RATING)
    right_rating = float(right_entry.get("rating", DEFAULT_RATING) or DEFAULT_RATING)
    expected_left = _expected_score(left_rating, right_rating)
    expected_right = _expected_score(right_rating, left_rating)
    right_score = 1.0 - float(left_score)
    new_left = left_rating + k_factor * (float(left_score) - expected_left)
    new_right = right_rating + k_factor * (right_score - expected_right)

    timestamp = _now()
    left_entry.update({"rating": round(new_left, 2), "matches": int(left_entry.get("matches", 0) or 0) + 1, "last_updated": timestamp})
    right_entry.update({"rating": round(new_right, 2), "matches": int(right_entry.get("matches", 0) or 0) + 1, "last_updated": timestamp})
    domain_bucket[left_variant_id] = left_entry
    domain_bucket[right_variant_id] = right_entry
    by_domain[domain_key] = domain_bucket
    storage.parameters.set_parameter(
        VARIANT_RATINGS_KEY,
        ratings,
        description="Domain-scoped Elo ratings for mutation variants.",
    )

    matchups = list(storage.parameters.peek_parameter(VARIANT_MATCHUPS_KEY, default_value=[]) or [])
    matchups.append(
        {
            "domain": domain_key,
            "left_variant_id": left_variant_id,
            "right_variant_id": right_variant_id,
            "left_score": float(left_score),
            "left_rating_before": round(left_rating, 2),
            "right_rating_before": round(right_rating, 2),
            "left_rating_after": round(new_left, 2),
            "right_rating_after": round(new_right, 2),
            "context": dict(context or {}),
            "recorded_at": timestamp,
        }
    )
    storage.parameters.set_parameter(
        VARIANT_MATCHUPS_KEY,
        matchups[-MAX_VARIANT_MATCHUPS:],
        description="Recent head-to-head variant matchups.",
    )
    return {
        "domain": domain_key,
        "left": {"variant_id": left_variant_id, **left_entry},
        "right": {"variant_id": right_variant_id, **right_entry},
        "expected_left": round(expected_left, 4),
    }


def get_variant_rating_snapshot(storage) -> Dict[str, Any]:
    ratings = _load_ratings(storage)
    matchups = list(storage.parameters.peek_parameter(VARIANT_MATCHUPS_KEY, default_value=[]) or [])
    index = _load_variant_index(storage)
    return {
        "index_size": len(index),
        "variants": index[-100:],
        "ratings": ratings,
        "recent_matchups": matchups[-100:],
    }
