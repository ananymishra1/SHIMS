import json
import os
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# No hard-coded default credentials for public repositories.
# Set TODO_DEMO_USERS as a JSON array, e.g.:
#   [{"username": "admin", "role": "admin", "password": "..."}]
_USERS_JSON = os.getenv("TODO_DEMO_USERS", "[]")
try:
    USERS = json.loads(_USERS_JSON)
    if not isinstance(USERS, list):
        USERS = []
except Exception:
    USERS = []


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def authenticate_user(username: str, password: str):
    for user in USERS:
        if user.get("username") == username and user.get("password") == password:
            return {"username": user["username"], "role": user.get("role", "user")}
    return None


def get_user_by_username(username: str):
    for user in USERS:
        if user.get("username") == username:
            return {"username": user["username"], "role": user.get("role", "user")}
    return None