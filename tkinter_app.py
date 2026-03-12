import json
from pathlib import Path
from datetime import datetime, timezone

from app.models.ocr_models import OCRDocumentCache, OCRPageCache
from app.services.session_manager import SessionManager


class OCRCacheService:
    def __init__(self, session_manager: SessionManager):
        self.session_manager = session_manager

    def _get_cache_file_path(self, session_id: str) -> Path:
        session = self.session_manager.get_session(session_id)
        return Path(session.session_dir) / "ocr_cache.json"

    def persist_session_cache_to_disk(self, session_id: str) -> None:
        session = self.session_manager.get_session(session_id)
        cache_path = self._get_cache_file_path(session_id)

        serializable = {
            "document_path": session.ocr_cache.document_path,
            "filename": session.ocr_cache.filename,
            "page_count": session.ocr_cache.page_count,
            "pages": {
                str(page): {
                    "page_number": page_cache.page_number,
                    "text": page_cache.text,
                    "cached_at": page_cache.cached_at.isoformat(),
                    "source_path": page_cache.source_path,
                }
                for page, page_cache in session.ocr_cache.pages.items()
            }
        }

        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(serializable, f, ensure_ascii=False, indent=2)

    def hydrate_session_cache_from_disk_if_needed(self, session_id: str) -> OCRDocumentCache:
        session = self.session_manager.get_session(session_id)

        if session.ocr_cache.pages:
            return session.ocr_cache

        cache_path = self._get_cache_file_path(session_id)
        if not cache_path.exists():
            return session.ocr_cache

        with cache_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        pages_data = {
            int(page): OCRPageCache(
                page_number=page_data["page_number"],
                text=page_data["text"],
                cached_at=datetime.fromisoformat(page_data["cached_at"]),
                source_path=page_data.get("source_path"),
            )
            for page, page_data in data.get("pages", {}).items()
        }

        session.ocr_cache = OCRDocumentCache(
            document_path=data["document_path"],
            filename=data["filename"],
            page_count=data.get("page_count"),
            pages=pages_data,
        )

        return session.ocr_cache

    def get_missing_pages(
        self,
        session_id: str,
        start_page: int,
        end_page: int,
    ) -> list[int]:
        cache = self.hydrate_session_cache_from_disk_if_needed(session_id)
        requested_pages = list(range(start_page, end_page + 1))
        return [page for page in requested_pages if page not in cache.pages]

    def add_page_result(
        self,
        session_id: str,
        page_number: int,
        text: str,
        source_path: str | None = None,
    ) -> OCRDocumentCache:
        session = self.session_manager.get_session(session_id)

        session.ocr_cache.pages[page_number] = OCRPageCache(
            page_number=page_number,
            text=text,
            cached_at=datetime.now(timezone.utc),
            source_path=source_path,
        )

        self.persist_session_cache_to_disk(session_id)
        return session.ocr_cache

    def build_text_for_range(
        self,
        session_id: str,
        start_page: int,
        end_page: int,
    ) -> str:
        cache = self.hydrate_session_cache_from_disk_if_needed(session_id)

        parts = []
        for page in range(start_page, end_page + 1):
            if page in cache.pages:
                parts.append(cache.pages[page].text)

        return "\n\n".join(parts)




class OCRService:
    def run_ocr_on_page(self, input_path: str, page_number: int) -> str:
        return f"[OCR] Texto simulado para la página {page_number} desde {input_path}"






from app.services.document_service import DocumentService
from app.services.ocr_cache_service import OCRCacheService


class OCRPipelineService:
    def __init__(
        self,
        document_service: DocumentService,
        ocr_cache_service: OCRCacheService,
        ocr_service,
    ):
        self.document_service = document_service
        self.ocr_cache_service = ocr_cache_service
        self.ocr_service = ocr_service

    def get_or_process_range(
        self,
        session_id: str,
        start_page: int,
        end_page: int,
    ) -> dict:
        missing_pages = self.ocr_cache_service.get_missing_pages(
            session_id=session_id,
            start_page=start_page,
            end_page=end_page,
        )

        processed_pages = []

        for page in missing_pages:
            recovered = self.document_service.recover_document(
                session_id=session_id,
                start_page=page,
                end_page=page,
            )

            text = self.ocr_service.run_ocr_on_page(
                input_path=recovered.recovered_file_path,
                page_number=page,
            )

            self.ocr_cache_service.add_page_result(
                session_id=session_id,
                page_number=page,
                text=text,
                source_path=recovered.recovered_file_path,
            )

            processed_pages.append(page)

        full_text = self.ocr_cache_service.build_text_for_range(
            session_id=session_id,
            start_page=start_page,
            end_page=end_page,
        )

        return {
            "session_id": session_id,
            "start_page": start_page,
            "end_page": end_page,
            "processed_pages": processed_pages,
            "cached_pages_used": [
                page for page in range(start_page, end_page + 1)
                if page not in processed_pages
            ],
            "text": full_text,
        }








