import time
from ..database import get_db
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

USERS = [
    {"username": "admin", "role": "admin", "password": "admin123"},
]


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def authenticate_user(username: str, password: str):
    for user in USERS:
        if user["username"] == username and user["password"] == password:
            return {"username": user["username"], "role": user["role"]}
    return None


def get_user_by_username(username: str):
    for user in USERS:
        if user["username"] == username:
            return {"username": user["username"], "role": user["role"]}
    return None