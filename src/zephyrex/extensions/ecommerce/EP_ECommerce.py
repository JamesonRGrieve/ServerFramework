# from datetime import datetime, timezone
# from typing import Dict, List, Optional
#
# from fastapi import APIRouter, Depends, HTTPException, Path, Query
# from pydantic import BaseModel, Field
# from sqlalchemy.orm import Session
#
# from database.DB_Ecommerce import EcommerceProvider
# from endpoints.AbstractEndpointRouter import AbstractEPRouter
# from endpoints.EP_Auth import get_current_user
# from logic.BLL_Ecommerce import EcommerceManager
#
#
# class ProviderCreate(BaseModel):
#     name: str = Field(..., description="Name for this provider connection")
#     provider_type: str = Field(
#         ..., description="Type of e-commerce provider (walmart, amazon, shopify, etc.)"
#     )
#     api_key: Optional[str] = Field(None, description="API key for the provider")
#     access_token: Optional[str] = Field(
#         None, description="Access token for the provider"
#     )
#     refresh_token: Optional[str] = Field(
#         None, description="Refresh token for the provider"
#     )
#     settings: Optional[Dict] = Field({}, description="Additional provider settings")
#
#
# class ProviderUpdate(BaseModel):
#     name: Optional[str] = Field(None, description="Name for this provider connection")
#     api_key: Optional[str] = Field(None, description="API key for the provider")
#     access_token: Optional[str] = Field(
#         None, description="Access token for the provider"
#     )
#     refresh_token: Optional[str] = Field(
#         None, description="Refresh token for the provider"
#     )
#     settings: Optional[Dict] = Field(None, description="Additional provider settings")
#     is_active: Optional[bool] = Field(
#         None, description="Whether this provider is active"
#     )
#
#
# class EcommerceRouter(AbstractEPRouter):
#     """
#     Endpoint router for e-commerce operations.
#     """
#
#     def __init__(self, session=None):
#         super().__init__(session)
#         self.router = APIRouter(prefix="/ecommerce", tags=["ecommerce"])
#         self.setup_routes()
#
#     def setup_routes(self):
#         """Configure the routes for this router."""
#
#         # Provider management
#         self.router.post(
#             "/providers",
#             response_model=Dict,
#             summary="Create a new e-commerce provider",
#         )
#         self.router.get(
#             "/providers",
#             response_model=List[Dict],
#             summary="Get all e-commerce providers for the current user",
#         )
#         self.router.get(
#             "/providers/{provider_id}",
#             response_model=Dict,
#             summary="Get a specific e-commerce provider",
#         )
#         self.router.put(
#             "/providers/{provider_id}",
#             response_model=Dict,
#             summary="Update an e-commerce provider",
#         )
#         self.router.delete(
#             "/providers/{provider_id}",
#             response_model=Dict,
#             summary="Delete an e-commerce provider",
#         )
#
#         # Orders
#         self.router.get(
#             "/orders",
#             response_model=List[Dict],
#             summary="Get orders from the e-commerce platform",
#         )
#         self.router.post(
#             "/orders/{order_id}/acknowledge",
#             response_model=Dict,
#             summary="Acknowledge an order",
#         )
#         self.router.post(
#             "/orders/{order_id}/cancel", response_model=Dict, summary="Cancel an order"
#         )
#
#         # Inventory and products
#         self.router.get(
#             "/inventory",
#             response_model=List[Dict],
#             summary="Get inventory from the e-commerce platform",
#         )
#         self.router.put(
#             "/inventory/{sku}",
#             response_model=Dict,
#             summary="Update inventory for a specific SKU",
#         )
#         self.router.get(
#             "/products",
#             response_model=List[Dict],
#             summary="Get products from the e-commerce platform",
#         )
#         self.router.put(
#             "/products/{sku}", response_model=Dict, summary="Update a product"
#         )
#
#         # Returns
#         self.router.get(
#             "/returns",
#             response_model=List[Dict],
#             summary="Get returns from the e-commerce platform",
#         )
#         self.router.post(
#             "/returns/{return_id}/process",
#             response_model=Dict,
#             summary="Process a return",
#         )
#
#         # Reports
#         self.router.post("/reports", response_model=Dict, summary="Generate a report")
#
#     async def create_provider(
#         self,
#         provider_data: ProviderCreate,
#         user: Dict = Depends(get_current_user),
#         db: Session = Depends(lambda: next(self.session)),
#     ) -> Dict:
#         """
#         Create a new e-commerce provider.
#         """
#         try:
#             new_provider = EcommerceProvider(
#                 user_id=user["id"],
#                 name=provider_data.name,
#                 provider_type=provider_data.provider_type,
#                 api_key=provider_data.api_key,
#                 access_token=provider_data.access_token,
#                 refresh_token=provider_data.refresh_token,
#                 settings=provider_data.settings,
#             )
#
#             db.add(new_provider)
#             db.commit()
#             db.refresh(new_provider)
#
#             return {
#                 "id": new_provider.id,
#                 "name": new_provider.name,
#                 "provider_type": new_provider.provider_type,
#                 "created_at": new_provider.created_at.isoformat(),
#             }
#         except Exception as e:
#             db.rollback()
#             raise HTTPException(
#                 status_code=500, detail=f"Failed to create provider: {str(e)}"
#             )
#
#     async def get_providers(
#         self,
#         user: Dict = Depends(get_current_user),
#         db: Session = Depends(lambda: next(self.session)),
#     ) -> List[Dict]:
#         """
#         Get all e-commerce providers for the current user.
#         """
#         try:
#             providers = (
#                 db.query(EcommerceProvider)
#                 .filter(EcommerceProvider.user_id == user["id"])
#                 .all()
#             )
#
#             return [
#                 {
#                     "id": provider.id,
#                     "name": provider.name,
#                     "provider_type": provider.provider_type,
#                     "is_active": provider.is_active,
#                     "created_at": provider.created_at.isoformat(),
#                 }
#                 for provider in providers
#             ]
#         except Exception as e:
#             raise HTTPException(
#                 status_code=500, detail=f"Failed to get providers: {str(e)}"
#             )
#
#     async def get_provider(
#         self,
#         provider_id: str = Path(..., description="ID of the provider to get"),
#         user: Dict = Depends(get_current_user),
#         db: Session = Depends(lambda: next(self.session)),
#     ) -> Dict:
#         """
#         Get a specific e-commerce provider.
#         """
#         try:
#             provider = (
#                 db.query(EcommerceProvider)
#                 .filter(
#                     EcommerceProvider.id == provider_id,
#                     EcommerceProvider.user_id == user["id"],
#                 )
#                 .first()
#             )
#
#             if not provider:
#                 raise HTTPException(status_code=404, detail="Provider not found")
#
#             return {
#                 "id": provider.id,
#                 "name": provider.name,
#                 "provider_type": provider.provider_type,
#                 "settings": provider.settings,
#                 "is_active": provider.is_active,
#                 "created_at": provider.created_at.isoformat(),
#                 "updated_at": provider.updated_at.isoformat(),
#             }
#         except HTTPException:
#             raise
#         except Exception as e:
#             raise HTTPException(
#                 status_code=500, detail=f"Failed to get provider: {str(e)}"
#             )
#
#     async def update_provider(
#         self,
#         provider_data: ProviderUpdate,
#         provider_id: str = Path(..., description="ID of the provider to update"),
#         user: Dict = Depends(get_current_user),
#         db: Session = Depends(lambda: next(self.session)),
#     ) -> Dict:
#         """
#         Update an e-commerce provider.
#         """
#         try:
#             provider = (
#                 db.query(EcommerceProvider)
#                 .filter(
#                     EcommerceProvider.id == provider_id,
#                     EcommerceProvider.user_id == user["id"],
#                 )
#                 .first()
#             )
#
#             if not provider:
#                 raise HTTPException(status_code=404, detail="Provider not found")
#
#             if provider_data.name:
#                 provider.name = provider_data.name
#             if provider_data.api_key:
#                 provider.api_key = provider_data.api_key
#             if provider_data.access_token:
#                 provider.access_token = provider_data.access_token
#             if provider_data.refresh_token:
#                 provider.refresh_token = provider_data.refresh_token
#             if provider_data.settings:
#                 provider.settings = provider_data.settings
#             if provider_data.is_active is not None:
#                 provider.is_active = provider_data.is_active
#
#             provider.updated_at = datetime.now(timezone.utc)
#
#             db.commit()
#             db.refresh(provider)
#
#             return {
#                 "id": provider.id,
#                 "name": provider.name,
#                 "provider_type": provider.provider_type,
#                 "is_active": provider.is_active,
#                 "updated_at": provider.updated_at.isoformat(),
#             }
#         except HTTPException:
#             raise
#         except Exception as e:
#             db.rollback()
#             raise HTTPException(
#                 status_code=500, detail=f"Failed to update provider: {str(e)}"
#             )
#
#     async def delete_provider(
#         self,
#         provider_id: str = Path(..., description="ID of the provider to delete"),
#         user: Dict = Depends(get_current_user),
#         db: Session = Depends(lambda: next(self.session)),
#     ) -> Dict:
#         """
#         Delete an e-commerce provider.
#         """
#         try:
#             provider = (
#                 db.query(EcommerceProvider)
#                 .filter(
#                     EcommerceProvider.id == provider_id,
#                     EcommerceProvider.user_id == user["id"],
#                 )
#                 .first()
#             )
#
#             if not provider:
#                 raise HTTPException(status_code=404, detail="Provider not found")
#
#             db.delete(provider)
#             db.commit()
#
#             return {"message": "Provider deleted successfully"}
#         except HTTPException:
#             raise
#         except Exception as e:
#             db.rollback()
#             raise HTTPException(
#                 status_code=500, detail=f"Failed to delete provider: {str(e)}"
#             )
#
#     async def get_orders(
#         self,
#         provider_id: str = Query(..., description="ID of the provider to use"),
#         status: Optional[str] = Query(None, description="Order status to filter by"),
#         days: int = Query(30, description="Number of days to look back"),
#         limit: int = Query(50, description="Maximum number of orders to retrieve"),
#         user: Dict = Depends(get_current_user),
#         db: Session = Depends(lambda: next(self.session)),
#     ) -> List[Dict]:
#         """
#         Get orders from the e-commerce platform.
#         """
#         try:
#             provider = (
#                 db.query(EcommerceProvider)
#                 .filter(
#                     EcommerceProvider.id == provider_id,
#                     EcommerceProvider.user_id == user["id"],
#                 )
#                 .first()
#             )
#
#             if not provider:
#                 raise HTTPException(status_code=404, detail="Provider not found")
#
#             manager = EcommerceManager(session=db)
#             await manager.initialize(
#                 user_id=user["id"],
#                 provider_type=provider.provider_type,
#                 api_key=provider.api_key,
#                 ECOMMERCE_ACCESS_TOKEN=provider.access_token,
#                 ECOMMERCE_REFRESH_TOKEN=provider.refresh_token,
#                 **(provider.settings or {}),
#             )
#
#             orders = await manager.get_recent_orders(days=days, status=status or "")
#             return orders
#         except HTTPException:
#             raise
#         except Exception as e:
#             raise HTTPException(
#                 status_code=500, detail=f"Failed to get orders: {str(e)}"
#             )
#
#     # Additional endpoint implementations would go here
#     # Following the same pattern for inventory, products, returns, and reports