from pathlib import Path
import webbrowser

from app.services.session_manager import SessionManager
from app.services.storage_service import StorageService
from app.services.document_service import DocumentService
from app.services.ocr_cache_service import OCRCacheService
from app.services.ocr_service import OCRService
from app.services.ocr_pipeline_service import OCRPipelineService


class FakeUploadFile:
    def __init__(self, filepath: str, content_type: str):
        self.path = Path(filepath)
        self.filename = self.path.name
        self.content_type = content_type
        self.file = open(self.path, "rb")

    def close(self):
        if self.file and not self.file.closed:
            self.file.close()


def detect_content_type(filepath: str) -> str:
    suffix = Path(filepath).suffix.lower()
    if suffix == ".pdf":
        return "application/pdf"
    if suffix == ".png":
        return "image/png"
    if suffix in [".jpg", ".jpeg"]:
        return "image/jpeg"
    raise ValueError(f"Tipo de archivo no soportado: {suffix}")


def show_file(path: str) -> None:
    file_path = Path(path).resolve()
    print(f"\n[INFO] Abriendo archivo: {file_path}")
    webbrowser.open(file_path.as_uri())


def main():
    sample_file = "sample_docs/ejemplo.pdf"

    if not Path(sample_file).exists():
        raise FileNotFoundError(
            f"No existe el archivo de prueba: {sample_file}\n"
            "Crea la carpeta sample_docs y coloca ahí un PDF o imagen."
        )

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

    upload = FakeUploadFile(
        filepath=sample_file,
        content_type=detect_content_type(sample_file),
    )

    try:
        print("\n=== 1. SUBIR DOCUMENTO ===")
        session = document_service.upload_document(upload)
        print(f"session_id  : {session.session_id}")
        print(f"file_path   : {session.file_path}")
        print(f"session_dir : {session.session_dir}")
        print(f"page_count  : {session.page_count}")

        print("\n=== 2. RECUPERAR ORIGINAL ===")
        recovered_original = document_service.recover_document(session.session_id)
        print(recovered_original.model_dump())
        if recovered_original.recovered_file_path.lower().endswith(".pdf"):
            show_file(recovered_original.recovered_file_path)

        if session.content_type == "application/pdf" and session.page_count and session.page_count >= 2:
            print("\n=== 3. RECUPERAR RANGO 1-2 ===")
            recovered_range = document_service.recover_document(
                session_id=session.session_id,
                start_page=1,
                end_page=2,
            )
            print(recovered_range.model_dump())
            show_file(recovered_range.recovered_file_path)

            print("\n=== 4. OCR DEL RANGO 1-2 ===")
            result_1 = ocr_pipeline.get_or_process_range(
                session_id=session.session_id,
                start_page=1,
                end_page=2,
            )
            print(result_1)

            print("\n=== 5. OCR DEL RANGO 2-3 (REUSA CACHE DE 2 SI EXISTE) ===")
            end_page = min(3, session.page_count)
            result_2 = ocr_pipeline.get_or_process_range(
                session_id=session.session_id,
                start_page=2,
                end_page=end_page,
            )
            print(result_2)

            print("\n=== 6. MOSTRAR CACHE EN MEMORIA ===")
            current_session = session_manager.get_session(session.session_id)
            print(current_session.ocr_cache.model_dump())

            print("\n=== 7. MOSTRAR OCR_CACHE.JSON ===")
            cache_file = Path(current_session.session_dir) / "ocr_cache.json"
            print(f"cache_file: {cache_file}")
            if cache_file.exists():
                print(cache_file.read_text(encoding='utf-8'))

        print("\n=== 8. ELIMINAR SESIÓN Y ARCHIVOS ===")
        document_service.delete_document_from_disk(session.session_id)
        print("Sesión y archivos eliminados.")

    finally:
        upload.close()


if __name__ == "__main__":
    main()

