from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from autohub.database import model
from autohub.database.connection import get_db
from autohub.model.schemas import Car, CarUpdate
from sqlalchemy.exc import IntegrityError

router = APIRouter(
    prefix="/cars",
    tags=["cars"],
)

@router.post("/add_car", status_code=status.HTTP_201_CREATED)
def add_car(request: Car, db: Session = Depends(get_db)):

    data = request.model_dump()
    image_urls = data.pop("car_images", [])

    new_car = model.Car(**data)

    # accept either list[str] or list[dict] with {'image_url': str}
    for img in image_urls:
        if isinstance(img, dict):
            url = img.get("image_url")
        else:
            url = img
        if url:
            image_obj = model.CarImage(image_url=url)
            new_car.car_images.append(image_obj)

    try:
        db.add(new_car)
        db.commit()
        db.refresh(new_car)

        return {
        "status": "success",
        "message": f"Car {request.car_name} added successfully",
        "image_count": len(new_car.car_images)
         }
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Car with this model already exists")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="An unexpected error occurred while adding the car")

@router.get("/", response_model=list[Car])
def get_cars(db: Session = Depends(get_db)):
    cars = db.query(model.Car).all()
    return cars

@router.put("/update_car/{car_id}")
def update_car(car_id: int, request: CarUpdate, db: Session = Depends(get_db)):
    car = db.query(model.Car).filter(model.Car.id == car_id).first()
    if not car:
        raise HTTPException(status_code=404, detail="Car not found")
    
    # only include fields the client actually set; ignore explicit None values
    data = request.model_dump(exclude_unset=True, exclude_none=True)
    data.pop("id", None)  # Prevent updating the ID
    image_urls = data.pop("car_images", [])

    for key, value in data.items():
        # defensive: skip None (already excluded) and empty-string placeholders
        if value is None:
            continue
        if isinstance(value, str) and value.strip() == "":
            continue
        if hasattr(car, key):
            setattr(car, key, value)

    if image_urls:
        existing_urls = {img.image_url for img in car.car_images}
        for img in image_urls:
            if isinstance(img, dict):
                url = img.get("image_url")
            else:
                url = img
            if url and url not in existing_urls:
                car.car_images.append(model.CarImage(image_url=url))

    try:
        db.add(car)
        db.commit()
        db.refresh(car)

        return {
        "status": "success",
        "message": f"Car {car.car_name} updated successfully",
        "image_count": len(car.car_images)
         }
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Car with this model already exists")
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="An unexpected error occurred while updating the car")

@router.delete("/delete_car/{car_id}")
def delete_car(car_id: int, db: Session = Depends(get_db)):
    car = db.query(model.Car).filter(model.Car.id == car_id).first()
    if not car:
        raise HTTPException(status_code=404, detail="Car not found")
    
    try:
        db.delete(car)
        db.commit()
        return {"message": f"Car with id {car_id} deleted successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail="An unexpected error occurred while deleting the car")