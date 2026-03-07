from sqlalchemy.orm import Session
from autohub.database.model import CarImage

def write_car_images(model_id: int, image_data: dict, db: Session) -> dict:

    exterior_count = 0
    interior_count = 0

    for image_type, urls in image_data.items():

        for url in urls:
            
            already_exists = db.query(CarImage).filter(CarImage.model_id == model_id, CarImage.image_url == url).first()

            if already_exists:
                print(f"[ImageWriter] Skipping duplicate image: {url}")
                continue

            image = CarImage(
                model_id=model_id,
                image_url=url,
                image_type=image_type
            )

            db.add(image)

            if image_type == "exterior":
                exterior_count += 1
            else:
                interior_count += 1

            
    return {
    "exterior_saved": exterior_count,
    "interior_saved": interior_count,
    }
        
