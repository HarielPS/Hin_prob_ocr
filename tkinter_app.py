from pathlib import Path
import shutil
from tempfile import NamedTemporaryFile

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from app.services.session_manager import SessionManager
from app.services.storage_service import StorageService
from app.services.document_service import DocumentService
from app.services.ocr_cache_service import OCRCacheService
from app.services.ocr_service import OCRService
from app.services.ocr_pipeline_service import OCRPipelineService


app = FastAPI(title="OCR API", version="0.1.0")


# =========================
# INSTANCIAS GLOBALES
# =========================

session_manager = SessionManager(ttl_minutes=120)
storage_service = StorageService(temp_dir="app/temp")
document_service = DocumentService(storage_service, session_manager)
ocr_cache_service = OCRCacheService(session_manager)
ocr_service = OCRService()
ocr_pipeline = OCRPipelineService(
    document_service=document_service,
    ocr_cache_service=ocr_cache_service,
    ocr_service=ocr_service,
)


# =========================
# MODELOS
# =========================

class HealthResponse(BaseModel):
    status: str
    service: str


class OCRRegionResponse(BaseModel):
    ok: bool
    session_id: str
    filename: str
    content_type: str
    page: int
    coordinates: dict
    ocr_result: str
    processed_pages: list[int]
    cached_pages_used: list[int]


# =========================
# HELPERS
# =========================

def validate_coordinates(x1: int, y1: int, x2: int, y2: int) -> None:
    if x1 < 0 or y1 < 0 or x2 < 0 or y2 < 0:
        raise HTTPException(status_code=400, detail="Las coordenadas no pueden ser negativas")
    if x2 <= x1:
        raise HTTPException(status_code=400, detail="x2 debe ser mayor que x1")
    if y2 <= y1:
        raise HTTPException(status_code=400, detail="y2 debe ser mayor que y1")


def save_upload_to_temp_input(upload_file: UploadFile) -> str:
    suffix = Path(upload_file.filename).suffix or ".bin"

    with NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(upload_file.file, tmp)
        return tmp.name


# =========================
# ENDPOINTS
# =========================

@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", service="ocr-api")


@app.post("/ocr/region", response_model=OCRRegionResponse)
async def ocr_region(
    file: UploadFile = File(...),
    x1: int = Form(...),
    y1: int = Form(...),
    x2: int = Form(...),
    y2: int = Form(...),
    page: int = Form(1),
):
    """
    Recibe archivo + coordenadas desde Tkinter.
    Usa tus servicios actuales para:
    - guardar el documento
    - crear sesión
    - recuperar la página
    - correr OCR pipeline
    """

    if not file.filename:
        raise HTTPException(status_code=400, detail="El archivo debe tener nombre")

    validate_coordinates(x1, y1, x2, y2)

    temp_input_path = save_upload_to_temp_input(file)

    try:
        # 1. Guardar documento usando tu servicio existente
        session = document_service.save_document_from_path(temp_input_path)

        # Ajustar nombre original si quieres conservarlo en respuesta
        filename = file.filename
        content_type = session.content_type

        # 2. Aquí por ahora usamos OCR por página
        # Más adelante puedes conectar x1,y1,x2,y2 a tu recorte real
        result = ocr_pipeline.get_or_process_range(
            session_id=session.session_id,
            start_page=page,
            end_page=page,
        )

        return OCRRegionResponse(
            ok=True,
            session_id=session.session_id,
            filename=filename,
            content_type=content_type,
            page=page,
            coordinates={
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
            },
            ocr_result=result["text"],
            processed_pages=result["processed_pages"],
            cached_pages_used=result["cached_pages_used"],
        )

    finally:
        temp_path = Path(temp_input_path)
        if temp_path.exists():
            temp_path.unlink()





from pathlib import Path
import mimetypes
import requests


class OCRLLMClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000"):
        self.base_url = base_url.rstrip("/")

    def health(self) -> dict:
        response = requests.get(f"{self.base_url}/health", timeout=30)
        response.raise_for_status()
        return response.json()

    def ocr_region(
        self,
        file_path: str,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        page: int = 1,
    ) -> dict:
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"No existe el archivo: {file_path}")

        content_type, _ = mimetypes.guess_type(str(path))
        if content_type is None:
            content_type = "application/octet-stream"

        with path.open("rb") as f:
            files = {
                "file": (path.name, f, content_type),
            }
            data = {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "page": page,
            }

            response = requests.post(
                f"{self.base_url}/ocr/region",
                files=files,
                data=data,
                timeout=120,
            )
            response.raise_for_status()
            return response.json()


resp = self.api.ocr_region(
    file_path=self.current_file_path,
    x1=100,
    y1=120,
    x2=300,
    y2=180,
    page=1,
)

uvicorn app.main:app --reload


http://127.0.0.1:8000/docs
