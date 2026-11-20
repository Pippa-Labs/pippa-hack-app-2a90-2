"""MechDojo: mechanical engineering practice questions, and marking.

Two outcomes:

* question.get  - picks a question from questions.json, on the topic the
                  student names (or any topic). Never returns the answer.
* answer.check  - marks the student's number against the stored answer,
                  within the question's tolerance, and returns the worked
                  solution.

No network at all. The app keeps nothing between calls, so the question is
chosen from `call.context.now` (seconds since midnight, modulo the number of
matching questions).
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

from pippa_agentapp_sdk import (
    AgentAppError,
    Call,
    Manifest,
    Result,
    load_manifest_file,
    validate_manifest,
)

HERE = Path(__file__).resolve().parent
MANIFEST_PATH = HERE / "manifest.yaml"
QUESTIONS: list[dict] = json.loads((HERE / "questions.json").read_text("utf-8"))
BY_ID: dict[str, dict] = {q["id"]: q for q in QUESTIONS}

HOW_TO_ANSWER = (
    "Reply with your number and the question code, for example: "
    "my answer to TH-03 is 42"
)

#: Loose topic words, matched at the start of a word. Checked in this order,
#: so "thermodynamics" is thermodynamics (not dynamics) and "fluid dynamics"
#: is fluid mechanics.
TOPIC_WORDS = [
    ("thermodynamics",
     r"thermo|heat|gas|entrop|enthalp|carnot|otto|diesel|rankine|brayton|"
     r"cycle|first law|second law|refrigerat|temperature|piston|isothermal|"
     r"isentropic|adiabatic"),
    ("fluid mechanics",
     r"fluid|hydro|bernoulli|continuity|reynold|pipe|flow|viscos|buoyan|"
     r"manometer|nozzle|venturi|head loss|darcy|water"),
    ("solid mechanics",
     r"solid|stress|strain|beam|bend|torsion|torque|shaft|deflect|strength|"
     r"material|elastic|young|modulus|structur|static|cantilever|shear|axial"),
    ("dynamics",
     r"dynamic|kinematic|kinetic|momentum|impulse|collision|impact|vibrat|"
     r"oscillat|spring|pendulum|projectile|motion|acceleration|velocity|"
     r"energy|flywheel|rotation|frequency"),
]
TOPIC_PATTERNS = [
    (name, re.compile(r"\b(?:" + words + r")", re.IGNORECASE))
    for name, words in TOPIC_WORDS
]
#: Words meaning "any topic will do".
ANY_TOPIC = re.compile(
    r"\b(?:any|random|mix|general|all|every|whatever|surprise|mech|engineering)",
    re.IGNORECASE,
)

#: A question code: two letters and a number, loosely written ("fl2", "FL 02").
CODE = re.compile(r"\b([A-Za-z]{2})\s*[-_ ]?\s*(\d{1,2})\b")


class UnknownTopic(AgentAppError):
    user_message = (
        "I only have questions on thermodynamics, fluid mechanics, solid "
        "mechanics and dynamics. Name one of those, or ask for any topic."
    )
    retryable = False


class MissingCode(AgentAppError):
    user_message = (
        "Tell me which question you are answering, with its code, for "
        "example: my answer to TH-03 is 42"
    )
    retryable = False


class UnknownCode(AgentAppError):
    user_message = "I do not have a question with that code."
    retryable = False


class MissingNumber(AgentAppError):
    user_message = (
        "Tell me your answer as a number, for example: my answer to TH-03 is 42"
    )
    retryable = False


class App:
    def __init__(self) -> None:
        self._manifest = validate_manifest(load_manifest_file(MANIFEST_PATH))

    @property
    def manifest(self) -> Manifest:
        return self._manifest

    def handle(self, call: Call) -> Result:
        if call.outcome_id == "question.get":
            return self._question(call)
        if call.outcome_id == "answer.check":
            return self._check(call)
        raise AgentAppError(f"this app cannot do {call.outcome_id!r}")

    # ── question.get ────────────────────────────────────────────────────────

    def _question(self, call: Call) -> Result:
        topic_text = (call.inputs.get("topic") or "").strip()
        pool = questions_for(topic_text)
        now = call.context.now
        seconds = now.hour * 3600 + now.minute * 60 + now.second
        question = pool[seconds % len(pool)]
        return Result(
            data={
                "question_id": question["id"],
                "topic": question["topic"],
                "question": question["question"],
                "how_to_answer": HOW_TO_ANSWER,
            },
            narrative=f"Here is a {question['topic']} question, code {question['id']}.",
        )

    # ── answer.check ────────────────────────────────────────────────────────

    def _check(self, call: Call) -> Result:
        raw_code = (call.inputs.get("question_id") or "").strip()
        if not raw_code:
            raise MissingCode("no question code in the message")

        code = normalise_code(raw_code)
        question = BY_ID.get(code) if code else None
        if question is None:
            error = UnknownCode(f"unknown question code {raw_code!r}")
            error.user_message = f"I do not have a question with the code {raw_code}."
            raise error

        given = call.inputs.get("answer")
        if (given is None or isinstance(given, bool)
                or not math.isfinite(float(given))):
            error = MissingNumber(f"no numeric answer for {code}")
            error.user_message = (
                f"Tell me your answer to {code} as a number, for example: "
                f"my answer to {code} is 42"
            )
            raise error

        given = float(given)
        expected = float(question["answer"])
        unit = question["unit"]
        allowed = abs(expected) * float(question["tolerance_percent"]) / 100
        correct = abs(given - expected) <= allowed + 1e-12

        right = with_unit(format_answer(expected), unit)
        opening = "Correct." if correct else "Not quite."
        return Result(
            data={
                "correct": correct,
                "your_answer": with_unit(plain_number(given), unit),
                "right_answer": right,
                "worked_solution": question["solution"],
            },
            narrative=f"{opening} The answer is {right}.",
        )


# ── Helpers (no state) ──────────────────────────────────────────────────────


def questions_for(topic_text: str) -> list[dict]:
    """The questions matching what the student named. Empty means any topic."""
    if not topic_text:
        return QUESTIONS
    for name, pattern in TOPIC_PATTERNS:
        if pattern.search(topic_text):
            return [q for q in QUESTIONS if q["topic"] == name]
    if ANY_TOPIC.search(topic_text):
        return QUESTIONS
    raise UnknownTopic(f"no topic matches {topic_text!r}")


def normalise_code(text: str) -> str:
    """'fl2', 'FL 02', 'fl-02' -> 'FL-02'. Empty string if no code is there."""
    match = CODE.search(text)
    if not match:
        return ""
    return f"{match.group(1).upper()}-{int(match.group(2)):02d}"


def format_answer(value: float) -> str:
    """Four significant figures, trailing zeros dropped: 164.0 -> '164'."""
    if value == 0:
        return "0"
    decimals = max(0, 3 - math.floor(math.log10(abs(value))))
    text = f"{value:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def plain_number(value: float) -> str:
    """The student's number as they gave it: 42.0 -> '42', 3.9 -> '3.9'."""
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def with_unit(number: str, unit: str) -> str:
    if not unit:
        return number
    if unit == "%":
        return f"{number}%"
    return f"{number} {unit}"
