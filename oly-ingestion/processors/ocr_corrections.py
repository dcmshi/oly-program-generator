# processors/ocr_corrections.py
"""
OCR correction dictionary for Soviet-era weightlifting sources.

Common OCR errors from scanned translations of Medvedev, Laputin & Oleshko,
Roman, and other Soviet-era authors. Apply these corrections after extraction
and before chunking.
"""

import re

OCR_CORRECTIONS: dict[str, str] = {
    # Weightlifting terminology
    "snalch": "snatch",
    "c1ean": "clean",
    "jerK": "jerk",
    "squalts": "squats",
    "pu11": "pull",
    "deadlifi": "deadlift",
    "barbe11": "barbell",

    # 1RM variants (numeral 1 vs lowercase L vs uppercase I)
    "1RM": "1RM",
    "lRM": "1RM",
    "IRM": "1RM",
    "lrm": "1RM",

    # Percentage/number fixes
    "l00%": "100%",
    "9O%": "90%",
    "8O%": "80%",
    "7O%": "70%",
    "6O%": "60%",
    "5O%": "50%",

    # Common author names (mistransliteration)
    "Medvedyev": "Medvedev",
    "Medvyedev": "Medvedev",
    "Zatsiorski": "Zatsiorsky",
    "Zatsiorksy": "Zatsiorsky",
    "Verkoshansky": "Verkhoshansky",
    "Verkhoshanskiy": "Verkhoshansky",
    "Laputin": "Laputin",   # usually fine, included for completeness

    # Training terminology
    "mesocyc1e": "mesocycle",
    "microcy1e": "microcycle",
    "microcyc1e": "microcycle",
    "macrocyc1e": "macrocycle",
    "periodisa tion": "periodisation",
    "periodiza tion": "periodization",
    "supercompen sation": "supercompensation",

    # Common OCR split words
    "hy- pertrophy": "hypertrophy",
    "in- tensity": "intensity",
    "ac- cumulation": "accumulation",
}


def apply_ocr_corrections(text: str) -> str:
    """Apply OCR correction substitutions to extracted text.

    Uses word-boundary matching to avoid replacing substrings of valid words.

    Args:
        text: Raw extracted text from OCR or PDF extraction.

    Returns:
        Corrected text with common OCR errors fixed.
    """
    for error, correction in OCR_CORRECTIONS.items():
        # Use word boundaries where the error is a standalone token
        pattern = re.compile(r"\b" + re.escape(error) + r"\b")
        text = pattern.sub(correction, text)
    return text
