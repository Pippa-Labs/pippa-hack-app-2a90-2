"""Tests for MechDojo, through LocalRunner, with a fixed `now`."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pippa_agentapp_sdk import LocalRunner

from app import App, MissingCode, MissingNumber, UnknownCode, UnknownTopic

NOW = datetime(2026, 11, 20, 14, 30, 7, tzinfo=timezone.utc)
BANK = json.loads(Path(__file__).with_name("questions.json").read_text("utf-8"))
BY_ID = {q["id"]: q for q in BANK}
HOW_TO_ANSWER = (
    "Reply with your number and the question code, for example: "
    "my answer to TH-03 is 42"
)
PREFIX = {
    "thermodynamics": "TH",
    "fluid mechanics": "FL",
    "solid mechanics": "SM",
    "dynamics": "DY",
}


def run(outcome: str, inputs: dict, *, explicit: bool = False, now=NOW):
    return LocalRunner(App()).call(
        outcome, inputs, granted_secrets={}, now=now, request_id="t1",
        explicit=explicit,
    ).result


# ── The manifest ────────────────────────────────────────────────────────────


def test_the_manifest_is_valid_and_has_both_outcomes():
    app = App()
    assert app.manifest.app_id == "hack.app_2a90_2"
    assert [o.id for o in app.manifest.outcomes] == ["question.get", "answer.check"]


# ── question.get ────────────────────────────────────────────────────────────


def test_fluids_gives_a_fluid_mechanics_question_and_no_answer():
    result = run("question.get", {"topic": "fluids"})
    assert result.data["topic"] == "fluid mechanics"
    assert result.data["question_id"].startswith("FL-")
    assert set(result.data) == {"question_id", "topic", "question", "how_to_answer"}
    assert "answer" not in result.data
    assert result.data["how_to_answer"] == HOW_TO_ANSWER
    assert result.narrative == (
        f"Here is a fluid mechanics question, code {result.data['question_id']}."
    )


@pytest.mark.parametrize("inputs", [{}, {"topic": ""}])
def test_no_topic_gives_a_question(inputs):
    result = run("question.get", inputs)
    assert result.data["question_id"] in BY_ID
    assert result.data["question"] == BY_ID[result.data["question_id"]]["question"]


@pytest.mark.parametrize("words, topic", [
    ("thermo", "thermodynamics"),
    ("thermodynamics", "thermodynamics"),
    ("beams", "solid mechanics"),
    ("torsion", "solid mechanics"),
    ("vibration", "dynamics"),
    ("fluid dynamics", "fluid mechanics"),
    ("Bernoulli", "fluid mechanics"),
])
def test_topics_match_loosely(words, topic):
    assert run("question.get", {"topic": words}).data["topic"] == topic


def test_any_topic_words_give_a_question():
    assert run("question.get", {"topic": "mechanical engineering"}).data["question_id"] in BY_ID


def test_the_question_changes_with_the_time():
    later = datetime(2026, 11, 20, 14, 30, 8, tzinfo=timezone.utc)
    first = run("question.get", {"topic": "dynamics"})
    second = run("question.get", {"topic": "dynamics"}, now=later)
    assert first.data["question_id"] != second.data["question_id"]


def test_an_unknown_topic_says_which_topics_exist():
    with pytest.raises(UnknownTopic) as failure:
        run("question.get", {"topic": "basket weaving"})
    assert "thermodynamics, fluid mechanics, solid mechanics and dynamics" in (
        failure.value.user_message
    )


# ── answer.check ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("question_id", sorted(BY_ID))
def test_the_stored_answer_is_marked_correct(question_id):
    stored = float(BY_ID[question_id]["answer"])
    result = run("answer.check", {"question_id": question_id, "answer": stored})
    assert result.data["correct"] is True
    assert result.narrative.startswith("Correct. The answer is ")
    assert result.data["worked_solution"] == BY_ID[question_id]["solution"]


def test_a_value_inside_tolerance_is_correct():
    result = run("answer.check", {"question_id": "FL-02", "answer": 3.9})
    assert result.data == {
        "correct": True,
        "your_answer": "3.9 m/s",
        "right_answer": "3.84 m/s",
        "worked_solution": BY_ID["FL-02"]["solution"],
    }
    assert result.narrative == "Correct. The answer is 3.84 m/s."


def test_a_value_outside_tolerance_is_wrong():
    result = run("answer.check", {"question_id": "FL-02", "answer": 4.0})
    assert result.data["correct"] is False
    assert result.data["your_answer"] == "4 m/s"
    assert result.narrative == "Not quite. The answer is 3.84 m/s."


def test_units_read_naturally():
    assert run("answer.check", {"question_id": "TH-03", "answer": 63.4}).data[
        "right_answer"] == "63.41%"
    assert run("answer.check", {"question_id": "FL-04", "answer": 1193.0}).data[
        "right_answer"] == "1193"
    assert run("answer.check", {"question_id": "FL-03", "answer": 164.0}).data[
        "right_answer"] == "164 kPa"


@pytest.mark.parametrize("written", ["fl2", "FL 02", "fl-02", "FL-02"])
def test_codes_are_read_loosely(written):
    result = run("answer.check", {"question_id": written, "answer": 3.84})
    assert result.data["correct"] is True


def test_an_unknown_code_is_named():
    with pytest.raises(UnknownCode) as failure:
        run("answer.check", {"question_id": "ZZ-99", "answer": 4.0})
    assert failure.value.user_message == "I do not have a question with the code ZZ-99."


@pytest.mark.parametrize("inputs", [{"answer": 3.84}, {"question_id": "", "answer": 3.84}])
def test_a_missing_code_says_how_to_answer(inputs):
    with pytest.raises(MissingCode) as failure:
        run("answer.check", inputs)
    assert failure.value.user_message == (
        "Tell me which question you are answering, with its code, for "
        "example: my answer to TH-03 is 42"
    )


def test_a_missing_number_says_how_to_answer():
    with pytest.raises(MissingNumber) as failure:
        run("answer.check", {"question_id": "FL-02"})
    assert failure.value.user_message == (
        "Tell me your answer to FL-02 as a number, for example: "
        "my answer to FL-02 is 42"
    )


def test_picked_by_name_it_answers_and_refuses_as_usual():
    picked = run("answer.check", {"question_id": "FL-02", "answer": 3.84}, explicit=True)
    assert picked.data == run("answer.check", {"question_id": "FL-02", "answer": 3.84}).data
    with pytest.raises(MissingNumber):
        run("answer.check", {"question_id": "FL-02"}, explicit=True)


# ── The question bank ───────────────────────────────────────────────────────


def test_the_bank_has_six_questions_on_each_of_four_topics():
    assert len(BANK) == 24
    assert len(BY_ID) == 24
    for topic, prefix in PREFIX.items():
        ids = sorted(q["id"] for q in BANK if q["topic"] == topic)
        assert ids == [f"{prefix}-{n:02d}" for n in range(1, 7)]
    for q in BANK:
        assert set(q) == {"id", "topic", "question", "answer", "unit",
                          "tolerance_percent", "solution"}
        assert isinstance(q["answer"], (int, float))
        assert q["tolerance_percent"] == 2
        assert 2 <= len(q["solution"].splitlines()) <= 4


#: Every answer worked out again from the values in its question.
RECOMPUTE = {
    "TH-01": lambda: 45 - (-12),                                        # kJ
    "TH-02": lambda: 0.8 * 287 * 350 / 0.25 / 1000,                     # kPa
    "TH-03": lambda: (1 - 300 / 820) * 100,                             # %
    "TH-04": lambda: 2 * 4.18 * math.log(350 / 290),                    # kJ/K
    "TH-05": lambda: 0.5 * 0.287 * 400 * math.log(0.6 / 0.2),           # kJ
    "TH-06": lambda: (1 - 1 / 9 ** (1.4 - 1)) * 100,                    # %
    "FL-01": lambda: 1025 * 9.81 * 7.5 / 1000,                          # kPa
    "FL-02": lambda: 1.5 * (80 / 50) ** 2,                              # m/s
    "FL-03": lambda: (180_000 + 0.5 * 1000 * (2 ** 2 - 6 ** 2)) / 1000, # kPa
    "FL-04": lambda: 870 * 1.2 * 0.040 / 0.035,                         # -
    "FL-05": lambda: 0.02 * (120 / 0.1) * 2.5 ** 2 / (2 * 9.81),        # m
    "FL-06": lambda: math.sqrt(2 * 9.81 * 3.2),                         # m/s
    "SM-01": lambda: 50_000 / (math.pi * 0.020 ** 2 / 4) / 1e6,         # MPa
    "SM-02": lambda: 28_000 * 1.5 / (400e-6 * 70e9) * 1000,             # mm
    "SM-03": lambda: 6 * 9000 / (0.060 * 0.150 ** 2) / 1e6,             # MPa
    "SM-04": lambda: 16 * 1200 / (math.pi * 0.050 ** 3) / 1e6,          # MPa
    "SM-05": lambda: 5000 * 2 ** 3 / (3 * 200e9 * 8e-6) * 1000,         # mm
    "SM-06": lambda: 12_000 * 4 ** 3 / (48 * 200e9 * 2.5e-5) * 1000,    # mm
    "DY-01": lambda: (5 + 25) * 8 / 2,                                  # m
    "DY-02": lambda: 12 * math.sqrt(2 * 45 / 9.81),                     # m
    "DY-03": lambda: 0.15 * math.sqrt(800 / 0.5),                       # m/s
    "DY-04": lambda: 1200 * 15 / (1200 + 800),                          # m/s
    "DY-05": lambda: math.sqrt(4000 / 2.5) / (2 * math.pi),             # Hz
    "DY-06": lambda: 0.5 * 3.2 * (600 * 2 * math.pi / 60) ** 2 / 1000,  # kJ
}


def test_every_question_is_recomputed():
    assert set(RECOMPUTE) == set(BY_ID)


@pytest.mark.parametrize("question_id", sorted(RECOMPUTE))
def test_every_stored_answer_equals_the_recomputed_value(question_id):
    stored = float(BY_ID[question_id]["answer"])
    # Stored to four significant figures, so within 0.1%: far tighter than
    # the 2% a student is allowed.
    assert math.isclose(stored, RECOMPUTE[question_id](), rel_tol=1e-3)
