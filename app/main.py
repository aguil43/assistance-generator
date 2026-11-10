"""
Punto de entrada principal del servidor FastAPI (main.py).
Sirve tanto la API REST modular (/api/*) como la interfaz web estática (/),
deshabilitando el caché del navegador en archivos estáticos para garantizar que
siempre se ejecuten los estilos y scripts más recientes.
"""

from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router as api_router

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="Sistema Analizador de Asistencia y Reportes PDF",
    description=(
        "Backend modular en FastAPI para analizar registros de reloj checador, "
        "calcular retardos, faltas y descuentos salariales, gestionar festivos y "
        "justificaciones, y exportar reportes PDF protegidos contra edición."
    ),
    version="2.1.0",
)


@app.middleware("http")
async def disable_static_cache(request: Request, call_next) -> Response:
    """Evita que el navegador sirva versiones en caché antiguas de HTML/JS/CSS."""
    response: Response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


app.include_router(api_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def serve_index() -> FileResponse:
    """Entrega la aplicación web principal sin caché obsoleto."""
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
