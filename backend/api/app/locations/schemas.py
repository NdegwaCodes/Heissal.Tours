from uuid import UUID
from pydantic import BaseModel, ConfigDict


class LocationBase(BaseModel):
    city: str
    country: str
    lat: float | None = None
    lon: float | None = None


class LocationCreate(LocationBase):
    pass


class LocationRead(LocationBase):
    id: UUID
    slug: str

    model_config = ConfigDict(from_attributes=True)
