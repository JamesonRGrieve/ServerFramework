# Endpoint Patterns Quick Reference

> **Full documentation:** [EP.Abstraction.md](EP.Abstraction.md) | **Common patterns:** [Framework.md](../Framework.md)

## Quick Start Checklist

1. Create Pydantic model with `Network` inner class
2. Create BLL manager inheriting `RouterMixin` + `AbstractBLLManager`
3. Set `BaseModel` class attribute
4. Register router: `app.include_router(Manager.Router(model_registry))`

```python
class ItemManager(AbstractBLLManager, RouterMixin):
    BaseModel = Item
    # Done - 8 CRUD endpoints auto-generated
```

## Request/Response Formats

### Single Resource

```python
# POST /items
{"item": {"name": "Widget", "price": 9.99}}

# Response
{"item": {"id": "uuid", "name": "Widget", "price": 9.99, "created_at": "..."}}
```

### Batch Create

```python
# POST /items
{"items": [{"name": "A"}, {"name": "B"}]}

# Response
{"items": [{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]}
```

### Batch Update

```python
# PUT /items/batch
{"item": {"price": 19.99}, "target_ids": ["id1", "id2"]}
```

### Batch Delete

```python
# DELETE /items/batch
{"target_ids": ["id1", "id2"]}
```

### Search

```python
# POST /items/search
{"item": {"status": "active", "min_price": 10}}
```

## Authentication Cheatsheet

| Scenario | Config |
|----------|--------|
| All JWT (default) | `auth_type = AuthType.JWT` |
| All public | `auth_type = AuthType.NONE` |
| Public read, JWT write | `route_auth_overrides = {RouteType.LIST: AuthType.NONE, RouteType.GET: AuthType.NONE}` |
| System entity | Set `is_system_entity=True` on model (auto API_KEY for writes) |

## Query Parameter Patterns

```bash
# Field projection
GET /items?fields=id,name,price

# Include relations
GET /items?include=category,supplier

# Combined
GET /items/{id}?fields=id,name&include=category

# Pagination
GET /items?offset=20&limit=10&sort_by=created_at&sort_order=desc
```

## Network Model Template

```python
class Item(BaseModel):
    id: str
    name: str

    class Network:
        class GET(BaseModel):
            include: Optional[List[str]] = None
            fields: Optional[List[str]] = None

        class LIST(BaseModel):
            include: Optional[List[str]] = None
            fields: Optional[List[str]] = None
            offset: int = 0
            limit: int = 100
            sort_by: Optional[str] = None
            sort_order: str = "asc"

        class POST(BaseModel):
            item: ItemCreate  # singular, snake_case

        class PUT(BaseModel):
            item: ItemUpdate

        class SEARCH(BaseModel):
            item: ItemSearch

        class ResponseSingle(BaseModel):
            item: Item  # singular

        class ResponsePlural(BaseModel):
            items: List[Item]  # plural
```

## Custom Route Patterns

### Instance Method

```python
custom_routes = [
    CustomRouteConfig(
        path="/{id}/publish",
        method=HTTPMethod.POST,
        function="publish",  # method name
    ),
]

def publish(self, id: str) -> Item:
    return self.update(id, published=True)
```

### With Auth Override

```python
CustomRouteConfig(
    path="/public-stats",
    method=HTTPMethod.GET,
    function="get_stats",
    auth_type=AuthType.NONE,
)
```

## Nested Resource Pattern

```python
nested_resources = {
    "comments": NestedResourceConfig(
        child_resource_name="comment",
        manager_property="Comment_manager",
        child_manager_class=CommentManager,
        routes_to_register=[RouteType.GET, RouteType.LIST, RouteType.CREATE],
    ),
}

@property
def Comment_manager(self):
    return CommentManager(parent_id=self.target_id, requester_id=self.requester_id)
```

**Result:** `GET/POST /items/{item_id}/comments`

## Error Response Format

```json
{
    "detail": "Item not found",
    "status_code": 404,
    "errors": [{"field": "id", "message": "Item with id 'abc' not found"}]
}
```

## Testing Pattern

```python
class TestItemEndpoints(AbstractEPTest):
    base_endpoint = "item"
    entity_name = "item"
    required_fields = ["name"]
    string_field_to_update = "name"
    create_fields = {"name": lambda: f"Test {faker.word()}"}
    update_fields = {"name": "Updated Name"}
```

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Network model field name mismatch | Field name must match resource name (`item` not `data`) |
| Missing `BaseModel` attribute | Add `BaseModel = YourModel` to manager |
| Wrong plural form | Check `ResponsePlural` uses correct plural (`items` not `item_list`) |
| Auth not working | Check `route_auth_overrides` uses `RouteType` enum, not strings |
| 422 on create | Verify request body structure matches `Network.POST` |
