from autohub.automation.brochures.parser.strategies.generic import GenericDoclingParser
from autohub.automation.brochures.extractor.car_extractor import CarExtractorV1
from pathlib import Path

def main():
    base = Path(__file__).resolve().parents[3]
    pdf_path = (base / "automation" / "brochures" / "data" / "pdfs" / "Mahindra" / "Thar_Roxx" / "2024" / "brochure.pdf")
    

    # Step 1: Parse
    parser = GenericDoclingParser()
    parsed_document = parser.parse(str(pdf_path))

    print("\n--- PARSED DOCUMENT DEBUG ---")
    print("source:", parsed_document.source)
    print("number_of_blocks =", len(parsed_document.blocks))
    print("first 3 blocks:", parsed_document.blocks[:3])

    # Step 2: Extract car info
    extractor = CarExtractorV1()
    car_info = extractor.extract(parsed_document)

    print("\n---- Extracted Car Information ----")
    print(car_info.model_dump())


if __name__ == "__main__":
    main()
