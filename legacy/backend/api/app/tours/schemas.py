from uuid import UUID
from pydantic import BaseModel, ConfigDict


class TourBase(BaseModel):
    title: str
    tour_type: str
    location_id: UUID
    base_price: float
    currency: str = "USD"
    min_guests: int = 1
    max_guests: int = 20
    duration_days: int
    duration_nights: int
    short_description: str | None = None


class TourCreate(TourBase):
    pass


class TourRead(TourBase):
    id: UUID
    slug: str
    average_rating: float
    review_count: int
    status: str

    model_config = ConfigDict(from_attributes=True)
