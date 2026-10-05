"""Orchestrates the end-to-end flow: image in, extracted + resolved additive data out."""

from io import BytesIO
from pathlib import Path

from PIL import Image

from src.extractors.base import LabelExtractor
from src.schemas import ExtractedItem

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
    extraction_result = extraction_result.model_copy(
        update={"items": _drop_redundant_code_children(extraction_result.items)}
    )

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


def _drop_redundant_code_children(items: list[ExtractedItem]) -> list[ExtractedItem]:
    """Drops a nested child item whose entire verbatim text is just the
    additive code its parent already carries inline -- MEASURED (Khusmain):
    the extractor sometimes emits "Citric Acid (INS 330)" as one item AND
    a separate nested child "INS 330" (parent_item_id pointing back at it)
    whose only content is that same code, restated. Left in, this produces
    two ItemVerdict rows -- and two additives-table rows -- for one real
    ingredient.

    A child is dropped only when ALL of:
      - it is nested (nesting_depth > 0, parent_item_id set)
      - it is an ONLY child -- its parent has exactly ONE nested child,
        this one. REGRESSION this guards against, MEASURED (Chipsmain):
        a compound bracket like "Seasoning [..., Anticaking Agent (INS
        470(i), INS 551)), ..., Acidity Regulator (INS 330), ...]" is one
        parent with TWELVE children, six of them code-only (each the
        SOLE declaration of a real, distinct additive named by
        functional-class + code, not a restatement of anything). Without
        this guard, every one of those six matched the conditions below
        too (each code is a real substring of the parent's giant
        verbatim) and would have been silently dropped -- six real
        additives disappearing from the assessment, not a duplicate row
        removed. A parent naming exactly ONE thing plus its OWN code,
        split into two items, is a fundamentally different shape from a
        compound bracket enumerating several real sub-ingredients; child
        count is what tells them apart.
      - it carries a real code (declared_code set, code_system != "none")
      - its verbatim IS that code, exactly, nothing else
      - the parent's own verbatim already contains that exact code text

    A child whose verbatim is genuine descriptive text (e.g. "Natural-
    Nature Identical Flavouring Substances", nested under "Added Khus
    Flavour (...)" on the same label) is never touched: it carries no
    declared_code at all, so that condition alone rules it out -- this is
    not a general "drop every nested child" filter."""
    by_id = {item.item_id: item for item in items}
    child_counts: dict[int, int] = {}
    for item in items:
        if item.parent_item_id is not None:
            child_counts[item.parent_item_id] = child_counts.get(item.parent_item_id, 0) + 1

    kept = []
    for item in items:
        parent = by_id.get(item.parent_item_id) if item.parent_item_id is not None else None
        if (
            item.nesting_depth > 0
            and parent is not None
            and child_counts.get(item.parent_item_id, 0) == 1
            and item.declared_code
            and item.code_system != "none"
            and item.verbatim.strip() == item.declared_code.strip()
            and item.declared_code in parent.verbatim
        ):
            continue
        kept.append(item)
    return kept


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
