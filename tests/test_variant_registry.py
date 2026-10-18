from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from strata.experimental.experiment_runner import ExperimentRunner
from strata.experimental.variants import ensure_variant, get_variant_rating_snapshot, record_variant_matchup
from strata.storage.models import Base
from strata.storage.services.main import StorageManager


def make_storage():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    return StorageManager(session=session)


def test_ensure_variant_dedupes_identical_payloads():
    storage = make_storage()

    first = ensure_variant(
        storage,
        kind="eval_harness_bundle",
        payload={"system_prompt": "alpha", "context_files": ["a.md"]},
        label="baseline",
        family="eval_harness",
    )
    second = ensure_variant(
        storage,
        kind="eval_harness_bundle",
        payload={"context_files": ["a.md"], "system_prompt": "alpha"},
        label="baseline-again",
        family="eval_harness",
    )

    assert first["variant_id"] == second["variant_id"]


def test_record_variant_matchup_updates_domain_rating():
    storage = make_storage()
    left = ensure_variant(storage, kind="eval_harness_bundle", payload={"system_prompt": "a"}, family="eval_harness")
    right = ensure_variant(storage, kind="eval_harness_bundle", payload={"system_prompt": "b"}, family="eval_harness")

    snapshot = record_variant_matchup(
        storage,
        domain="eval_harness_full_eval:bootstrap_mcq_v1",
        left_variant_id=left["variant_id"],
        right_variant_id=right["variant_id"],
        left_score=1.0,
        context={"candidate_change_id": "cand-1"},
    )
    rating_snapshot = get_variant_rating_snapshot(storage)

    assert snapshot["left"]["rating"] > snapshot["right"]["rating"]
    assert rating_snapshot["ratings"]["by_domain"]["eval_harness_full_eval:bootstrap_mcq_v1"][left["variant_id"]]["matches"] == 1


def test_experiment_runner_resolves_eval_and_promotion_policy_variants():
    storage = make_storage()
    runner = ExperimentRunner(storage, model_adapter=None)

    assignment = runner._resolve_eval_variant_pair(
        candidate_change_id="candidate_x",
        baseline_change_id="baseline",
        eval_harness_config_override={"system_prompt": "candidate prompt", "context_files": ["docs/spec/eval-brief.md"]},
        proposal_metadata={"proposer_tier": "agent"},
    )

    assert assignment["candidate_variant_id"].startswith("eval_harness_bundle_")
    assert assignment["baseline_variant_id"].startswith("eval_harness_bundle_")
    assert assignment["candidate_promotion_policy_variant_id"].startswith("promotion_policy_bundle_")
    assert assignment["baseline_promotion_policy_variant_id"].startswith("promotion_policy_bundle_")
