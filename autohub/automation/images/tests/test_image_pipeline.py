from autohub.automation.images.run import run_image_pipeline
from autohub.database.connection import session_local
from autohub.database.model import CarModel, CarBrand, CarImage
from typing import cast

def test_image_pipeline():

    print("\n" + "="*50)
    print("TEST: Full Image Pipeline")
    print("="*50)

    db = session_local()

    try:

        models =  db.query(CarModel).join(CarBrand).all()

        if not models:
            print("SKIPPED: No car models found in DB. Run brochure pipeline first.")
            return
        
        print(f"\nFound {len(models)} model(s) in DB:")

        for m in models:
            brand = cast(str, m.brand.name)
            name = cast(str, m.name)
            model_id = cast(int, m.id)

            existing = db.query(CarImage).filter(CarImage.model_id == model_id).count()

            print(f"  → {brand} {name} (id={model_id}, existing images={existing})")

    finally:
        db.close()

    print("\nRunning image pipeline...")
    print("-"*50)
    run_image_pipeline()
    print("-"*50)

    db = session_local()

    try:

        print("\nVerifying results...")

        all_images = db.query(CarImage).all()
        assert len(all_images) > 0, "Failed: No images were saved to the database."
        print(f"SUCCESS: {len(all_images)} images saved to the database.")

        exterior_images = db.query(CarImage).filter(
            CarImage.image_type == "exterior"
        ).all()

        interior_images = db.query(CarImage).filter(
            CarImage.image_type == "interior"
        ).all()


        assert len(exterior_images) > 0, "FAILED: No exterior images saved"
        assert len(interior_images) > 0, "FAILED: No interior images saved"

        print(f"✓ Exterior images: {len(exterior_images)}")
        print(f"✓ Interior images: {len(interior_images)}")

        for img in all_images:
            url = cast(str, img.image_url)
            assert url and url.startswith("http"), \
                f"FAILED: Invalid image URL found: {url}"
            
        print("✓ All image URLs are valid.")

        seen = set()
        for img in all_images:
            key = (cast(int, img.model_id), cast(str, img.image_url))
            assert key not in seen, \
                f"FAILED: Duplicate image found for model_id={img.model_id}, url={img.image_url}"
            seen.add(key)

        print(f"✓ No duplicate images found")

        print("\nPer model breakdown:")
        models = db.query(CarModel).join(CarBrand).all()

        for m in models:
            model_id = cast(int, m.id)
            brand = cast(str, m.brand.name)
            name = cast(str, m.name)

            ext_count = db.query(CarImage).filter(
                CarImage.model_id == model_id,
                CarImage.image_type == "exterior"
            ).count()

            int_count = db.query(CarImage).filter(
                CarImage.model_id == model_id,
                CarImage.image_type == "interior"
            ).count()

            print(f"  {brand} {name} → {ext_count} exterior, {int_count} interior")

        print("\n" + "="*50)
        print("ALL CHECKS PASSED ✓")
        print("="*50)

    except AssertionError as e:

          print(f"\n{e}")

    finally:
        db.close()

if __name__ == "__main__":
    test_image_pipeline()