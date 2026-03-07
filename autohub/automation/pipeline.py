"""
Combined AutoHub pipeline — runs brochure ingestion followed by image fetching.
This is the single entry point for the full automation pipeline.
"""

from autohub.automation.brochures.downloader.brochure_downloader import (
    run_brochure_downloader,
    PDF_BASE_DIR,
)
from autohub.automation.discovery.mahindra import (
    discover_mahindra_brochures,
    save_discovery,
)
from autohub.automation.brochures.extractor.sources.pdf_text_llm import (
    PDFTextLLMExtractor,
)
from autohub.automation.normalizer.car_normalizer import normalize_variant
from autohub.automation.db_writer.car_writer import write_car_payload
from autohub.automation.brochures.checksum import (
    calculate_checksum,
    load_checksums,
    save_checksums,
    update_record,
)
from autohub.automation.brochures.utils import iter_downloaded_pdfs
from autohub.automation.images.image_fetcher import fetch_car_images
from autohub.automation.images.image_writer import write_car_images
from autohub.database.connection import session_local
from autohub.database.model import CarModel, CarBrand
from pathlib import Path
from typing import cast

FORCE_REPROCESS = False

def run_full_pipeline():
    """
    Runs the complete AutoHub pipeline:
    1. Discover brochures
    2. Download PDFs
    3. Extract specs via Gemini
    4. Normalize + write to DB
    5. Fetch car images via SerpApi
    6. Write images to DB
    """

    print("\n" + "="*60)
    print("AUTOHUB FULL PIPELINE STARTED")
    print("="*60)

    # Phese 1: Brochure Ingestion Pipeline
    print("\n[Phase 1] Starting brochure pipeline...")

    # STEP 1: Discovery
    print("\n[Step 1/4] Discovering brochures...")
    data = discover_mahindra_brochures()
    save_discovery(data)
    print(f"Discovered {len(data)} brochures.")

    # STEP 2: Download
    print("\n[Step 2/4] Downloading brochures...")
    run_brochure_downloader()
    print("Download complete.")

    # STEP 3 + 4: Extraction + Normalization + DB Write
    print("\n[Step 3/4] Extracting specs and writing to DB...")

    checksums = load_checksums()
    db = session_local()

    brochure_success = 0
    brochure_skipped = 0
    brochure_failed = 0

    try:
        metas = list(iter_downloaded_pdfs())

        if not metas:
            print("No downloaded PDFs found.")
        else:
            print(f"Found {len(metas)} PDFs to process.")

            for meta in metas:
                pdf_path = Path(meta["file_path"])

                if not pdf_path.exists():
                    print(f"PDF missing on disk: {pdf_path}")
                    brochure_failed += 1
                    continue

                pdf_bytes = pdf_path.read_bytes()
                checksum = calculate_checksum(pdf_bytes)
                file_key = str(pdf_path.relative_to(PDF_BASE_DIR))

                stored = checksums.get(file_key)
                stored_hash = stored.get("checksum") if isinstance(stored, dict) else None
                already_extracted = stored.get("extracted", False) if isinstance(stored, dict) else False

                # Skip if already successfully extracted
                if not FORCE_REPROCESS and stored_hash == checksum and already_extracted:
                    print(f"[Brochure] Skipping — already extracted: {pdf_path.name}")
                    brochure_skipped += 1
                    continue

                print(f"[Brochure] Processing: {pdf_path.name}")

                extractor = PDFTextLLMExtractor(str(pdf_path))
                raw_result = extractor.extract()

                variants = raw_result.get("variants", [])
                car_brand = raw_result.get("car_brand")
                car_model = raw_result.get("car_model")

                if not variants:
                    print(f"[Brochure] No variants found: {pdf_path.name}")
                    update_record(
                        checksums=checksums,
                        file_key=file_key,
                        checksum=checksum,
                        extracted=False,
                        model_version="gemini-2.5-flash",
                    )
                    save_checksums(checksums)
                    brochure_failed += 1
                    continue

                for variant in variants:
                    normalized = normalize_variant(
                        variant=variant,
                        car_brand=car_brand,
                        car_model=car_model,
                    )
                    if normalized:
                        write_car_payload(normalized, db)

                update_record(
                    checksums=checksums,
                    file_key=file_key,
                    checksum=checksum,
                    extracted=True,
                    model_version="gemini-2.5-flash",
                )
                save_checksums(checksums)
                brochure_success += 1
                print(f"[Brochure] Completed: {pdf_path.name}")

        db.commit()

    except Exception as e:
        db.rollback()
        print(f"[Brochure] Pipeline error: {e}")
    finally:
        db.close()

    print(f"\n[Phase 1] Done — success: {brochure_success}, skipped: {brochure_skipped}, failed: {brochure_failed}")

    # Phase 2: Image Fetching Pipeline

    print("\n[Phase 2] Starting image pipeline...")

    db = session_local()

    image_total_exterior = 0
    image_total_interior = 0
    image_failed = []

    try:
        models = db.query(CarModel).join(CarBrand).all()

        if not models:
            print("[Image] No car models found in DB.")
        else:
            print(f"[Image] Found {len(models)} models to process.")

            for car_model in models:
                brand_name = cast(str, car_model.brand.name)
                model_name = cast(str, car_model.name)
                model_id = cast(int, car_model.id)

                print(f"\n[Image] Processing: {brand_name} {model_name}")

                try:
                    image_data = fetch_car_images(brand_name, model_name)

                    if not image_data["exterior"] and not image_data["interior"]:
                        print(f"[Image] No images found for {brand_name} {model_name}, skipping.")
                        continue

                    result = write_car_images(
                        model_id=model_id,
                        image_data=image_data,
                        db=db,
                    )

                    exterior_saved = result["exterior_saved"]
                    interior_saved = result["interior_saved"]

                    db.commit()

                    image_total_exterior += exterior_saved
                    image_total_interior += interior_saved

                    print(f"[Image] Saved {exterior_saved} exterior, {interior_saved} interior for {brand_name} {model_name}")

                except Exception as e:
                    db.rollback()
                    image_failed.append(f"{brand_name} {model_name}")
                    print(f"[Image] Failed for {brand_name} {model_name}: {e}")
                    continue
    except Exception as e:
        db.rollback()
        print(f"[Image] Pipeline crashed: {e}")
    finally:
        db.close()

    print(f"\n[Phase 2] Done — exterior: {image_total_exterior}, interior: {image_total_interior}")
    if image_failed:
        print(f"[Image] Failed models: {', '.join(image_failed)}")

    print("\n" + "="*60)
    print("AUTOHUB FULL PIPELINE COMPLETED")
    print("="*60)

if __name__ == "__main__":
    run_full_pipeline()    