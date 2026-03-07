from sqlalchemy.orm import Session
from typing import cast
from autohub.database.connection import session_local
from autohub.database.model import CarModel, CarBrand
from autohub.automation.images.image_fetcher import fetch_car_images
from autohub.automation.images.image_writer import write_car_images

def run_image_pipeline():

    db: Session = session_local()

    try:
        models = (
            db.query(CarModel)
            .join(CarBrand)
            .all()
        )

        if not models:
            print("[ImagePipeline] No car models found in DB.")
            return
        
        print(f"[ImagePipeline] Found {len(models)} models to process.")

        total_exterior = 0
        total_interior = 0
        failed_models = []

        for car_model in models:

            brand_name = cast(str, car_model.brand.name)
            model_name = cast(str, car_model.name)
            model_id = cast(int, car_model.id)

            print(f"\n[ImagePipeline] Processing: {brand_name} {model_name}")

            try:

                image_data = fetch_car_images(brand_name, model_name)

                if not image_data["exterior"] and not image_data["interior"]:
                    print(f"[ImagePipeline] No images found for {brand_name} {model_name}, skipping.")
                    continue

                result = write_car_images(
                    model_id=model_id,
                    image_data=image_data,
                    db=db,
                )

                exterior_saved = result["exterior_saved"]
                interior_saved = result["interior_saved"]

                db.commit()

                total_exterior += result["exterior_saved"]
                total_interior += result["interior_saved"]

                print(f"[ImagePipeline] Saved {result['exterior_saved']} exterior, "
                      f"{result['interior_saved']} interior for {brand_name} {model_name}")
                
            except Exception as e:

                db.rollback()
                failed_models.append(f"{brand_name} {model_name}")
                print(f"[ImagePipeline] Failed for {brand_name} {model_name}: {e}")
                continue

        print(f"\n[ImagePipeline] Pipeline completed.")
        print(f"[ImagePipeline] Total exterior saved: {total_exterior}")
        print(f"[ImagePipeline] Total interior saved: {total_interior}")

        if failed_models:
            print(f"[ImagePipeline] Failed models: {', '.join(failed_models)}")

    except Exception as e:

        db.rollback()
        print(f"[ImagePipeline] Pipeline crashed: {e}")

    finally:
        db.close()


if __name__ == "__main__":
    run_image_pipeline()
