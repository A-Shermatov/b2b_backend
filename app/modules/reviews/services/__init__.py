from app.modules.reviews.services.base import ReviewServiceBase
from app.modules.reviews.services.create import ReviewCreateMixin
from app.modules.reviews.services.read import ReviewReadMixin


class ReviewService(ReviewReadMixin, ReviewCreateMixin, ReviewServiceBase):
    pass


__all__ = ["ReviewService"]
