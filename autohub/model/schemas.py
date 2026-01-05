from pydantic import BaseModel, Field

class User(BaseModel):
    id: int
    name: str = Field(..., max_length=50)
    email: str = Field(..., pattern=r'^\S+@\S+\.\S+$')
    gender: str = Field(..., pattern=r"^(M|F|O)$")  # Male, Female, Other
    location: str = Field(..., max_length=60)
    password: str = Field(..., min_length=8, max_length=100, pattern=r'^[A-Za-z\d@$!%*#?&]{8,}$')

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

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "raju@example.com",
                "password": "securepassword123"
            }
        }
    }

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: str | None = None

class CarImageRead(BaseModel):
    image_url: str

    model_config = {
        "from_attributes": True
    }


class Car(BaseModel):
    car_name: str = Field(..., max_length=100)
    car_model: str = Field(..., max_length=100)
    car_brand: str = Field(..., max_length=50)
    car_type: str = Field(..., max_length=50)
    car_color: str = Field(..., max_length=50)
    car_launch_date: str = Field(..., pattern=r'^\d{2}-\d{2}-\d{4}$')
    car_engine: str = Field(..., max_length=50)
    car_engine_capacity: float = Field(..., ge=0)
    car_torque: float = Field(..., ge=0)
    car_power: float = Field(..., ge=0)
    car_fuel: str = Field(..., max_length=50)
    car_mileage: float = Field(..., ge=0) #kmpl
    car_transmission: str = Field(..., max_length=50)
    car_price: float = Field(..., ge=0)
    car_description: str | None = Field(None, max_length=500)

    car_images: list[CarImageRead] = Field(default_factory=list, description="List of car image objects")

    model_config = {
        "from_attributes": True, # Replaces old 'orm_mode = True'
        "json_schema_extra": {
            "example": {
                "car_name": "Camry",
                "car_model": "Camry 2025 XSE",
                "car_brand": "Toyota",
                "car_type": "Sedan",
                "car_color": "Celestial Silver",
                "car_launch_date": "15-10-2024",
                "car_engine": "2.5L 4-Cylinder Hybrid",
                "car_engine_capacity": 2.5,
                "car_torque": 221.0,
                "car_power": 225.0,
                "car_fuel": "Hybrid",
                "car_mileage": 22.5,
                "car_transmission": "eCVT",
                "car_price": 35000.00,
                "car_description": "All-new hybrid powertrain with luxury interior.",
                "car_images": [
                    {"image_url": "https://example.com/front.jpg"},
                    {"image_url": "https://example.com/interior.jpg"}
                ]
            }
        }
    }

class CarUpdate(BaseModel):
    car_name: str | None = Field(None, max_length=100)
    car_model: str | None = Field(None, max_length=100)
    car_brand: str | None = Field(None, max_length=50)
    car_type: str | None = Field(None, max_length=50)
    car_color: str | None = Field(None, max_length=50)
    car_launch_date: str | None = Field(None, pattern=r'^\d{2}-\d{2}-\d{4}$')
    car_engine: str | None = Field(None, max_length=50)
    car_engine_capacity: float | None = Field(None, ge=0)
    car_torque: float | None = Field(None, ge=0)
    car_power: float | None = Field(None, ge=0)
    car_fuel: str | None = Field(None, max_length=50)
    car_mileage: float | None = Field(None, ge=0) #kmpl
    car_transmission: str | None = Field(None, max_length=50)
    car_price: float | None = Field(None, ge=0)
    car_description: str | None = Field(None, max_length=500)

class NewsImage(BaseModel):
    image_url: str

    model_config = {
        "from_attributes": True
    }

class News(BaseModel):
    title: str = Field(..., max_length=200)
    content: str = Field(..., max_length=2000)
    published_at: str = Field(..., pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')  # ISO 8601 format
    news_images: list[NewsImage] = Field(default_factory=list, description="List of news image objects")


    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "New Electric Car Launched",
                "content": "The latest electric car model has been launched with advanced features and improved range.",
                "published_at": "2024-06-15T10:00:00Z",
                "news_images": [
                    {"image_url": "https://example.com/news1.jpg"},
                    {"image_url": "https://example.com/news2.jpg"}
                ]
            }
        }
    }


class NewsUpdate(BaseModel):
    title: str | None = Field(None, max_length=200)
    content: str | None = Field(None, max_length=2000)
    published_at: str | None = Field(None, pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
    news_images: list[NewsImage] | None = None

    model_config = {
        "json_schema_extra": {
            "example": {
                "content": "New Tata Sierra has just launch with great performance and feathers with competitive pricing.",
                "news_images": [
                    {"image_url": "https://example2.com/news1.jpg"},
                    {"image_url": "https://example1.com/news2.jpg"}
                ]
            }
        }
    }

