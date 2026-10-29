from __future__ import annotations

"""Local eSCL bridge for DocuDesk Web and Canon MF440 Series.

The service binds to loopback only and exposes a fixed set of scanner operations.
It is intentionally not a general-purpose HTTP proxy.
"""

import base64
import http.client
import json
import os
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urljoin, urlsplit

HOST = "127.0.0.1"
PORT = 8765
PRINTER_HOST = "192.168.5.180"
PRINTER_PORT = 80
PRINTER_BASE = f"http://{PRINTER_HOST}:{PRINTER_PORT}"
ALLOWED_ORIGIN = "https://lomakobodiakov.github.io"
CAPABILITIES_PATH = "/eSCL/ScannerCapabilities"
SCAN_JOBS_PATH = "/eSCL/ScanJobs"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SCAN_IMAGE_BYTES = 12 * 1024 * 1024
MAX_SCAN_PAGES = 20
MAX_CONVERT_BYTES = 80 * 1024 * 1024
ALLOWED_SOURCES = {"Platen", "Feeder"}
ALLOWED_COLOR_MODES = {"RGB24", "Grayscale8", "BlackAndWhite1"}
ALLOWED_RESOLUTIONS = {150, 300, 600}
PAPER_SIZES = {
    "A4": (2480, 3508),
    "Letter": (2550, 3300),
    "Legal": (2550, 4200),
}


class ScannerError(Exception):
    pass


