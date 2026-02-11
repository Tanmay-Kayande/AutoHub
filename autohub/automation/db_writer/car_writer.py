from sqlalchemy.orm import Session
from autohub.database.model import CarBrand, CarModel, CarVariant, CarSpec

def write_car_payload(payload: dict, db: Session) -> None:
    """
    Insert normalized car payload into DB.
    Safe to re-run
    """

    brand = db.query(CarBrand).filter_by(name=payload["brand"]).first()
    if not brand:
        brand = CarBrand(name=payload["brand"])
        db.add(brand)
        db.flush()

    
    model_data = payload["model"]
    model = (
        db.query(CarModel).filter_by(name=model_data["name"], brand_id=brand.id).first()
    )

    if not model:
        model = CarModel(
            name=model_data["name"],
            brand_id=brand.id,
            body_type=model_data["body_type"],
            launch_date=model_data["launch_date"],
            description=model_data["description"],
        )
        db.add(model)
        db.flush()

    variant_data = payload["variant"]
    variant = (
        db.query(CarVariant).filter_by(model_id=model.id, variant_name=variant_data["name"],).first()
    )

    if not variant:
        variant = CarVariant(
            model_id=model.id,
            variant_name=variant_data["name"],
            fuel_type=variant_data["fuel_type"],
            transmission=variant_data["transmission"],
            price=variant_data["price"],
        )
        db.add(variant)
        db.flush()


    spec_data = payload["spec"]

    if variant.specs:
        for key, value in spec_data.items():
            if value is not None:
                setattr(variant.specs, key, value)
    else:
        spec = CarSpec(
            variant_id=variant.id,
            **spec_data,
        )
        db.add(spec)

    db.commit()
