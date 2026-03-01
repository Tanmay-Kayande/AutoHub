from pydantic import BaseModel, Field
from typing import Optional, List

class User(BaseModel):
    id: int
    name: str = Field(..., max_length=50)
    email: str = Field(..., pattern=r'^\S+@\S+\.\S+$')
    gender: str = Field(..., pattern=r'^(M|F|O)$')
    location: str = Field(..., max_length=100)
    password: str = Field(..., min_length=8, max_length=100)

    model_config = {
        "json_schema_extra": {
            "example": {
                "id": 1,
                "name": "Raju",
                "email": "raju@example.com",
                "gender": "M",
                "location": "India",
                "password": "securepassword123"
            }
        }
    }

class Login(BaseModel):
    email: str = Field(..., pattern=r'^\S+@\S+\.\S+$')
    password: str = Field(..., min_length=8, max_length=100)

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

# Brand Schemas
class BrandBase(BaseModel):
    name: str = Field(..., max_length=100)


class BrandCreate(BrandBase):
    pass


class BrandRead(BrandBase):
    id: int

    model_config = {"from_attributes": True}

# Model Schemas
class ModelBase(BaseModel):
    name: str = Field(..., max_length=100)
    brand_id: int
    body_type: Optional[str] = None
    launch_date: Optional[str] = None
    description: Optional[str] = Field(None, max_length=500)


class ModelCreate(ModelBase):
    pass


class ModelRead(ModelBase):
    id: int

    model_config = {"from_attributes": True}

# Variant Schemas
class VariantBase(BaseModel):
    model_id: int
    variant_name: str = Field(..., max_length=120)
    fuel_type: Optional[str] = None
    transmission: Optional[str] = None
    price: Optional[float] = Field(None, ge=0)


class VariantCreate(VariantBase):
    pass


class VariantRead(VariantBase):
    id: int

    model_config = {"from_attributes": True}

# Spec Schemas
class SpecBase(BaseModel):
    variant_id: int
    engine: Optional[str] = None
    engine_capacity: Optional[float] = Field(None, ge=0)
    power: Optional[str] = None
    torque: Optional[str] = None
    mileage: Optional[float] = Field(None, ge=0)


class SpecCreate(SpecBase):
    pass


class SpecRead(SpecBase):
    id: int

    model_config = {"from_attributes": True}

# Image Schemas
class ImageBase(BaseModel):
    model_id: int
    image_url: str = Field(..., max_length=300)


class ImageCreate(ImageBase):
    pass


class ImageRead(ImageBase):
    id: int

    model_config = {"from_attributes": True}

# Foe Nested Response
class SpecNested(BaseModel):
    engine: Optional[str]
    engine_capacity: Optional[float]
    power: Optional[str]
    torque: Optional[str]
    mileage: Optional[float]

    model_config = {"from_attributes": True}


class VariantNested(BaseModel):
    id: int
    variant_name: str
    fuel_type: Optional[str]
    transmission: Optional[str]
    price: Optional[float]
    specs: Optional[SpecNested]

    model_config = {"from_attributes": True}


class ModelNested(BaseModel):
    id: int
    name: str
    body_type: Optional[str]
    launch_date: Optional[str]
    description: Optional[str]
    variants: List[VariantNested] = []
    images: List[ImageRead] = []

    model_config = {"from_attributes": True}


class BrandNested(BaseModel):
    id: int
    name: str
    models: List[ModelNested] = []

    model_config = {"from_attributes": True}

# News Schemas
class NewsImage(BaseModel):
    image_url: str

    model_config = {"from_attributes": True}


class NewsBase(BaseModel):
    title: str = Field(..., max_length=200)
    content: str = Field(..., max_length=2000)
    published_at: str = Field(
        ...,
        pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'
    )


class NewsCreate(NewsBase):
    news_images: List[NewsImage] = []


class NewsRead(NewsBase):
    id: int
    news_images: List[NewsImage] = []

    model_config = {"from_attributes": True}


class NewsUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    content: Optional[str] = Field(None, max_length=2000)
    published_at: Optional[str] = Field(
        None,
        pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$'
    )
    news_images: Optional[List[NewsImage]] = None
