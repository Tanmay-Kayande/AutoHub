from autohub.automation.brochures.parser.strategies.generic import GenericDoclingParser
from autohub.automation.brochures.extractor.car_extractor import CarExtractor
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
    
    text_blocks = [b for b in parsed_document.blocks if b.type == "text"]
    table_blocks = [b for b in parsed_document.blocks if b.type == "table"]

    print("Text blocks:", len(text_blocks))
    print("Table blocks:", len(table_blocks))

    print("\nFirst 2 text blocks:")
    for b in text_blocks[:2]:
        print("-", b.content[:200], "...")

    # Step 2: Extract car info
    extractor = CarExtractor()
    extracted_data = extractor.extract(parsed_document)

    print("\n=== EXTRACTED RAW DATA (v2) ===")
    for key, value in extracted_data.items():
        print(f"{key}: {value}")


    print("\n=== Sanity Check ===")
    
    required_fields = [
        "car_engine",
        "car_engine_capacity",
        "car_power",
        "car_torque",
        "car_fuel",
        "car_transmission",
        "car_mileage",
        "car_price",
    ]

    for keys in required_fields:
        print(f"{keys}: {'OK' if keys in extracted_data else 'MISSING'}")

if __name__ == "__main__":
    main()
