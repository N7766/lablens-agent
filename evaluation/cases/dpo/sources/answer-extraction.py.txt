"""Answer extraction helpers for GSM8K-style numerical answers."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")


def normalize_number(value: str | int | float | None) -> str | None:
    """Normalize a numeric string for exact-answer comparison."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def extract_gold_answer(answer: str) -> str | None:
    """Extract the final GSM8K answer, usually after '####'."""
    if "####" in answer:
        tail = answer.split("####")[-1]
        match = NUMBER_RE.search(tail)
        return normalize_number(match.group(0)) if match else None
    return extract_final_number(answer)


def extract_final_number(text: str) -> str | None:
    """Return the last numerical value in text."""
    matches = NUMBER_RE.findall(text or "")
    if not matches:
        return None
    return normalize_number(matches[-1])


def extract_final_answer(text: str) -> str | None:
    """Extract the final numerical answer from model output.

    Priority order:
    1. Number after the last "Answer:" tag.
    2. Number after the last "final answer is" phrase.
    3. GSM8K-style "####" final answer.
    4. Last number in the whole prediction as a fallback.
    """
    if not text:
        return None

    answer_tag_matches = list(re.finditer(r"answer\s*:", text, flags=re.IGNORECASE))
    if answer_tag_matches:
        tail = text[answer_tag_matches[-1].end() :]
        match = NUMBER_RE.search(tail)
        if match:
            return normalize_number(match.group(0))

    final_answer_matches = list(
        re.finditer(
            r"final\s+answer\s+(?:is|=|:)\s*([-+]?\d[\d,]*(?:\.\d+)?)",
            text,
            flags=re.IGNORECASE,
        )
    )
    if final_answer_matches:
        return normalize_number(final_answer_matches[-1].group(1))

    if "####" in text:
        gold_style = extract_gold_answer(text)
        if gold_style is not None:
            return gold_style

    return extract_final_number(text)


def extract_pred_answer(text: str) -> str | None:
    """Extract a model's final numerical answer robustly.

    Handles forms such as:
    - Answer: 72
    - The answer is 72.
    - Therefore, the answer is 72.
    - GSM8K-style #### 72
    """
    return extract_final_answer(text)


def answers_match(predicted: str | None, gold: str | None) -> bool:
    return predicted is not None and gold is not None and normalize_number(predicted) == normalize_number(gold)
