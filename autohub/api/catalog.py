from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.exc import IntegrityError

from autohub.database import model
from autohub.database.connection import get_db
from autohub.model.schemas import (BrandCreate, BrandRead, BrandNested,
                                      ModelCreate, ModelRead,
                                      VariantCreate, VariantRead,
                                      SpecCreate, SpecRead,
                                      ImageCreate, ImageRead)

router = APIRouter(
    prefix="/catalog",
    tags=["catalog"]
)

# Brands

@router.post("/brands",  response_model=BrandRead, status_code=status.HTTP_201_CREATED)
def create_brand(request: BrandCreate, db: Session = Depends(get_db)):
    brand = model.CarBrand(name=request.name)

    try:
        db.add(brand)
        db.commit()
        db.refresh(brand)
        return brand
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Brand already exists")
    

@router.get("/brands", response_model=list[BrandRead])
def get_brands(db: Session = Depends(get_db)):
    return db.query(model.CarBrand).all()

# Models

@router.post("/models", response_model=ModelRead, status_code=status.HTTP_201_CREATED)
def create_model(request: ModelCreate, db: Session = Depends(get_db)):
    
    brand = db.query(model.CarBrand).filter_by(id=request.brand_id).first()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")
    
    car_model = model.CarModel(**request.model_dump())

    try:
        db.add(car_model)
        db.commit()
        db.refresh(car_model)
        return car_model
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Model already exists for this brand")
    
@router.get("/models", response_model=list[ModelRead])
def get_models(db: Session = Depends(get_db)):
    return db.query(model.CarModel).all()

# Variants

@router.post("/variants", response_model=VariantRead, status_code=status.HTTP_201_CREATED)
def create_variant(request: VariantCreate, db: Session = Depends(get_db)):
    
    model_obj = db.query(model.CarModel).filter_by(id=request.model_id).first()
    if not model_obj:
        raise HTTPException(status_code=404, detail="Model not found")
    
    variant = model.CarVariant(**request.model_dump())

    try:
        db.add(variant)
        db.commit()
        db.refresh(variant)
        return variant
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Variant already exists for this model")
    
@router.get("/variants", response_model=list[VariantRead])
def get_variants(db: Session = Depends(get_db)):
    return db.query(model.CarVariant).all()

# Specs

@router.post("/specs", response_model=SpecRead, status_code=status.HTTP_201_CREATED)
def create_spec(request: SpecCreate, db: Session = Depends(get_db)):

    variant = db.query(model.CarVariant).filter_by(id=request.variant_id).first()
    if not variant:
        raise HTTPException(status_code=404, detail="Variant not found")

    spec = model.CarSpec(**request.model_dump())

    try:
        db.add(spec)
        db.commit()
        db.refresh(spec)
        return spec
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=400, detail="Spec already exists for this variant")

@router.get("/specs", response_model=list[SpecRead])
def get_specs(db: Session = Depends(get_db)):
    return db.query(model.CarSpec).all()

# Image

@router.post("/images", response_model=ImageRead, status_code=status.HTTP_201_CREATED)
def add_image(request: ImageCreate, db: Session = Depends(get_db)):

    model_obj = db.query(model.CarModel).filter_by(id=request.model_id).first()
    if not model_obj:
        raise HTTPException(status_code=404, detail="Model not found")

    image = model.CarImage(**request.model_dump())

    db.add(image)
    db.commit()
    db.refresh(image)

    return image


@router.get("/images", response_model=list[ImageRead])
def get_images(db: Session = Depends(get_db)):
    return db.query(model.CarImage).all()

# Nested Catalog

@router.get("", response_model=list[BrandNested])
def get_catalog(db: Session = Depends(get_db)):
    brands = (
        db.query(model.CarBrand)
        .options(
            joinedload(model.CarBrand.models)
            .joinedload(model.CarModel.variants)
            .joinedload(model.CarVariant.specs),
            joinedload(model.CarBrand.models)
            .joinedload(model.CarModel.images)
        )
        .all()
    )

    return brands
    