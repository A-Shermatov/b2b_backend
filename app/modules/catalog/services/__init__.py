from app.modules.catalog.services.base import CatalogItemServiceBase, CategoryServiceBase
from app.modules.catalog.services.items import CatalogItemMixin
from app.modules.catalog.services.read import CategoryReadMixin


class CategoryService(CategoryReadMixin, CategoryServiceBase):
    pass


class CatalogItemService(CatalogItemMixin, CatalogItemServiceBase):
    pass


__all__ = ["CategoryService", "CatalogItemService"]
