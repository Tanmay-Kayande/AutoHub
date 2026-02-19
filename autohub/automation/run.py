"""
Run AutoHub brochure automation pipeline.
"""

from pathlib import Path

# ---------------------------
# CONFIG
# ---------------------------

FORCE_REPROCESS = True   # Set True to ignore checksum and force extraction


# ---------------------------
# Imports
# ---------------------------

from autohub.automation.discovery.mahindra import (
    discover_mahindra_brochures,
    save_discovery,
)

from autohub.automation.brochures.downloader.brochure_downloader import (
    run_brochure_downloader,
    PDF_BASE_DIR,
)

from autohub.automation.brochures.extractor.sources.pdf_text_llm import (
    PDFTextLLMExtractor,
)

from autohub.automation.normalizer.car_normalizer import normalize_variant
from autohub.automation.db_writer.car_writer import write_car_payload
from autohub.database.connection import session_local

from autohub.automation.brochures.checksum import (
    calculate_checksum,
    load_checksums,
    save_checksums,
)

from autohub.automation.brochures.utils import iter_downloaded_pdfs


# ---------------------------
# MAIN PIPELINE
# ---------------------------

def run_brochure_pipeline():

    # ---------------------------
    # STEP 1: Discovery
    # ---------------------------
    print("Starting Mahindra brochure discovery...")
    data = discover_mahindra_brochures()
    save_discovery(data)
    print("Discovery completed.")

    # ---------------------------
    # STEP 2: Download
    # ---------------------------
    print("Starting brochure downloader...")
    run_brochure_downloader()
    print("Brochure download completed.")

    # ---------------------------
    # STEP 3: Extraction + DB
    # ---------------------------
    checksums = load_checksums()
    db = session_local()

    try:
        metas = list(iter_downloaded_pdfs())

        if not metas:
            print("No downloaded PDFs found.")
            return

        print(f"Found {len(metas)} downloaded PDFs.")

        for meta in metas:

            pdf_path = Path(meta["file_path"])

            if not pdf_path.exists():
                print(f"PDF missing on disk: {pdf_path}")
                continue

            pdf_bytes = pdf_path.read_bytes()
            checksum = calculate_checksum(pdf_bytes)

            # MUST match downloader logic
            file_key = str(pdf_path.relative_to(PDF_BASE_DIR))

            stored_checksum = checksums.get(file_key)

            # Debug visibility
            print("\n---------------------------------")
            print("File:", pdf_path)
            print("File key:", file_key)
            print("Stored checksum:", stored_checksum)
            print("Current checksum:", checksum)
            print("Checksum match:", stored_checksum == checksum)
            print("---------------------------------")

            if not FORCE_REPROCESS and stored_checksum == checksum:
                print(f"Skipping unchanged brochure: {pdf_path}")
                continue

            print(f"Processing brochure: {pdf_path}")

            # ---------------------------
            # Gemini Extraction
            # ---------------------------
            extractor = PDFTextLLMExtractor(str(pdf_path))
            raw_result = extractor.extract()

            variants = raw_result.get("variants", [])
            car_brand = raw_result.get("car_brand")
            car_model = raw_result.get("car_model")

            if not variants:
                print(f"No variants found in {pdf_path}")
                continue

            # ---------------------------
            # Normalize + Write DB
            # ---------------------------
            for variant in variants:
                normalized = normalize_variant(
                    variant=variant,
                    car_brand=car_brand,
                    car_model=car_model,
                )

                if normalized:
                    write_car_payload(normalized, db)

            # Update checksum only after successful processing
            checksums[file_key] = checksum
            save_checksums(checksums)

            print(f"Completed brochure: {pdf_path}")

        db.commit()
        print("\nAll brochures processed successfully.")

    except Exception as e:
        db.rollback()
        print(f"\nPipeline failed: {e}")

    finally:
        db.close()


# ---------------------------
# ENTRY POINT
# ---------------------------

if __name__ == "__main__":
    run_brochure_pipeline()
