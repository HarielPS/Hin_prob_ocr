Te lo dejo ya aterrizado a tu caso exacto: una API que maneja

crear sesión

eliminar sesión

subir documento

recuperar documento

borrar documento en disco


con una carpeta temp/ y una sesión ligera en memoria.

La idea es esta:

la sesión guarda metadatos

el documento vive en temp/{session_id}/

recover_document toma el original desde disco

si necesitas un rango de páginas, crea un chunk temporal

al eliminar la sesión, borras memoria y carpeta temporal



---

Estructura recomendada

app/
├── main.py
├── models/
│   └── session_models.py
├── services/
│   ├── session_manager.py
│   ├── storage_service.py
│   └── document_service.py
└── temp/


---

1) app/models/session_models.py

Aquí defines la estructura de la sesión y de recuperación.

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class SessionData(BaseModel):
    session_id: str
    filename: str
    content_type: str
    file_path: str
    session_dir: str
    created_at: datetime
    expires_at: datetime
    page_count: Optional[int] = None
    chunks: list[str] = Field(default_factory=list)


class RecoveredDocument(BaseModel):
    source_file_path: str
    recovered_file_path: str
    start_page: Optional[int] = None
    end_page: Optional[int] = None
    is_original: bool = False


---

2) app/services/session_manager.py

Esto solo maneja la sesión en memoria.

from datetime import datetime, timedelta, timezone
from uuid import uuid4
from typing import Dict
from fastapi import HTTPException

from app.models.session_models import SessionData


class SessionManager:
    def __init__(self, ttl_minutes: int = 120):
        self.ttl_minutes = ttl_minutes
        self.sessions: Dict[str, SessionData] = {}

    def create_session(
        self,
        filename: str,
        content_type: str,
        file_path: str,
        session_dir: str,
        page_count: int | None = None,
    ) -> SessionData:
        session_id = str(uuid4())
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=self.ttl_minutes)

        session = SessionData(
            session_id=session_id,
            filename=filename,
            content_type=content_type,
            file_path=file_path,
            session_dir=session_dir,
            created_at=now,
            expires_at=expires_at,
            page_count=page_count,
        )

        self.sessions[session_id] = session
        return session

    def create_session_with_id(
        self,
        session_id: str,
        filename: str,
        content_type: str,
        file_path: str,
        session_dir: str,
        page_count: int | None = None,
    ) -> SessionData:
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(minutes=self.ttl_minutes)

        session = SessionData(
            session_id=session_id,
            filename=filename,
            content_type=content_type,
            file_path=file_path,
            session_dir=session_dir,
            created_at=now,
            expires_at=expires_at,
            page_count=page_count,
        )

        self.sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> SessionData:
        session = self.sessions.get(session_id)

        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        if datetime.now(timezone.utc) > session.expires_at:
            self.sessions.pop(session_id, None)
            raise HTTPException(status_code=410, detail="Session expired")

        return session

    def delete_session(self, session_id: str) -> None:
        self.sessions.pop(session_id, None)


---

3) app/services/storage_service.py

Este servicio maneja el disco.

import shutil
from pathlib import Path
from uuid import uuid4
from fastapi import UploadFile, HTTPException


class StorageService:
    def __init__(self, temp_dir: str = "app/temp"):
        self.temp_dir = Path(temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def create_session_dir(self, session_id: str) -> Path:
        session_dir = self.temp_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def save_uploaded_file(self, session_id: str, upload_file: UploadFile) -> tuple[str, str]:
        session_dir = self.create_session_dir(session_id)
        file_path = session_dir / upload_file.filename

        with file_path.open("wb") as buffer:
            shutil.copyfileobj(upload_file.file, buffer)

        return str(file_path), str(session_dir)

    def delete_session_dir(self, session_id: str) -> None:
        session_dir = self.temp_dir / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)

    def ensure_chunks_dir(self, session_id: str) -> Path:
        chunks_dir = self.temp_dir / session_id / "chunks"
        chunks_dir.mkdir(parents=True, exist_ok=True)
        return chunks_dir

    def build_chunk_path(self, session_id: str, start_page: int, end_page: int) -> Path:
        chunks_dir = self.ensure_chunks_dir(session_id)
        return chunks_dir / f"pages_{start_page}_{end_page}.pdf"

    def delete_file(self, file_path: str) -> None:
        path = Path(file_path)
        if path.exists() and path.is_file():
            path.unlink()

    def validate_file_exists(self, file_path: str) -> Path:
        path = Path(file_path)
        if not path.exists():
            raise HTTPException(status_code=404, detail="Document file not found on disk")
        return path


