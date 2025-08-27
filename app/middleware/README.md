# Middleware System

This directory contains the middleware components for the Vietnam Hearts application. The middleware system provides logging, CORS handling, error handling, and rate limiting in a modular, maintainable way.

## Overview

The middleware system is designed to:
- **Centralize** common functionality across all endpoints
- **Provide** consistent error handling and logging
- **Protect** the API from abuse with rate limiting
- **Enable** proper CORS handling for web frontend integration
- **Maintain** clean separation of concerns

**Note**: Authentication is handled by **FastAPI router-level dependencies**, not middleware, to avoid conflicts with FastAPI's dependency injection system.

## Middleware Components

### 1. Logging Middleware (`logging_middleware.py`)

Provides comprehensive request/response logging for monitoring and debugging.

**Features:**
- Unique request ID for each request
- Request/response timing
- Performance metrics
- Sensitive data filtering

**Configuration:**
```python
# Enable/disable body logging
log_request_body = True
log_response_body = False
```

### 2. CORS Middleware (`cors_middleware.py`)

Handles Cross-Origin Resource Sharing for web frontend integration.

**Features:**
- Environment-based configuration
- Development vs production origins
- Configurable methods and headers
- Preflight request caching

**Configuration:**
```python
# Development origins
allowed_origins = [
    "http://localhost:3000",
    "http://localhost:8000"
]

# Production origins (update these)
allowed_origins = [
    "https://yourdomain.com",
    "https://admin.yourdomain.com"
]
```

### 3. Error Handling Middleware (`error_handling.py`)

Provides consistent error handling and response formatting.

**Features:**
- Automatic exception catching
- Standardized error response format
- Request ID tracking
- Environment-based error detail level

**Configuration:**
```python
# Include traceback in development
include_traceback = ENVIRONMENT == "development"
```

### 4. Rate Limiting Middleware (`rate_limit_middleware.py`)

Protects the API from abuse by limiting request frequency.

**Features:**
- Category-based rate limiting
- Different limits for different endpoint types
- Client identification (user ID or IP)
- Automatic cleanup of expired entries

**Rate Limits:**
```python
rate_limits = {
    "default": {"requests": 100, "window": 3600},    # 100/hour
    "auth": {"requests": 10, "window": 3600},        # 10/hour
    "admin": {"requests": 1000, "window": 3600},     # 1000/hour
    "public": {"requests": 500, "window": 3600},     # 500/hour
    "bot": {"requests": 200, "window": 3600}         # 200/hour
}
```

## Authentication Architecture

**Authentication is handled by FastAPI router-level dependencies**, not middleware. This approach:

### **Why Router-Level Dependencies?**
- ✅ **FastAPI native** - Uses the framework's intended pattern
- ✅ **No conflicts** - Works seamlessly with dependency injection
- ✅ **Better performance** - No unnecessary middleware processing
- ✅ **Type safety** - Better integration with FastAPI's type system

### **Example Usage:**
```python
# In app/routers/admin.py
admin_api_router = APIRouter(
    prefix="/admin",
    tags=["admin",
    dependencies=[Depends(get_current_admin_user)]  # Router-level auth
)

# Individual endpoints don't need auth dependencies
@admin_api_router.get("/dashboard")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    # Automatically protected by router-level dependency
    pass
```

## Usage

### **Basic Setup**
The middleware is automatically configured in your `main.py`:

```python
from .middleware import setup_middleware

app = FastAPI(...)
setup_middleware(app)  # All middleware configured automatically
```

### **Middleware Order**
Middleware is executed in this order (outermost to innermost):

1. **Error Handling** - Catches unhandled exceptions
2. **Rate Limiting** - Checks request frequency
3. **Logging** - Logs request/response details
4. **CORS** - Handles cross-origin requests

**Note**: Authentication happens at the router level, before middleware execution.

## Customization

### **Adding Custom Middleware**
```python
# In app/middleware/__init__.py
from .custom_middleware import CustomMiddleware

def setup_middleware(app: FastAPI) -> None:
    # Add custom middleware
    app.add_middleware(CustomMiddleware)
    
    # ... existing middleware
```

### **Modifying Rate Limits**
```python
# In app/middleware/rate_limit_middleware.py
self.rate_limits = {
    "custom": {"requests": 50, "window": 1800},  # 50 per 30 minutes
    # ... existing limits
}
```

## Testing

Run the middleware tests:

```bash
poetry run pytest tests/test_middleware.py -v
```

## Monitoring

### **Request IDs**
Every request gets a unique ID that appears in:
- Response headers (`X-Request-ID`)
- Log messages
- Error responses

### **Rate Limit Headers**
Rate limit information is included in response headers:
- `X-RateLimit-Limit` - Maximum requests allowed
- `X-RateLimit-Remaining` - Requests remaining in window
- `X-RateLimit-Reset` - When the limit resets
- `X-RateLimit-Category` - Endpoint category

### **Logging**
Middleware logs are available in your application logs with the component name:
- `logging_middleware`
- `rate_limit_middleware`
- `cors_middleware`
- `error_handling`

## Production Considerations

### **Rate Limiting Storage**
For production, consider replacing in-memory storage with Redis:

```python
# In rate_limit_middleware.py
import redis

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        self.redis = redis.Redis(host='localhost', port=6379, db=0)
        # ... rest of implementation
```

### **CORS Origins**
Update the production origins in `cors_middleware.py`:

```python
allowed_origins = [
    "https://vietnamhearts.org",
    "https://admin.vietnamhearts.org",
    "https://volunteer.vietnamhearts.org"
]
```

### **Error Detail Level**
Ensure `include_traceback` is `False` in production:

```python
# In error_handling.py
include_traceback = ENVIRONMENT == "development"
```

## Troubleshooting

### **Common Issues**
1. **Missing headers** - Ensure middleware is added in correct order
2. **Rate limit errors** - Review rate limit configuration
3. **CORS errors** - Verify allowed origins
4. **Authentication issues** - Check router-level dependencies, not middleware

### **Debug Mode**
Enable debug logging for middleware:

```python
# In your logging configuration
logger = get_logger("middleware")
logger.setLevel(logging.DEBUG)
```

## Contributing

When adding new middleware:

1. Follow the existing pattern using `BaseHTTPMiddleware`
2. Include comprehensive docstrings
3. Add appropriate tests
4. Update this README
5. Consider performance implications
6. Include proper error handling
7. **Don't duplicate authentication logic** - use router-level dependencies instead
