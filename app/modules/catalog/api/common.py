from fastapi import APIRouter

public_router = APIRouter(tags=["public"])
supplier_router = APIRouter(prefix="/catalog", tags=["supplier-catalog"])
catalog_router = APIRouter(prefix="/catalog", tags=["catalog"])
buyer_router = APIRouter(prefix="/catalog", tags=["buyer-catalog"])
