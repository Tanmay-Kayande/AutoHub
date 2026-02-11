"""
Run AutoHub brochure automation pipeline.
"""

from pathlib import Path

from autohub.automation.discovery.mahindra import (
    discover_mahindra_brochures,
    save_discovery,
    )

from autohub.automation.brochures.downloader.brochure_downloader import run_brochure_downloader

from autohub.automation.brochures.extractor.sources.pdf_text_llm import PDFTextLLMExtractor
from autohub.automation.normalizer.car_normalizer import normalize_variant
from autohub.automation.db_writer.car_writer import write_car_payload
from autohub.database.connection import session_local

from autohub.automation.brochures.checksum import calculate_checksum, load_checksums, save_checksums

from autohub.automation.brochures.utils import iter_downloaded_pdfs

def run_brochure_pipeline():

    # Discovery
    print("Starting Mahindra brochure discovery...")
    data = discover_mahindra_brochures()
    save_discovery(data)
    print("Discovery completed.")

    # Download
    print("Starting brochure downloader...")
    run_brochure_downloader()
    print("Brochure download completed.")

    # Setup
    checksums = load_checksums()
    db = session_local()


    try:
        # Process each downloaded pdf
        for meta in iter_downloaded_pdfs():
            pdf_path = Path(meta["file_path"])
            if not pdf_path.exists():
                print(f"PDF missing on disk: {pdf_path}")
                continue

            pdf_bytes = pdf_path.read_bytes()
            checksum = calculate_checksum(pdf_bytes)
            file_key = pdf_path.name

            if checksums.get(file_key) == checksums:
                print(f"Skipping unchanged brochure: {pdf_path}")
                continue

            print(f"Processing brochure: {pdf_path}")

            # Gemini Extractor
            extractor = PDFTextLLMExtractor(str(pdf_path))
            raw_result = extractor.extract()

            variants = raw_result.get("vaariants", [])
            car_brand = raw_result.get("car_brand")
            car_model = raw_result.get("car_model")

            if not variants:
                print(f"No variants found in {pdf_path}")
                continue

            #Normalizze + Write(db)
            for variant in variants:
                normalized = normalize_variant(
                    variant=variant,
                    car_brand=car_brand,
                    car_model=car_model,
                )

                if normalized:
                    write_car_payload(normalized, db)

            #update checksum
            checksums[file_key] = checksum
            save_checksums(checksums)

            print(f"Completed brochure: {pdf_path}")

        db.commit()
        print("All brochures processed successfully")

    except Exception as e:
        db.rollback()
        print(f"Pipeline failed: {e}")

    finally:
        db.close()

if __name__ == "__main__":
    run_brochure_pipeline()