def printer_request(method: str, path: str, body: bytes | None = None,
                    headers: dict[str, str] | None = None, timeout: int = 90):
    conn = http.client.HTTPConnection(PRINTER_HOST, PRINTER_PORT, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        data = response.read(MAX_SCAN_IMAGE_BYTES + 1 if method == "GET" and path.endswith("/NextDocument") else MAX_RESPONSE_BYTES + 1)
        if len(data) > (MAX_SCAN_IMAGE_BYTES if method == "GET" and path.endswith("/NextDocument") else MAX_RESPONSE_BYTES):
            raise ScannerError("Ответ сканера превышает допустимый размер.")
        return response.status, dict(response.getheaders()), data
    finally:
        conn.close()


def safe_job_url(location: str) -> str:
    if not location:
        raise ScannerError("МФУ создало задание без заголовка Location.")
    candidate = urljoin(PRINTER_BASE + "/", location)
    parsed = urlsplit(candidate)
    if parsed.scheme != "http" or parsed.hostname != PRINTER_HOST or parsed.port not in (None, PRINTER_PORT):
        raise ScannerError("МФУ вернуло неожиданный адрес задания сканирования.")
    if not parsed.path.startswith("/eSCL/ScanJobs/"):
        raise ScannerError("МФУ вернуло неожиданный путь задания сканирования.")
    return candidate.rstrip("/")


def make_scan_settings(source: str, color_mode: str, resolution: int, paper_size: str) -> bytes:
    if source not in ALLOWED_SOURCES:
        raise ScannerError("Источник должен быть Platen (стекло) или Feeder (автоподатчик).")
    if color_mode not in ALLOWED_COLOR_MODES:
        raise ScannerError("Неподдерживаемый режим цвета.")
    if resolution not in ALLOWED_RESOLUTIONS:
        raise ScannerError("Разрешение должно быть 150, 300 или 600 dpi.")
    if paper_size not in PAPER_SIZES:
        raise ScannerError("Неподдерживаемый формат бумаги.")
    width, height = PAPER_SIZES[paper_size]
    xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"
 xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
 xmlns:escl="http://schemas.hp.com/imaging/escl/2011/05/03">
  <pwg:Version>2.0</pwg:Version>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>{width}</pwg:Width>
      <pwg:Height>{height}</pwg:Height>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>{source}</pwg:InputSource>
  <scan:ColorMode>{color_mode}</scan:ColorMode>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:XResolution>{resolution}</scan:XResolution>
  <scan:YResolution>{resolution}</scan:YResolution>
</scan:ScanSettings>'''
    return xml.encode("utf-8")


def scan_document(settings: bytes) -> list[dict]:
    status, headers, data = printer_request(
        "POST", SCAN_JOBS_PATH, settings,
        {"Content-Type": "text/xml; charset=utf-8", "Accept": "application/xml, text/xml"},
        timeout=20,
    )
    if status not in (200, 201):
        detail = data.decode("utf-8", errors="replace")[:500]
        raise ScannerError(f"МФУ отклонило задание (HTTP {status}). {detail}")
    location = next((v for k, v in headers.items() if k.lower() == "location"), "")
    job_url = safe_job_url(location)
    job_path = urlsplit(job_url).path
    pages = []
    try:
        for page_index in range(MAX_SCAN_PAGES):
            page_status = None
            page_headers: dict[str, str] = {}
            page_data = b""
            # Some scanners return 503 briefly while the next page is being prepared.
            for attempt in range(90):
                page_status, page_headers, page_data = printer_request(
                    "GET", job_path + "/NextDocument",
                    headers={"Accept": "image/jpeg, image/png, application/octet-stream"},
                    timeout=90,
                )
                if page_status == 503:
                    time.sleep(1)
                    continue
                break
            if page_status in (404, 410):
                if not pages:
                    raise ScannerError("Сканер завершил задание, но не вернул изображение. Проверьте бумагу и источник.")
                break
            if page_status != 200:
                detail = page_data.decode("utf-8", errors="replace")[:300]
                raise ScannerError(f"Не удалось получить страницу {page_index + 1} (HTTP {page_status}). {detail}")
            if not page_data:
                raise ScannerError(f"Сканер вернул пустую страницу №{page_index + 1}.")
            content_type = next((v.split(";")[0].strip().lower() for k, v in page_headers.items()
                                 if k.lower() == "content-type"), "image/jpeg")
            if content_type not in ("image/jpeg", "image/png"):
                raise ScannerError(f"Формат изображения {content_type} пока не поддерживается в PDF DocuDesk.")
            pages.append({
                "name": f"scan_{page_index + 1:03d}.jpg" if content_type == "image/jpeg" else f"scan_{page_index + 1:03d}.png",
                "content_type": content_type,
                "size": len(page_data),
                "data": base64.b64encode(page_data).decode("ascii"),
            })
            if len(pages) >= MAX_SCAN_PAGES:
                break
    finally:
        try:
            printer_request("DELETE", job_path, headers={"Accept": "*/*"}, timeout=5)
        except Exception:
            pass
    return pages


def list_wia_scanners() -> list[dict]:
    """Enumerate scanner devices exposed by Windows WIA; unavailable on non-Windows systems."""
    if os.name != "nt":
        return []
    try:
        import win32com.client
        manager = win32com.client.Dispatch("WIA.DeviceManager")
        devices = []
        for info in manager.DeviceInfos:
            try:
                if int(info.Type) != 1:
                    continue
                name = "Сканер"
                device_id = str(info.DeviceID)
                for prop in info.Properties:
                    if str(prop.Name).lower() == "name":
                        name = str(prop.Value)
                        break
                devices.append({"id": "wia:" + device_id, "name": name, "connection": "Windows WIA"})
            except Exception:
                continue
        return devices
    except Exception:
        return []


def scan_wia_one_page(device_id: str, color_mode: str, resolution: int) -> list[dict]:
    """Scan one page through a Windows WIA device and return a browser-ready image."""
    if os.name != "nt":
        raise ScannerError("USB/WIA-сканирование доступно только в Windows.")
    try:
        import win32com.client
        from PIL import Image
    except ImportError as exc:
        raise ScannerError("Для USB-сканирования нужны pywin32 и Pillow. Переустановите зависимости помощника.") from exc
    raw_id = device_id.removeprefix("wia:")
    manager = win32com.client.Dispatch("WIA.DeviceManager")
    selected = None
    for info in manager.DeviceInfos:
        if str(info.DeviceID) == raw_id:
            selected = info
            break
    if selected is None:
        raise ScannerError("Выбранный сканер Windows не найден. Нажмите «Обновить устройства».")
    try:
        item = selected.Connect().Items(1)
        for prop_id, value in ((6147, resolution), (6148, resolution), (6146, 1 if color_mode == "RGB24" else 2 if color_mode == "Grayscale8" else 4)):
            try:
                item.Properties(str(prop_id)).Value = value
            except Exception:
                pass
        transfer = item.Transfer()
        with tempfile.TemporaryDirectory(prefix="docudesk-wia-") as folder:
            raw_path = os.path.join(folder, "scan.bmp")
            transfer.SaveFile(raw_path)
            with Image.open(raw_path) as scanned:
                from io import BytesIO
                output = BytesIO()
                scanned.convert("RGB").save(output, format="JPEG", quality=94)
                data = output.getvalue()
        return [{"name": "scan_001.jpg", "content_type": "image/jpeg", "size": len(data),
                 "data": base64.b64encode(data).decode("ascii")}]
    except Exception as exc:
        raise ScannerError("Windows не смог выполнить сканирование этим устройством: " + str(exc)) from exc


class Handler(BaseHTTPRequestHandler):
    server_version = "DocuDeskLocalBridge/0.2"

    def log_message(self, fmt: str, *args: object) -> None:
        print("[DocuDesk Bridge] " + (fmt % args))

    def _headers(self, status: int, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        origin = self.headers.get("Origin", "")
        if origin == ALLOWED_ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ALLOWED_ORIGIN)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename, X-Convert-To")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'")
        self.end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status)
        self.wfile.write(body)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        return not origin or origin == ALLOWED_ORIGIN

    def do_OPTIONS(self) -> None:
        if self.headers.get("Origin", "") != ALLOWED_ORIGIN:
            self._json(403, {"ok": False, "error": "Origin not allowed"})
            return
        self._headers(204)

    def do_GET(self) -> None:
        if not self._origin_allowed():
            self._json(403, {"ok": False, "error": "Origin not allowed"})
            return
        path = urlsplit(self.path).path
        if path == "/health":
            self._json(200, {"ok": True, "service": "DocuDesk Local Bridge", "version": "0.2"})
            return
        if path == "/devices":
            devices = list_wia_scanners()
            devices.append({"id": "escl:canon-mf440", "name": "Canon MF440 Series (сеть eSCL)", "connection": "eSCL", "address": PRINTER_HOST})
            self._json(200, {"ok": True, "devices": devices})
            return
        if path == "/capabilities":
            try:
                status, _, data = printer_request(
                    "GET", CAPABILITIES_PATH,
                    headers={"Accept": "application/xml, text/xml"}, timeout=8,
                )
                if status != 200 or b"ScannerCapabilities" not in data:
                    self._json(502, {"ok": False, "error": f"Некорректный ответ сканера (HTTP {status})."})
                    return
                try:
                    root = ET.fromstring(data)
                    sources = sorted({el.text.strip() for el in root.iter()
                                      if el.tag.split("}")[-1] == "InputSource" and el.text and el.text.strip()})
                except ET.ParseError:
                    sources = []
                self._json(200, {
                    "ok": True, "printer": "Canon MF440 Series",
                    "address": PRINTER_BASE, "protocol": "eSCL",
                    "message": "Printer responded with ScannerCapabilities XML",
                    "sources": sources, "xml": data.decode("utf-8", errors="replace"),
                })
            except (OSError, TimeoutError, http.client.HTTPException, ScannerError) as exc:
                self._json(502, {"ok": False, "error": f"Не удалось получить возможности сканера: {exc}"})
            return
        self._json(404, {"ok": False, "error": "Not found"})

    def _read_upload(self) -> tuple[str, bytes]:
        length = int(self.headers.get("Content-Length", "0"))
        if length < 1 or length > MAX_CONVERT_BYTES:
            raise ScannerError("Размер файла должен быть не более 80 МБ.")
        filename = unquote(self.headers.get("X-Filename", "document.bin"))
        filename = os.path.basename(filename.replace("\\", "/"))
        if not filename or filename in (".", ".."):
            filename = "document.bin"
        return filename, self.rfile.read(length)

    def _find_office_binary(self) -> str | None:
        # First try PATH, then common Windows installation locations.
        office = shutil.which("soffice.com") or shutil.which("soffice.exe") or shutil.which("soffice") or shutil.which("libreoffice")
        if office:
            return office

        roots = [
            os.environ.get("PROGRAMFILES", r"C:\\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        candidates = []
        for root in roots:
            if not root:
                continue
            candidates.extend([
                os.path.join(root, "LibreOffice", "program", "soffice.com"),
                os.path.join(root, "LibreOffice", "program", "soffice.exe"),
                os.path.join(root, "Programs", "LibreOffice", "program", "soffice.com"),
                os.path.join(root, "Programs", "LibreOffice", "program", "soffice.exe"),
            ])
        for candidate in candidates:
            if os.path.isfile(candidate):
                return candidate
        return None

    def _run_office_conversion(self, filename: str, data: bytes, output_ext: str) -> tuple[bytes, str]:
        office = self._find_office_binary()
        if not office:
            raise ScannerError("Не удалось найти исполняемый файл LibreOffice (soffice.com/soffice.exe). Проверьте папку установки LibreOffice или переустановите программу.")
        with tempfile.TemporaryDirectory(prefix="docudesk-") as temp_dir:
            source = os.path.join(temp_dir, filename)
            out_dir = os.path.join(temp_dir, "out")
            os.makedirs(out_dir, exist_ok=True)
            with open(source, "wb") as stream:
                stream.write(data)
            command = [office, "--headless", "--convert-to", output_ext, "--outdir", out_dir, source]
            try:
                proc = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
            except subprocess.TimeoutExpired as exc:
                raise ScannerError("Конвертация превысила лимит времени (180 секунд).") from exc
            expected = os.path.join(out_dir, os.path.splitext(filename)[0] + "." + output_ext.split(":")[0])
            if proc.returncode != 0 or not os.path.isfile(expected):
                detail = (proc.stderr or proc.stdout or "LibreOffice не создал выходной файл.")[-1200:]
                raise ScannerError("LibreOffice не смог выполнить конвертацию. " + detail)
            with open(expected, "rb") as stream:
                result = stream.read(MAX_CONVERT_BYTES + 1)
            if len(result) > MAX_CONVERT_BYTES:
                raise ScannerError("Результат превышает допустимый размер 80 МБ.")
            return result, os.path.basename(expected)

    def do_POST(self) -> None:
        if not self._origin_allowed():
            self._json(403, {"ok": False, "error": "Origin not allowed"})
            return
        path = urlsplit(self.path).path
        if path in ("/convert", "/pdfa"):
            try:
                filename, data = self._read_upload()
                if path == "/pdfa":
                    if not filename.lower().endswith(".pdf"):
                        raise ScannerError("Для PDF/A выберите PDF-файл.")
                    # LibreOffice PDF export filter with PDF/A-1b selection.
                    result, out_name = self._run_office_conversion(filename, data, 'pdf:writer_pdf_Export:{"SelectPdfVersion":{"type":"long","value":1}}')
                else:
                    target = self.headers.get("X-Convert-To", "").lower().strip()
                    source_ext = os.path.splitext(filename)[1].lower()
                    allowed = {
                        "pdf": {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".odt", ".odp", ".ods"},
                        "docx": {".pdf"}, "pptx": {".pdf"}, "xlsx": {".pdf"},
                    }
                    if target not in allowed or source_ext not in allowed[target]:
                        raise ScannerError("Поддерживаются Word/PowerPoint/Excel в PDF и пробная конвертация PDF в DOCX/PPTX/XLSX.")
                    result, out_name = self._run_office_conversion(filename, data, target)
                self._headers(200, "application/pdf")
                self.wfile.write(result)
            except (ValueError, TypeError, ScannerError, OSError, subprocess.SubprocessError) as exc:
                self._json(400, {"ok": False, "error": str(exc) or "Ошибка конвертации."})
            return
        if path != "/scan":
            self._json(404, {"ok": False, "error": "Not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 16 * 1024:
                raise ScannerError("Некорректный размер запроса.")
            request_data = json.loads(self.rfile.read(length).decode("utf-8"))
            device_id = str(request_data.get("deviceId", "escl:canon-mf440"))
            source = request_data.get("source", "Platen")
            color_mode = request_data.get("colorMode", "RGB24")
            resolution = int(request_data.get("resolution", 300))
            paper_size = request_data.get("paperSize", "A4")
            if device_id.startswith("wia:"):
                if source == "Feeder":
                    raise ScannerError("Для устройств Windows WIA автоподатчик пока не поддерживается в этой версии. Выберите «Стекло» или устройство eSCL.")
                pages = scan_wia_one_page(device_id, color_mode, resolution)
                self._json(200, {"ok": True, "printer": device_id[4:], "pages": pages})
            elif device_id == "escl:canon-mf440":
                settings = make_scan_settings(source, color_mode, resolution, paper_size)
                self._json(200, {
                    "ok": True, "printer": "Canon MF440 Series",
                    "pages": scan_document(settings),
                })
            else:
                raise ScannerError("Неизвестное устройство сканирования. Обновите список устройств.")
        except (ValueError, TypeError, json.JSONDecodeError, ScannerError,
                OSError, TimeoutError, http.client.HTTPException) as exc:
            self._json(502, {"ok": False, "error": str(exc) or "Ошибка сканирования."})


if __name__ == "__main__":
    print("DocuDesk Local Bridge 0.2")
    print(f"Printer: {PRINTER_BASE}{CAPABILITIES_PATH}")
    print(f"Local endpoint: http://{HOST}:{PORT}")
    print("Keep this window open while using the scanner. Close it to stop the bridge.")
    try:
        ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nDocuDesk Local Bridge stopped.")
