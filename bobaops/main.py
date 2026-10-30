"""Application composition and frontend file routes."""
from fastapi import FastAPI
from fastapi.responses import FileResponse

from bobaops.analytics.routes import router as analytics_router
from bobaops.assistant.routes import router as assistant_router
from bobaops.catalog.routes import router as shop_router
from bobaops.core.paths import FRONTEND_DIR
from bobaops.extraction.routes import router as extraction_router
from bobaops.inventory.routes import router as inventory_router
from bobaops.inventory.powder_usage import router as ice_cream_usage_router
from bobaops.sales.routes import router as sales_router
from bobaops.sales.reversals import router as reversals_router

app = FastAPI(title="BobaOps")
for router in (shop_router, sales_router, reversals_router, inventory_router,
               ice_cream_usage_router, analytics_router, assistant_router, extraction_router):
    app.include_router(router)


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / 'dashboard.html', headers={'Cache-Control': 'no-store'})


@app.get('/upload')
def upload_page():
    return FileResponse(FRONTEND_DIR / "index.html", headers={"Cache-Control": "no-store"})


@app.get('/setup')
def shop_setup():
    return FileResponse(FRONTEND_DIR / 'setup.html', headers={'Cache-Control': 'no-store'})


@app.get('/sales')
def saved_sales():
    return FileResponse(FRONTEND_DIR / 'sales.html', headers={'Cache-Control': 'no-store'})


@app.get('/inventory')
def inventory_page():
    return FileResponse(FRONTEND_DIR / 'inventory.html', headers={'Cache-Control': 'no-store'})


@app.get('/chat')
def chat_page():
    return FileResponse(FRONTEND_DIR / 'chat.html', headers={'Cache-Control': 'no-store'})


@app.get('/dashboard')
def dashboard_page():
    return FileResponse(FRONTEND_DIR / 'dashboard.html', headers={'Cache-Control': 'no-store'})


@app.get('/assets/ice-cream-usage.js')
def ice_cream_script():
    return FileResponse(FRONTEND_DIR / 'ice-cream-usage.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})


@app.get('/assets/app.css')
def app_styles():
    return FileResponse(FRONTEND_DIR / 'app.css', media_type='text/css', headers={'Cache-Control': 'no-store'})


@app.get('/assets/ui.js')
def app_ui_script():
    return FileResponse(FRONTEND_DIR / 'ui.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})


@app.get('/assets/receipt-powders.js')
def receipt_powders_script():
    return FileResponse(FRONTEND_DIR / 'receipt-powders.js', media_type='text/javascript', headers={'Cache-Control': 'no-store'})


@app.get('/assets/chartjs-4.5.1.js')
def chartjs_asset():
    return FileResponse(FRONTEND_DIR / 'vendor/chartjs/chart.umd.min.js', media_type='text/javascript')


