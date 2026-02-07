import json
import os
from autohub.automation.brochures.extractor.sources.pdf_text_llm import PDFTextLLMExtractor

# Path to your brochure
PDF_PATH = os.path.join(os.path.dirname(__file__), "sample_brochure.pdf")

def test_final_extraction():
    print("\n" + "="*40)
    print("FINAL INTEGRATION TEST: NATIVE PDF EXTRACTION")
    print("="*40)

    # 1. Initialize Extractor
    extractor = PDFTextLLMExtractor(pdf_path=PDF_PATH)
    
    # 2. Execute Extraction (Includes File Upload to Gemini)
    print(f"[*] Sending '{os.path.basename(PDF_PATH)}' to Gemini API...")
    result = extractor.extract()

    # 3. Print JSON Output for Manual Review
    print("\n--- GEMINI JSON OUTPUT ---")
    print(json.dumps(result, indent=2))
    print("-" * 26 + "\n")

    # 4. Automated Verifications
    try:
        # Check Brand & Model
        assert result.get("car_brand") == "Mahindra", f"Expected Mahindra, got {result.get('car_brand')}"
        assert "Thar Roxx" in (result.get("car_model") or ""), "Car model identification failed"

        # Check Variants List
        variants = result.get("variants", [])
        assert len(variants) > 0, "No variants were extracted"

        # Check for Technical Data (Verify page 32 was read correctly)
        first_variant = variants[0]
        assert first_variant.get("power") is not None, f"Technical data missing for {first_variant.get('variant_name')}"
        
        print(f"SUCCESS: Extracted {len(variants)} variants.")
        print(f"DATA VERIFIED: First variant ({first_variant['variant_name']}) power is {first_variant['power']}.")

    except AssertionError as e:
        print(f"TEST FAILED: {e}")
    except Exception as e:
        print(f"UNEXPECTED ERROR: {e}")

if __name__ == "__main__":
    test_final_extraction()