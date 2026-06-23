from fastapi import APIRouter, Depends, Form, Header, HTTPException
from ..services.auth import login, get_current_user

router = APIRouter()


@router.post("/auth/token")
def auth_token(username: str = Form(...), password: str = Form(...)):
    result = login(username, password)
    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Invalid credentials"))
    return {"access_token": result["token"], "token_type": "bearer", "role": result["role"]}


async def require_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization[7:]
    user = get_current_user(token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return user


@router.get("/auth/me")
async def auth_me(user: dict = Depends(require_user)):
    return {"username": user["username"], "role": user["role"], "ok": True}
