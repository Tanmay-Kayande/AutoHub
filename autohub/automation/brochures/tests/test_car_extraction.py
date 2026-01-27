from pathlib import Path
from autohub.automation.brochures.extractor.car_extractor import CarExtractor
from autohub.automation.brochures.extractor.sources.brochure_pdf import BrochurePdfExtractor
from autohub.automation.brochures.extractor.sources.mahindra_web import MahindraWebExtractor


def main():
    base = Path(__file__).resolve().parents[3]
    pdf_path = (base / "automation" / "brochures" / "data" / "pdfs" / "Mahindra" / "Thar_Roxx" / "2024" / "brochure.pdf")
    
    assert pdf_path.exists(), f"PDF not found: {pdf_path}"

    web_source = MahindraWebExtractor(model="thar-roxx")
    brochure_source = BrochurePdfExtractor(str(pdf_path))

   # -------------------------------------------------
    # Orchestrator (priority: web → pdf)
    # -------------------------------------------------
    extractor = CarExtractor(
        sources=[
            web_source,
            brochure_source,
        ]
    )

    # -------------------------------------------------
    # Run extraction
    # -------------------------------------------------
    extracted_data = extractor.extract()

    # -------------------------------------------------
    # Output results
    # -------------------------------------------------
    print("\n=== FINAL MERGED CAR DATA ===")

    if not extracted_data:
        print("(no data extracted)")
    else:
        for key, value in extracted_data.items():
            print(f"{key}: {value}")

    # -------------------------------------------------
    # Sanity check (non-blocking)
    # -------------------------------------------------
    print("\n=== SANITY CHECK ===")

    expected_fields = [
        "car_engine_capacity",
        "car_power",
        "car_torque",
        "car_fuel",
        "car_transmission",
        "car_mileage",
        "car_price",
    ]

    for field in expected_fields:
        status = "OK" if field in extracted_data else "MISSING"
        print(f"{field}: {status}")

if __name__ == "__main__":
    main()
