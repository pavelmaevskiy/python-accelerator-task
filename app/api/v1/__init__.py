from fastapi import APIRouter

from app.api.v1.admin.routes import router as admin_router
from app.api.v1.auth.routes import router as auth_router
from app.api.v1.me.routes import router as me_router
from app.api.v1.public.routes import router as public_router
from app.api.v1.trainer.routes import router as trainer_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(admin_router)
api_router.include_router(auth_router)
api_router.include_router(me_router)
api_router.include_router(trainer_router)
api_router.include_router(public_router)
