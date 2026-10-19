from strata.eval.structured_eval import _extract_choice_letter, _grade_response


def _assert_extract(text: str, expected: str) -> None:
    assert _extract_choice_letter(text) == expected


def test_bare_letters() -> None:
    _assert_extract("C", "C")
    _assert_extract("c", "C")
    _assert_extract(" (B) ", "B")
    _assert_extract("B.", "B")
    _assert_extract("Option D", "D")


def test_article_before_trailing_letter() -> None:
    _assert_extract("Strata is a manual system.\n\nB", "B")


def test_article_only_no_answer() -> None:
    _assert_extract("It is a manual system.", "")


def test_option_headings_then_final_marker() -> None:
    _assert_extract("**Option A:** wrong because...\n**Option C:** right\n\n# Final Answer: C", "C")


def test_trailing_marker_after_explanation() -> None:
    _assert_extract("Long explanation about a thing.\n\n**Answer: C**", "C")


def test_marker_on_next_line() -> None:
    _assert_extract("## Final Answer\n\n**C**", "C")


def test_prose_marker_with_bold_letter() -> None:
    _assert_extract("The correct answer is **D** - deterministic preprocessing...", "D")


def test_last_marker_wins() -> None:
    _assert_extract("The answer is B? No. Final answer: C", "C")


def test_lowercase_letter_after_marker() -> None:
    _assert_extract("answer: c", "C")
    _assert_extract("the answer is (b).", "B")


def test_marker_followed_by_word_does_not_match() -> None:
    _assert_extract("The answer is a manual system", "")
    _assert_extract("Answer: Cats are mammals", "")


def test_leading_letter_forms() -> None:
    _assert_extract("B) Because the queue is empty", "B")
    _assert_extract("C. The scheduler", "C")
    _assert_extract("A manual system is best", "")
    _assert_extract("A) Spawn autonomous tasks", "A")


def test_grade_response_contract() -> None:
    assert _grade_response("Strata is a manual system.\n\nB", "B", "choice") is True
    assert _grade_response("A", "B", "choice") is False
    assert _grade_response("42", "42", "exact_match") is True


def test_word_containing_marker_is_not_a_marker() -> None:
    # "\b" anchors the marker phrase: "reanswer" style substrings must not count.
    _assert_extract("I would reanswerB later", "")


def test_recorded_bench_shapes() -> None:
    # Shapes taken from the October 19, 2026 raw-model bench responses.
    _assert_extract("Strata was designed as a manual system ... rather than an automatic one.\n\nB", "B")
    _assert_extract("**Option D: .knowledge/specs/project_spec.md**\n- Also a spec\n\n# Final Answer: C", "C")
    _assert_extract("**Option C** (\"Supervise\") accurately describes it.\n\n## Final Answer\n\n**C**", "C")


def test_letter_first_then_explanation() -> None:
    # Recorded harness response shape: the bare letter, then a justification
    # that mentions the article "a" and no marker phrase.
    _assert_extract("**C**\n\nThe main purpose is to build a loop where a weaker model improves.", "C")
