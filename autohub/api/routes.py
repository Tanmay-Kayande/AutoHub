from fastapi import APIRouter, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from autohub.model.schemas import User
from autohub.database.connection import engine, session_local
from sqlalchemy.orm import Session
from fastapi import Depends
from autohub.database import model
from autohub.database.connection import get_db
from passlib.context import CryptContext
from autohub.api.login import get_current_user
from typing import cast

router = APIRouter()

pwd_context = CryptContext(schemes=["argon2","bcrypt"], deprecated="auto")

@router.get("/status")
def get_status():    
    return {"status": "API is running"}

@router.post("/signin")
def sign_in(request: User, db: Session = Depends(get_db)):
    user = db.query(model.User).filter(model.User.email == request.email).first()
    if not user:
        raise HTTPException(status_code=400, detail="Invalid email or password")
    # verify hashed password
    if not pwd_context.verify(request.password, cast(str, user.hashed_password)):
        raise HTTPException(status_code=400, detail="Invalid email or password")
    return {"message": f"User {user.name} signed in successfully"}

@router.post("/signup")
def sign_up(request: User, db: Session = Depends(get_db)):
    # defensive check: bcrypt limit is 72 bytes
    pwd_bytes = request.password.encode("utf-8")
    if len(pwd_bytes) > 72:
        raise HTTPException(status_code=400, detail="Password too long (max 72 bytes)")

    try:
        hashed = pwd_context.hash(request.password)
    except ValueError as exc:
        # passlib/bcrypt raised (e.g. length), surface friendly error
        raise HTTPException(status_code=400, detail=str(exc))

    new_user = model.User(
        name=request.name,
        email=request.email,
        gender=request.gender,
        location=request.location,
        hashed_password=hashed,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return {"message": f"User {request.name} signed up successfully"}

@router.get("/users")
def get_users(request: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db), current_user: model.User = Depends(get_current_user)):
    users = db.query(model.User).all()
    return users

@router.delete("/users/{user_id}")
def delete_user(User_id: int, db : Session = Depends(get_db)):
    user = db.query(model.User).filter(model.User.id == User_id).delete(synchronize_session=False)
    db.commit()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"message": f"User with id {User_id} deleted successfully"}
                
@router.put("/users/{user_id}")
def update_user(user_id: int, request: User, db: Session = Depends(get_db)):
    user = db.query(model.User).filter(model.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # get dict from Pydantic model (supports v1/v2)
    data = request.model_dump() if hasattr(request, "model_dump") else request.dict()
    data.pop("id", None)  # don't allow changing the primary key

    # handle password separately (ORM column is hashed_password)
    pwd = data.pop("password", None)
    
    # assign remaining fields to the ORM object
    for key, value in data.items():
        if hasattr(user, key):
            setattr(user, key, value)

    if pwd is not None:
        # TODO: hash the password before saving (e.g. bcrypt)
        user.hashed_password = pwd

    db.commit()
    db.refresh(user)
    return {"message": f"User with id {user_id} updated successfully"}