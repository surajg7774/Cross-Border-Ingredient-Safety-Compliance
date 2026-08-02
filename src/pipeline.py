"""Orchestrates the end-to-end flow: image in, extracted + resolved additive data out."""

from io import BytesIO
from pathlib import Path

from PIL import Image

from src.extractors.base import LabelExtractor

UPSCALE_FACTOR = 2
EVIDENCE_TYPES = 5  # ingredients_keyword, nutrition_table, net_quantity, e_number_pattern, best_before
MAX_USEFUL_BBOX_AREA = 0.9  # above this, cropping barely shrinks the image — treat as not localised
BBOX_PADDING = 0.05  # fraction of image dimension
MIN_BBOX_DIMENSION = 0.05  # a box narrower or shorter than this is a degenerate sliver


def run_extraction(image_path: str | Path, extractor: LabelExtractor, crop: bool = False) -> dict:
    """Gate an image, then (if it's a food label with an ingredients declaration) extract it."""
    image_bytes = Path(image_path).read_bytes()

    gate_result = extractor.gate(image_bytes)
    # Self-reported LLM confidence is uncalibrated, so we derive it
    # deterministically from how much evidence was actually found instead of
    # trusting a number the model made up.
    evidence_count = min(len(gate_result.evidence_found), EVIDENCE_TYPES)
    gate_result = gate_result.model_copy(update={"confidence": evidence_count / EVIDENCE_TYPES})

    if not gate_result.is_food_label:
        return {
            "gate": gate_result.model_dump(),
            "extraction": None,
            "stop_reason": gate_result.reject_reason,
        }

    if not gate_result.has_ingredients_declaration:
        return {
            "gate": gate_result.model_dump(),
            "extraction": None,
            "stop_reason": (
                "food label detected but no ingredients declaration visible — "
                "please photograph the ingredients panel."
            ),
        }

    raw_bbox = gate_result.ingredients_panel_bbox
    bbox = _sanitise_bbox(raw_bbox)
    warnings = list(gate_result.warnings)

    if raw_bbox is not None and bbox is None:
        warnings.append(
            "ingredients panel bbox was invalid — crop+upscale accuracy step skipped"
        )
    if bbox is None or _bbox_area(bbox) > MAX_USEFUL_BBOX_AREA:
        # A missing or near-full-image bbox means the crop step below does
        # nothing — surface that instead of silently losing the accuracy step.
        warnings.append("ingredients panel not localised — crop+upscale accuracy step skipped")
    if crop:
        warnings.append("crop enabled by --crop")

    # Write back the sanitised box (not the model's raw claim) so the saved
    # JSON reflects what was actually used for cropping. Computed and stored
    # as diagnostics regardless of `crop` — only acted on when crop=True.
    gate_result = gate_result.model_copy(
        update={"warnings": warnings, "ingredients_panel_bbox": bbox}
    )

    if crop:
        extract_bytes = _crop_and_upscale(image_bytes, bbox)
    else:
        # Cropping to the ingredients panel and upscaling was originally added as
        # an ACCURACY step, on the assumption that small print needs more pixels.
        # Measured across 10 labels it produced identical extraction wherever it
        # engaged, and on one label it mis-located the panel and lost every
        # additive (0 items vs 25 without cropping). It is therefore OFF by
        # default and kept only as an opt-in for images where the declaration is
        # a small part of a large pack photo.
        extract_bytes = image_bytes

    language = gate_result.language_selected or "en"
    extraction_result = extractor.extract(extract_bytes, language)

    stop_reason = None
    if not extraction_result.items:
        # An empty result that reports success is worse than a crash,
        # because zero additives reads downstream as "no compliance issues".
        stop_reason = (
            "gate detected an ingredients declaration but extraction returned no "
            "items — the ingredients panel was probably mis-located; try an image "
            "cropped closer to the ingredients list"
        )

    return {
        "gate": gate_result.model_dump(),
        "extraction": extraction_result.model_dump(),
        "stop_reason": stop_reason,
    }


def _sanitise_bbox(bbox: list[float] | None) -> list[float] | None:
    """Repair or reject a model-supplied bounding box.

    The model sometimes returns coordinates out of order or outside
    [0, 1]. A bad box must degrade to 'not localised', never crash the
    pipeline.
    """
    if bbox is None:
        return None

    x_min, y_min, x_max, y_max = (max(0.0, min(1.0, value)) for value in bbox)
    if x_max < x_min:
        x_min, x_max = x_max, x_min
    if y_max < y_min:
        y_min, y_max = y_max, y_min

    if (x_max - x_min) < MIN_BBOX_DIMENSION or (y_max - y_min) < MIN_BBOX_DIMENSION:
        return None

    return [x_min, y_min, x_max, y_max]


def _bbox_area(bbox: list[float]) -> float:
    """Fraction of the image area a normalized [x_min, y_min, x_max, y_max] bbox covers."""
    x_min, y_min, x_max, y_max = bbox
    return (x_max - x_min) * (y_max - y_min)


def _pad_bbox(bbox: list[float]) -> list[float]:
    """Expand a normalized bbox by BBOX_PADDING on every side, clamped to [0, 1].

    A slightly generous crop costs a few wasted pixels; a tight crop
    silently discards footnotes and allergen statements that belong to the
    declaration. Recall matters more than tightness.
    """
    x_min, y_min, x_max, y_max = bbox
    return [
        max(0.0, x_min - BBOX_PADDING),
        max(0.0, y_min - BBOX_PADDING),
        min(1.0, x_max + BBOX_PADDING),
        min(1.0, y_max + BBOX_PADDING),
    ]


def _crop_and_upscale(image_bytes: bytes, bbox: list[float] | None) -> bytes:
    """Crop to the normalized bbox (if any, padded for safety) and upscale 2x, re-encoded as JPEG."""
    image = Image.open(BytesIO(image_bytes)).convert("RGB")

    if bbox is not None:
        width, height = image.size
        x_min, y_min, x_max, y_max = _pad_bbox(bbox)
        image = image.crop((
            int(x_min * width),
            int(y_min * height),
            int(x_max * width),
            int(y_max * height),
        ))

    upscaled = image.resize(
        (image.width * UPSCALE_FACTOR, image.height * UPSCALE_FACTOR), Image.LANCZOS
    )
    buffer = BytesIO()
    upscaled.save(buffer, format="JPEG")
    return buffer.getvalue()
