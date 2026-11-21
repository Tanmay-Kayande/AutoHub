"""Write one successful extraction into the catalogue.

Resolves the brochure to a model (matching existing names loosely, creating the
model only when nothing matches), then each extracted variant to a variant of
the model's current generation, then hands the normalised values to
:func:`autohub.specs.service.apply_spec_values`, which owns the guarded write
rule and history.

Nothing here deletes, and an empty value never blanks a stored one. A variant
missing from a newer extraction of the brochure that alone supplied it is
retired (``is_active = False``, see :func:`_retire_superseded`); every other
variant stays as it is. The caller owns the transaction (one brochure = one
transaction).
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from autohub.catalog.slugs import slugify, unique_slug
from autohub.catalog.vocab import (
    normalize_body_type,
    normalize_drivetrain,
    normalize_fuel_type,
    normalize_transmission_type,
)
from autohub.db.models import (
    Brochure,
    BrochureVersion,
    ExtractionRun,
    ModelGeneration,
    Variant,
    VariantSpec,
    VehicleModel,
)
from autohub.ingestion.extraction.schema import BrochureExtraction, ExtractedVariant, extraction_from_raw
from autohub.ingestion.normalize import Provenance, normalize_specs
from autohub.specs.service import ApplyResult, SpecValueInput, apply_spec_values
from autohub.specs.taxonomy import get_spec

logger = logging.getLogger(__name__)

MODEL_MATCH_RATIO = 0.9  # "XUV 3XO EV" vs "XUV 3XO" is 0.86 and must not match
# An extraction listing fewer than half the variants its brochure supplied before is
# treated as incomplete (e.g. one row per engine instead of every trim): nothing retires.
RETIRE_MIN_SHARE = 0.5
_NON_ALNUM = re.compile(r"[^a-z0-9+]+")


def compact(name: str) -> str:
    """``"XUV 3XO"`` and ``"XUV3XO"`` -> ``"xuv3xo"``; ``"Scorpio-N"`` -> ``"scorpion"``."""
    return _NON_ALNUM.sub("", name.lower())


_WORD = re.compile(r"[a-z0-9]+|\+")  # "+" is its own token: "Pure+ S" == "Pure + S"
_SEATS = re.compile(r"(\d)\s*-?\s*(str|seater|seats?)\b")
_SYNONYMS = {"seater": "str", "seats": "str", "seat": "str", "automatic": "at", "manual": "mt", "4x4": "4wd"}


def name_tokens(name: str) -> tuple[str, ...]:
    """Order-free identity of a variant name: ``"AX7 TECH 6 STR Diesel AWD AT"`` and
    ``"AX7 Tech Diesel AT AWD 6-Seater"`` give the same tokens; ``"AX 7 STR"`` and
    ``"AX7 STR"`` do not."""
    text = _SEATS.sub(r"\1 str", name.lower())
    return tuple(sorted(_SYNONYMS.get(w, w) for w in _WORD.findall(text)))


# Powertrain wording that brochures of one model print differently for the same car
# ("Pure+ 1.2L Revotron 6MT" in the iCNG brochure, "Pure + CNG 6MT" in the main one).
# Engine families are listed explicitly: an unknown word stays part of the trim, so
# the worst case is a missed match (today's behaviour), never a wrong merge.
_FUEL_WORDS = frozenset({"petrol", "gasoline", "diesel", "cng", "icng", "electric", "ev", "lpg"})
_GEARBOX_WORDS = frozenset(
    {
        "mt",
        "at",
        "amt",
        "dct",
        "dca",
        "cvt",
        "ivt",
        "manual",
        "automatic",
        "dual",
        "clutch",
        "speed",
        "transmission",
    }
)
_ENGINE_WORDS = frozenset(
    {
        "kappa", "revotron", "revotorq", "kryojet", "kryotec", "hyperion", "mpi", "turbo",
        "turbocharged", "gdi", "tgdi", "u2", "crdi", "crde", "mhawk", "mstallion", "na",
        "engine", "injection", "direct",
    }
)  # fmt: skip
_GEARS = re.compile(r"(\d+)\s*-?\s*(?:speed\b|(?=(?:mt|at|amt|dct|dca|cvt|ivt)\b))")
_DISPLACEMENT = re.compile(r"\b(\d\.\d)\s*l\b")


@dataclass(frozen=True)
class PowertrainKey:
    """A variant name split into the trim (identity) and its powertrain wording."""

    trim: tuple[str, ...]
    gears: frozenset[str]
    displacement: frozenset[str]
    engines: frozenset[str]


def powertrain_key(name: str) -> PowertrainKey:
    text = _SEATS.sub(r"\1 str", name.lower())
    gears = frozenset(_GEARS.findall(text))
    text = _GEARS.sub(" ", text)
    displacement = frozenset(_DISPLACEMENT.findall(text))
    text = _DISPLACEMENT.sub(" ", text)
    trim: list[str] = []
    engines: set[str] = set()
    for word in _WORD.findall(text):
        if word in _ENGINE_WORDS:
            engines.add(word)
        elif word not in _FUEL_WORDS and word not in _GEARBOX_WORDS:
            trim.append(_SYNONYMS.get(word, word))
    return PowertrainKey(tuple(sorted(trim)), gears, displacement, frozenset(engines))


def same_car(
    a_name: str,
    a_codes: tuple[str | None, str | None],
    b_name: str,
    b_codes: tuple[str | None, str | None],
) -> bool:
    """Whether two differently worded names of one model describe the same variant.

    Requires the same trim words, the same known fuel and transmission codes, no
    contradiction in gear count or displacement, and engine words of one name
    contained in the other's ("1.2L Revotron" vs nothing). "#DARK", "CAMO", seat
    counts and drivetrain stay in the trim, so editions are never merged.
    """
    if None in a_codes or a_codes != b_codes:
        return False
    a, b = powertrain_key(a_name), powertrain_key(b_name)
    if a.trim != b.trim or not any(w != "+" for w in a.trim):
        return False
    if a.gears and b.gears and a.gears != b.gears:
        return False
    if a.displacement and b.displacement and a.displacement != b.displacement:
        return False
    return a.engines <= b.engines or b.engines <= a.engines


_TITLE_FILLER = frozenset({"brochure", "download", "main", "the", "new", "catalogue", "catalog"})
_YEAR = re.compile(r"^(19|20)\d\d$")


def edition_label(title: str | None, model_names: list[str]) -> str | None:
    """The edition a brochure title names beyond the model: ``"Sierra #DARK Brochure"``
    -> ``"#DARK"``; ``"Nexon iCNG Brochure 2026"`` and ``"Brochure"`` -> ``None``."""
    if not title:
        return None
    model_words = {w for n in model_names for w in _WORD.findall(n.lower())}
    kept = []
    for word in title.split():
        tokens = _WORD.findall(word.lower())
        if not tokens or all(
            t in model_words or t in _TITLE_FILLER or t in _FUEL_WORDS or _YEAR.match(t) for t in tokens
        ):
            continue
        kept.append(word)
    return " ".join(kept) or None


def with_edition(name: str, label: str | None) -> str:
    """Append the edition unless the name already carries all its words."""
    if not label or set(name_tokens(label)) <= set(name_tokens(name)):
        return name
    return f"{name} {label}"


def full_variant_name(extracted: ExtractedVariant) -> str:
    """The printed name plus any powertrain words it lacks ("AX" + "MT" -> "AX MT")."""
    name = " ".join(extracted.name.split())
    present = set(name_tokens(name))
    extra = [
        part.strip()
        for part in (extracted.fuel_type, extracted.drivetrain, extracted.transmission)
        if part and part.strip() and not set(name_tokens(part)) <= present
    ]
    for part in extra:
        present |= set(name_tokens(part))
    return " ".join([name, *extra])


def match_model(candidates: list[str], models: list[VehicleModel]) -> VehicleModel | None:
    """Exact compact-name match first, then the closest name above ``MODEL_MATCH_RATIO``."""
    keys = [compact(c) for c in candidates if c and compact(c)]
    by_key = {compact(m.name): m for m in models}
    for key in keys:
        if key in by_key:
            return by_key[key]
    best: tuple[float, VehicleModel | None] = (0.0, None)
    for key in keys:
        for model_key, model in by_key.items():
            ratio = SequenceMatcher(None, key, model_key).ratio()
            if ratio > best[0]:
                best = (ratio, model)
    return best[1] if best[0] >= MODEL_MATCH_RATIO else None


@dataclass
class WriteSummary:
    model_id: int
    model_created: bool = False
    variants_created: int = 0
    variants_matched: int = 0
    not_applicable_skipped: int = 0
    ambiguous_variants: int = 0  # same name twice in one extraction: skipped, never merged
    retired: list[str] = field(default_factory=list)  # names of variants set inactive
    retire_skipped: str | None = None  # why superseded variants were kept, if they were
    result: ApplyResult = field(default_factory=ApplyResult)


def _resolve_model(
    db: Session, brochure: Brochure, extraction: BrochureExtraction
) -> tuple[VehicleModel, bool]:
    if brochure.model_id is not None:
        model = db.get(VehicleModel, brochure.model_id)
        if model is not None:
            return model, False
    models = list(
        db.execute(select(VehicleModel).where(VehicleModel.brand_id == brochure.brand_id)).scalars()
    )
    names = [n for n in (brochure.model_hint, extraction.model_name) if n]
    found = match_model(names, models)
    if found is not None:
        brochure.model_id = found.id
        return found, False
    if not names:
        raise ValueError(f"brochure {brochure.id} has no model hint and the extraction names no model")
    name = names[0].strip()
    model = VehicleModel(
        brand_id=brochure.brand_id,
        name=name,
        slug=unique_slug(name, {m.slug for m in models}),
        body_type=normalize_body_type(extraction.body_type),
    )
    db.add(model)
    db.flush()
    brochure.model_id = model.id
    logger.info("model_created brand_id=%s model=%s brochure_id=%s", brochure.brand_id, name, brochure.id)
    return model, True


def _current_generation(db: Session, model: VehicleModel) -> ModelGeneration:
    generation = db.execute(
        select(ModelGeneration).where(
            ModelGeneration.model_id == model.id, ModelGeneration.is_current.is_(True)
        )
    ).scalar_one_or_none()
    if generation is None:
        generation = ModelGeneration(model_id=model.id, name="Current", is_current=True)
        db.add(generation)
        db.flush()
    return generation


def _codes(extracted: ExtractedVariant) -> tuple[str | None, str | None]:
    fuel = normalize_fuel_type(extracted.fuel_type) or normalize_fuel_type(extracted.name)
    transmission = normalize_transmission_type(extracted.transmission) or normalize_transmission_type(
        extracted.name
    )
    return fuel, transmission


def _resolve_variant(
    db: Session,
    generation: ModelGeneration,
    existing: list[Variant],
    extracted: ExtractedVariant,
    name: str,
    touched: set[int],
) -> tuple[Variant, bool]:
    """Exact name match first; then the one active variant worded differently for the
    same car (:func:`same_car`), skipping variants this extraction already wrote to."""
    slug = slugify(name)
    forms = {name_tokens(name), name_tokens(full_variant_name(extracted))}
    for variant in existing:
        if variant.slug == slug or name_tokens(variant.name) in forms:
            _fill_codes(variant, extracted)
            return variant, False
    codes = _codes(extracted)
    similar = [
        v
        for v in existing
        if v.is_active
        and v.id not in touched
        and same_car(name, codes, v.name, (v.fuel_type, v.transmission_type))
    ]
    if len(similar) == 1:
        logger.info("variant_matched_by_powertrain name=%r variant=%r", name, similar[0].name)
        _fill_codes(similar[0], extracted)
        return similar[0], False
    variant = Variant(
        generation_id=generation.id, name=name, slug=unique_slug(name, {v.slug for v in existing})
    )
    _fill_codes(variant, extracted)
    db.add(variant)
    db.flush()
    existing.append(variant)
    return variant, True


def _fill_codes(variant: Variant, extracted: ExtractedVariant) -> None:
    """Set fuel/transmission/drivetrain only where still unknown; never overwrite."""
    fuel, transmission = _codes(extracted)
    if variant.fuel_type is None:
        variant.fuel_type = fuel
        variant.fuel_type_raw = variant.fuel_type_raw or extracted.fuel_type
    if variant.transmission_type is None:
        variant.transmission_type = transmission
        variant.transmission_raw = variant.transmission_raw or extracted.transmission
        if (
            variant.transmission_type is None
            and variant.fuel_type == "electric"
            and not variant.transmission_raw
        ):
            # Brochures often omit an EV's gearbox; every EV sold here uses one fixed reduction gear.
            variant.transmission_type = "single_speed"
    if variant.drivetrain is None:
        variant.drivetrain = normalize_drivetrain(
            extracted.drivetrain, extracted.transmission, extracted.name
        )


def _applicable(values: list[SpecValueInput], fuel_type: str | None) -> tuple[list[SpecValueInput], int]:
    """Drop values for specs that cannot apply to this fuel (e.g. fuel tank on an EV)."""
    if fuel_type is None:
        return values, 0
    kept = []
    for value in values:
        spec = get_spec(value.key)
        if spec is not None and spec.applies_to_fuel is not None and fuel_type not in spec.applies_to_fuel:
            continue
        kept.append(value)
    return kept, len(values) - len(kept)


def _listed_by_other_brochures(db: Session, brochure: Brochure, model_id: int) -> list[ExtractedVariant]:
    """Variants in the latest successful extraction of every other active brochure of the model."""
    others = db.execute(
        select(Brochure.id).where(
            Brochure.model_id == model_id, Brochure.id != brochure.id, Brochure.is_active.is_(True)
        )
    ).scalars()
    listed: list[ExtractedVariant] = []
    for other_id in others:
        raw = db.execute(
            select(ExtractionRun.raw_output)
            .join(BrochureVersion, BrochureVersion.id == ExtractionRun.brochure_version_id)
            .where(BrochureVersion.brochure_id == other_id, ExtractionRun.status == "succeeded")
            .order_by(ExtractionRun.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        if raw:
            try:
                listed.extend(extraction_from_raw(raw).variants)
            except ValueError:
                logger.warning("retire_check_unreadable_output brochure_id=%s", other_id)
    return listed


def _retire_superseded(
    db: Session,
    brochure: Brochure,
    model_id: int,
    existing: list[Variant],
    touched: set[int],
    summary: WriteSummary,
) -> None:
    """Set inactive the variants this brochure alone supplied that its new extraction no longer lists.

    A variant is kept when any of its values came from another source (another brochure,
    the V1 import, a manual edit), when another brochure's latest extraction still lists it
    (an unchanged value keeps the provenance of whichever brochure wrote it first), or when
    it has no values at all. Nothing retires when the new extraction looks incomplete.
    """
    candidates = [v for v in existing if v.is_active and v.id not in touched]
    if not candidates:
        return
    sources: dict[int, set[tuple[str, int | None]]] = defaultdict(set)
    for variant_id, source_type, brochure_id in db.execute(
        select(VariantSpec.variant_id, VariantSpec.source_type, BrochureVersion.brochure_id)
        .outerjoin(BrochureVersion, BrochureVersion.id == VariantSpec.brochure_version_id)
        .where(VariantSpec.variant_id.in_([v.id for v in candidates]))
    ):
        sources[variant_id].add((source_type, brochure_id))
    only_here = {("official_brochure", brochure.id)}
    superseded = [v for v in candidates if sources.get(v.id) == only_here]
    if not superseded:
        return
    previously = len(superseded) + summary.variants_matched
    if summary.variants_matched + summary.variants_created < RETIRE_MIN_SHARE * previously:
        summary.retire_skipped = (
            f"extraction lists {summary.variants_matched + summary.variants_created} variants; "
            f"the brochure supplied {previously}"
        )
        logger.warning("retire_skipped brochure_id=%s reason=%s", brochure.id, summary.retire_skipped)
        return
    elsewhere = _listed_by_other_brochures(db, brochure, model_id)
    for variant in superseded:
        codes = (variant.fuel_type, variant.transmission_type)
        if any(
            name_tokens(variant.name) == name_tokens(o.name)
            or same_car(variant.name, codes, o.name, _codes(o))
            for o in elsewhere
        ):
            continue
        variant.is_active = False
        summary.retired.append(variant.name)
    if summary.retired:
        logger.info("variants_retired brochure_id=%s names=%s", brochure.id, summary.retired)


def write_extraction(
    db: Session,
    *,
    version: BrochureVersion,
    run: ExtractionRun,
    extraction: BrochureExtraction,
) -> WriteSummary:
    brochure = db.get(Brochure, version.brochure_id)
    assert brochure is not None
    model, created = _resolve_model(db, brochure, extraction)
    if model.body_type is None and extraction.body_type:
        model.body_type = normalize_body_type(extraction.body_type)
    generation = _current_generation(db, model)
    existing = list(db.execute(select(Variant).where(Variant.generation_id == generation.id)).scalars())
    summary = WriteSummary(model_id=model.id, model_created=created)
    provenance = Provenance(brochure_version_id=version.id)

    # Names are kept as printed; powertrain words are added only where printed names repeat.
    printed = Counter(name_tokens(v.name) for v in extraction.variants)
    edition = edition_label(
        brochure.title, [model.name, *([brochure.model_hint] if brochure.model_hint else [])]
    )
    names = [
        with_edition(
            full_variant_name(v) if printed[name_tokens(v.name)] > 1 else " ".join(v.name.split()), edition
        )
        for v in extraction.variants
    ]
    counts = Counter(name_tokens(n) for n in names)
    variants = [v for v, n in zip(extraction.variants, names, strict=True) if counts[name_tokens(n)] == 1]
    kept_names = [n for n in names if counts[name_tokens(n)] == 1]
    summary.ambiguous_variants = len(names) - len(kept_names)
    if summary.ambiguous_variants:
        logger.warning(
            "ambiguous_variants run=%s skipped=%s names=%s",
            run.id,
            summary.ambiguous_variants,
            sorted({n for n in names if counts[name_tokens(n)] > 1}),
        )

    if not extraction.variants:
        # Only common values: they describe every variant already on file.
        targets = [(v, False) for v in existing]
        common_only = True
    else:
        touched: set[int] = set()
        targets = []
        for ev, name in zip(variants, kept_names, strict=True):
            variant, was_created = _resolve_variant(db, generation, existing, ev, name, touched)
            touched.add(variant.id)
            targets.append((variant, was_created))
        common_only = False

    for index, (variant, was_created) in enumerate(targets):
        if was_created:
            summary.variants_created += 1
        else:
            summary.variants_matched += 1
        own = [] if common_only else variants[index].specs
        values = normalize_specs(extraction.common_specs, own, provenance)
        values, skipped = _applicable(values, variant.fuel_type)
        summary.not_applicable_skipped += skipped
        summary.result.merge(
            apply_spec_values(db, variant.id, values, run_id=run.id, reason=f"brochure version {version.id}")
        )

    if not common_only:
        _retire_superseded(db, brochure, model.id, existing, {v.id for v, _ in targets}, summary)

    run.specs_written = summary.result.written
    run.specs_unchanged = summary.result.unchanged
    run.conflicts_skipped = summary.result.conflicts_skipped
    run.finished_at = datetime.now(UTC)
    db.flush()
    logger.info(
        "extraction_written run=%s model_id=%s variants_created=%s matched=%s written=%s unchanged=%s conflicts=%s",
        run.id,
        model.id,
        summary.variants_created,
        summary.variants_matched,
        summary.result.written,
        summary.result.unchanged,
        summary.result.conflicts_skipped,
    )
    return summary