---

4) app/services/document_service.py

Aquí está la lógica de documento: subir, recuperar original o rango, y borrar.

Voy a usar pypdf para extraer páginas. Si no la tienes, sería:

pip install pypdf

from pathlib import Path
from fastapi import UploadFile, HTTPException
from pypdf import PdfReader, PdfWriter

from app.models.session_models import RecoveredDocument
from app.services.storage_service import StorageService
from app.services.session_manager import SessionManager


class DocumentService:
    def __init__(self, storage_service: StorageService, session_manager: SessionManager):
        self.storage_service = storage_service
        self.session_manager = session_manager

    def upload_document(self, upload_file: UploadFile):
        if not upload_file.filename:
            raise HTTPException(status_code=400, detail="Filename is required")

        allowed_types = {
            "application/pdf",
            "image/png",
            "image/jpeg",
            "image/jpg",
        }

        if upload_file.content_type not in allowed_types:
            raise HTTPException(status_code=400, detail="Unsupported file type")

        from uuid import uuid4
        session_id = str(uuid4())

        file_path, session_dir = self.storage_service.save_uploaded_file(session_id, upload_file)

        page_count = None
        if upload_file.content_type == "application/pdf":
            page_count = self._count_pdf_pages(file_path)
        elif upload_file.content_type.startswith("image/"):
            page_count = 1

        session = self.session_manager.create_session_with_id(
            session_id=session_id,
            filename=upload_file.filename,
            content_type=upload_file.content_type,
            file_path=file_path,
            session_dir=session_dir,
            page_count=page_count,
        )

        return session

    def recover_document(
        self,
        session_id: str,
        start_page: int | None = None,
        end_page: int | None = None,
    ) -> RecoveredDocument:
        session = self.session_manager.get_session(session_id)
        original_path = self.storage_service.validate_file_exists(session.file_path)

        # Si es imagen, solo regresa el original
        if session.content_type.startswith("image/"):
            return RecoveredDocument(
                source_file_path=str(original_path),
                recovered_file_path=str(original_path),
                is_original=True,
            )

        # Si es PDF y no mandan rango, regresa el original
        if start_page is None and end_page is None:
            return RecoveredDocument(
                source_file_path=str(original_path),
                recovered_file_path=str(original_path),
                is_original=True,
            )

        if start_page is None or end_page is None:
            raise HTTPException(status_code=400, detail="start_page and end_page must both be provided")

        if start_page < 1 or end_page < 1:
            raise HTTPException(status_code=400, detail="Pages must be >= 1")

        if start_page > end_page:
            raise HTTPException(status_code=400, detail="start_page cannot be greater than end_page")

        if session.page_count is not None and end_page > session.page_count:
            raise HTTPException(status_code=400, detail="Requested range exceeds document page count")

        chunk_path = self.storage_service.build_chunk_path(session_id, start_page, end_page)

        # Si ya existe el chunk, lo reutilizamos
        if chunk_path.exists():
            return RecoveredDocument(
                source_file_path=str(original_path),
                recovered_file_path=str(chunk_path),
                start_page=start_page,
                end_page=end_page,
                is_original=False,
            )

        self._extract_pdf_range(
            source_pdf_path=str(original_path),
            output_pdf_path=str(chunk_path),
            start_page=start_page,
            end_page=end_page,
        )

        session.chunks.append(str(chunk_path))

        return RecoveredDocument(
            source_file_path=str(original_path),
            recovered_file_path=str(chunk_path),
            start_page=start_page,
            end_page=end_page,
            is_original=False,
        )

    def delete_document_from_disk(self, session_id: str) -> None:
        session = self.session_manager.get_session(session_id)
        self.storage_service.delete_session_dir(session_id)
        self.session_manager.delete_session(session_id)

    def _count_pdf_pages(self, file_path: str) -> int:
        reader = PdfReader(file_path)
        return len(reader.pages)

    def _extract_pdf_range(
        self,
        source_pdf_path: str,
        output_pdf_path: str,
        start_page: int,
        end_page: int,
    ) -> None:
        reader = PdfReader(source_pdf_path)
        writer = PdfWriter()

        # pypdf usa índice base 0
        for page_index in range(start_page - 1, end_page):
            writer.add_page(reader.pages[page_index])

        with open(output_pdf_path, "wb") as output_file:
            writer.write(output_file)


