from fastapi import FastAPI

from .core.config import settings
from .users.routers import router as users_router, auth_router
from .tours.routers import router as tours_router
from .locations.routers import router as locations_router

app = FastAPI(title=settings.PROJECT_NAME)

api_prefix = settings.API_V1_STR

app.include_router(auth_router, prefix=api_prefix)
app.include_router(users_router, prefix=api_prefix)
app.include_router(locations_router, prefix=api_prefix)
app.include_router(tours_router, prefix=api_prefix)