---

5) Cómo se usan estas funciones

Crear sesión + subir documento

En realidad en tu flujo se crean juntas cuando subes el archivo.

session = document_service.upload_document(upload_file)

Eso hace:

1. genera session_id


2. crea carpeta temp/{session_id}


3. guarda archivo


4. crea sesión en memoria


5. devuelve SessionData




---

Recuperar documento completo

recovered = document_service.recover_document(session_id)

Devuelve algo así:

RecoveredDocument(
    source_file_path="app/temp/abc123/estado.pdf",
    recovered_file_path="app/temp/abc123/estado.pdf",
    is_original=True
)


---

Recuperar rango de páginas

recovered = document_service.recover_document(session_id, start_page=2, end_page=4)

Devuelve algo así:

RecoveredDocument(
    source_file_path="app/temp/abc123/estado.pdf",
    recovered_file_path="app/temp/abc123/chunks/pages_2_4.pdf",
    start_page=2,
    end_page=4,
    is_original=False
)


---

Eliminar sesión y borrar disco

document_service.delete_document_from_disk(session_id)

Eso hace:

1. busca la sesión


2. borra temp/{session_id}


3. elimina la sesión en memoria




---

6) Cómo se vería tu API

Si quieres exponerlo en FastAPI, sería algo así.

from fastapi import APIRouter, UploadFile, File
from app.services.storage_service import StorageService
from app.services.session_manager import SessionManager
from app.services.document_service import DocumentService

router = APIRouter(prefix="/documents", tags=["documents"])

storage_service = StorageService(temp_dir="app/temp")
session_manager = SessionManager(ttl_minutes=120)
document_service = DocumentService(storage_service, session_manager)


@router.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    session = document_service.upload_document(file)

    return {
        "session_id": session.session_id,
        "filename": session.filename,
        "content_type": session.content_type,
        "file_path": session.file_path,
        "page_count": session.page_count,
        "created_at": session.created_at,
        "expires_at": session.expires_at,
    }


@router.get("/{session_id}/recover")
async def recover_original_document(session_id: str):
    recovered = document_service.recover_document(session_id)

    return recovered.model_dump()


@router.get("/{session_id}/recover-range")
async def recover_document_range(session_id: str, start_page: int, end_page: int):
    recovered = document_service.recover_document(
        session_id=session_id,
        start_page=start_page,
        end_page=end_page,
    )

    return recovered.model_dump()


@router.delete("/{session_id}")
async def delete_document(session_id: str):
    document_service.delete_document_from_disk(session_id)
    return {"message": "Session and temp files deleted"}


---

7) Cómo conecta esto con tu OCR

Tu OCR ya no necesita “guardar binario en sesión”.

Hace esto:

recovered = document_service.recover_document(session_id, start_page=1, end_page=2)
input_path = recovered.recovered_file_path

# aquí llamas tu OCR
text = ocr_service.run_ocr(input_path)

Entonces:

si quieres el original, OCR usa el original

si quieres un rango, OCR usa el chunk temporal

nunca modificas el PDF original



---

8) Qué recomiendo finalmente para tu caso

Yo dejaría así las responsabilidades:

SessionManager

Maneja:

crear sesión

obtener sesión

eliminar sesión


StorageService

Maneja:

crear carpetas

guardar archivo

borrar carpeta de sesión

crear rutas de chunks


DocumentService

Coordina:

subir documento

recuperar documento completo

recuperar rango

borrar documento en disco + sesión



---

9) Punto importante de diseño

Aunque preguntaste por “crear sesión” y “subir documento” por separado, en tu caso práctico casi siempre van unidos.

Porque una sesión vacía sin archivo no aporta mucho.

Entonces tu flujo real más natural es:

upload_document() → crea sesión + guarda archivo

recover_document() → obtiene original o rango

delete_document_from_disk() → borra carpeta + sesión


Si quisieras, sí puedo darte la versión final completa de estos 3 archivos ya corregidos y pulidos, lista para copiar al proyecto, con nombres más limpios y preparada para luego meter OCR cache y render cache.
