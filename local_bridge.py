from __future__ import annotations

"""Local scanner bridge for DocuDesk Web.

Discovers Windows WIA/eSCL scanners and accepts paired phone photos over LAN.
"""

import base64
import http.client
import ipaddress
import json
import os
import shutil
import socket
import secrets
import threading
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urljoin, urlsplit, quote

HOST = "127.0.0.1"
PORT = 8765
ALLOWED_ORIGIN = "https://lomakobodiakov.github.io"
PREVIEW_ORIGIN = "https://raw.githack.com"
ALLOWED_ORIGINS = {ALLOWED_ORIGIN, PREVIEW_ORIGIN}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_SCAN_IMAGE_BYTES = 12 * 1024 * 1024
MAX_SCAN_PAGES = 20
MAX_CONVERT_BYTES = 80 * 1024 * 1024
ALLOWED_SOURCES = {"Platen", "Feeder"}
ALLOWED_COLOR_MODES = {"RGB24", "Grayscale8", "BlackAndWhite1"}
ALLOWED_RESOLUTIONS = {75, 100, 150, 200, 300, 600, 1200}
ESCL_DEVICES: dict[str, dict] = {}
MOBILE_SESSIONS: dict[str, list[dict]] = {}
MOBILE_SESSION_LOCK = threading.Lock()
MAX_MOBILE_IMAGE_BYTES = 12 * 1024 * 1024
PAPER_SIZES = {
    "A4": (2480, 3508),
    "Letter": (2550, 3300),
    "Legal": (2550, 4200),
}


class ScannerError(Exception):
    pass


def escl_request(base_url: str, method: str, path: str, body: bytes | None = None,
                 headers: dict[str, str] | None = None, timeout: int = 90):
    if path.startswith("/"):
        origin = urlsplit(base_url)
        parsed = urlsplit(f"{origin.scheme}://{origin.netloc}{path}")
    else:
        parsed = urlsplit(urljoin(base_url.rstrip("/") + "/", path))
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ScannerError("Некорректный адрес сетевого сканера.")
    cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    conn = cls(parsed.hostname, parsed.port, timeout=timeout)
    try:
        target = parsed.path + (("?" + parsed.query) if parsed.query else "")
        conn.request(method, target, body=body, headers=headers or {})
        response = conn.getresponse()
        limit = MAX_SCAN_IMAGE_BYTES if method == "GET" and path.endswith("/NextDocument") else MAX_RESPONSE_BYTES
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ScannerError("Ответ сканера превышает допустимый размер.")
        return response.status, dict(response.getheaders()), data
    finally:
        conn.close()


def discover_escl_scanners() -> list[dict]:
    found = []
    try:
        from zeroconf import Zeroconf, ServiceBrowser, ServiceListener
    except ImportError:
        return found
    class Listener(ServiceListener):
        def __init__(self): self.items = []
        def add_service(self, zc, service_type, name):
            info = zc.get_service_info(service_type, name, timeout=1500)
            if not info: return
            props = {k.decode("utf-8", "ignore").lower(): v.decode("utf-8", "ignore")
                     for k, v in (info.properties or {}).items()}
            addresses = info.parsed_addresses()
            if not addresses: return
            addr = next((v for v in addresses if ":" not in v), addresses[0])
            resource = props.get("rs", "/eSCL/").strip()
            if not resource.startswith("/"): resource = "/" + resource
            scheme = "https" if props.get("tls", "").lower() in ("1", "true", "yes") else "http"
            base = f"{scheme}://{addr}:{info.port}{resource.rstrip('/')}/"
            label = props.get("ty") or name.split(".")[0].replace("-", " ")
            self.items.append((name, base, label))
        def update_service(self, zc, service_type, name): self.add_service(zc, service_type, name)
        def remove_service(self, zc, service_type, name): pass
    listener = Listener()
    zc = None
    try:
        # mDNS discovery is optional: a disconnected/unrouted network adapter
        # must never prevent Windows WIA USB scanners from being listed.
        zc = Zeroconf()
        browser = ServiceBrowser(zc, ["_uscan._tcp.local.", "_uscans._tcp.local."], listener)
        time.sleep(2)
    except Exception as exc:
        print("[DocuDesk Bridge] Network scanner discovery skipped:", exc)
    finally:
        if zc is not None:
            try:
                zc.close()
            except Exception:
                pass
    for name, base, label in listener.items:
        device_id = "escl:" + quote(name, safe="")
        ESCL_DEVICES[device_id] = {"base": base, "name": label}
        found.append({"id": device_id, "name": label, "connection": "eSCL/AirScan", "address": base})
    return found


def escl_capabilities(device: dict):
    status, _, data = escl_request(device["base"], "GET", "ScannerCapabilities",
        headers={"Accept": "application/xml, text/xml"}, timeout=8)
    if status != 200 or b"ScannerCapabilities" not in data:
        raise ScannerError(f"Сканер не вернул возможности (HTTP {status}).")
    root = ET.fromstring(data)
    sources = sorted({el.text.strip() for el in root.iter()
        if el.tag.split("}")[-1] == "InputSource" and el.text and el.text.strip()})
    return data, sources


def escl_job_path(base: str, location: str) -> str:
    if not location: raise ScannerError("Сканер не вернул адрес задания.")
    url = urljoin(base, location)
    parsed, origin = urlsplit(url), urlsplit(base)
    if parsed.scheme != origin.scheme or parsed.hostname != origin.hostname or parsed.port != origin.port:
        raise ScannerError("Сканер вернул неожиданный адрес задания.")
    if "/ScanJobs/" not in parsed.path: raise ScannerError("Неожиданный путь задания сканирования.")
    return parsed.path.rstrip("/")


def scan_escl(device: dict, source: str, color_mode: str, resolution: int, paper_size: str) -> list[dict]:
    if source not in ALLOWED_SOURCES or color_mode not in ALLOWED_COLOR_MODES or resolution not in ALLOWED_RESOLUTIONS:
        raise ScannerError("Выбраны неподдерживаемые параметры сканирования.")
    if paper_size not in PAPER_SIZES: raise ScannerError("Неподдерживаемый формат бумаги.")
    capabilities, sources = escl_capabilities(device)
    if sources and source not in sources:
        raise ScannerError("Выбранный источник не поддерживается этим сканером.")
    width, height = PAPER_SIZES[paper_size]
    settings = f'''<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm" xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03">
<pwg:Version>2.0</pwg:Version><pwg:ScanRegions><pwg:ScanRegion><pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
<pwg:XOffset>0</pwg:XOffset><pwg:YOffset>0</pwg:YOffset><pwg:Width>{width}</pwg:Width><pwg:Height>{height}</pwg:Height></pwg:ScanRegion></pwg:ScanRegions>
<pwg:InputSource>{source}</pwg:InputSource><scan:ColorMode>{color_mode}</scan:ColorMode><pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
<scan:XResolution>{resolution}</scan:XResolution><scan:YResolution>{resolution}</scan:YResolution></scan:ScanSettings>'''.encode("utf-8")
    status, headers, data = escl_request(device["base"], "POST", "ScanJobs", settings,
        {"Content-Type": "text/xml; charset=utf-8", "Accept": "application/xml, text/xml"}, timeout=20)
    if status not in (200, 201): raise ScannerError(f"Сканер отклонил задание (HTTP {status}). " + data.decode("utf-8", "replace")[:400])
    job = escl_job_path(device["base"], next((v for k,v in headers.items() if k.lower()=="location"), ""))
    pages = []
    try:
        for i in range(MAX_SCAN_PAGES):
            for _ in range(90):
                st, hdr, raw = escl_request(device["base"], "GET", job + "/NextDocument",
                    headers={"Accept":"image/jpeg, image/png"}, timeout=90)
                if st != 503: break
                time.sleep(1)
            if st in (404, 410):
                if not pages: raise ScannerError("Сканер не вернул изображение.")
                break
            if st != 200: raise ScannerError(f"Ошибка получения страницы {i+1}: HTTP {st}")
            mime = next((v.split(";")[0].lower() for k,v in hdr.items() if k.lower()=="content-type"),"image/jpeg")
            if mime not in ("image/jpeg","image/png"): raise ScannerError("Формат страницы не поддерживается.")
            ext = "jpg" if mime=="image/jpeg" else "png"
            pages.append({"name":f"scan_{i+1:03d}.{ext}","content_type":mime,"size":len(raw),
                          "data":base64.b64encode(raw).decode("ascii")})
    finally:
        try: escl_request(device["base"], "DELETE", job, timeout=5)
        except Exception: pass
    return pages


def list_wia_scanners() -> tuple[list[dict], str | None]:
    """Enumerate Windows WIA scanners on this worker thread."""
    if os.name != "nt":
        return [], "USB-сканирование через WIA доступно только в Windows."
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return [], "Не установлен pywin32: закройте помощник и запустите start_local_bridge.bat — он установит компонент автоматически."

    # HTTP requests run on separate threads; each COM-using thread must initialize COM.
    pythoncom.CoInitialize()
    try:
        try:
            manager = win32com.client.Dispatch("WIA.DeviceManager")
        except Exception:
            manager = win32com.client.gencache.EnsureDispatch("WIA.DeviceManager")
        devices = []
        item_errors = []
        for info in manager.DeviceInfos:
            try:
                if int(info.Type) != 1:
                    continue
                name = "Сканер Windows"
                device_id = str(info.DeviceID)
                for prop in info.Properties:
                    try:
                        if str(prop.Name).lower() == "name":
                            name = str(prop.Value)
                            break
                    except Exception:
                        continue
                devices.append({"id": "wia:" + device_id, "name": name, "connection": "Windows WIA"})
            except Exception as exc:
                item_errors.append(str(exc))
        if devices:
            return devices, None
        if item_errors:
            return [], "Windows WIA запустился, но не смог прочитать устройства: " + "; ".join(item_errors[:2])
        return [], "Windows WIA не обнаружил сканеры. Проверьте подключение и установку драйвера с поддержкой WIA."
    except Exception as exc:
        return [], "Не удалось обратиться к Windows WIA: " + str(exc)
    finally:
        pythoncom.CoUninitialize()


def scan_wia_one_page(device_id: str, color_mode: str, resolution: int) -> list[dict]:
    """Scan one page through a Windows WIA device on this worker thread."""
    if os.name != "nt":
        raise ScannerError("USB/WIA-сканирование доступно только в Windows.")
    try:
        import pythoncom
        import win32com.client
        from PIL import Image
    except ImportError as exc:
        raise ScannerError("Для USB-сканирования нужны pywin32 и Pillow. Переустановите зависимости помощника.") from exc

    pythoncom.CoInitialize()
    try:
        raw_id = device_id.removeprefix("wia:")
        try:
            manager = win32com.client.Dispatch("WIA.DeviceManager")
        except Exception:
            manager = win32com.client.gencache.EnsureDispatch("WIA.DeviceManager")
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
        except ScannerError:
            raise
        except Exception as exc:
            raise ScannerError("Windows не смог выполнить сканирование этим устройством: " + str(exc)) from exc
    finally:
        pythoncom.CoUninitialize()


def local_lan_ip() -> str:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("192.0.2.1", 9))
        return sock.getsockname()[0]
    except OSError:
        return ""
    finally:
        sock.close()


MOBILE_PAGE = """<!doctype html>
<html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DocuDesk — сканирование с телефона</title>
<style>
body{margin:0;padding:24px;background:#f4f6fa;color:#202938;font:16px/1.5 system-ui,sans-serif}.card{max-width:520px;margin:5vh auto;background:white;padding:24px;border-radius:20px}h1{font-size:24px}.pick{display:block;margin:24px 0;padding:20px;text-align:center;background:#d92d4a;color:white;border-radius:14px;font-weight:700;font-size:18px}input{position:absolute;width:1px;height:1px;opacity:0}#status{margin-top:18px;padding:12px;background:#f2f4f7;border-radius:10px}
</style><div class="card"><h1>Сканирование с телефона</h1><p>Сделайте снимок документа. Можно добавить несколько страниц — они появятся на компьютере автоматически.</p>
<label class="pick" for="camera">📷 Сфотографировать документ</label><input id="camera" type="file" accept="image/*" capture="environment" multiple>
<div id="status">Подключено. Нажмите кнопку и сфотографируйте страницу.</div></div>
<script>
const token=new URLSearchParams(location.search).get('token'),input=document.getElementById('camera'),status=document.getElementById('status');
if(!token){status.textContent='Нет кода подключения. Отсканируйте QR-код заново.';input.disabled=true}
input.addEventListener('change',async()=>{const chosen=[...input.files];let done=0;for(const file of chosen){if(file.size>25*1024*1024){status.textContent='Исходное фото больше 25 МБ.';continue}status.textContent='Подготавливаем фото '+(done+1)+' из '+chosen.length+'…';try{let upload=file;if(!['image/jpeg','image/png'].includes(file.type)){if(!window.createImageBitmap)throw new Error('Этот формат фото не поддерживается. Выберите JPG или PNG.');const bitmap=await createImageBitmap(file),scale=Math.min(1,2600/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement('canvas');canvas.width=Math.round(bitmap.width*scale);canvas.height=Math.round(bitmap.height*scale);canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);upload=await new Promise((resolve,reject)=>canvas.toBlob(blob=>blob?resolve(blob):reject(new Error('Не удалось обработать фото')),'image/jpeg',.9));bitmap.close()}if(upload.size>12*1024*1024)throw new Error('Фото после обработки больше 12 МБ. Сделайте снимок меньшего размера.');status.textContent='Отправляем страницу '+(done+1)+' из '+chosen.length+'…';const response=await fetch('/mobile/upload?token='+encodeURIComponent(token),{method:'POST',headers:{'Content-Type':upload.type},body:upload}),result=await response.json();if(!response.ok||!result.ok)throw new Error(result.error||'Ошибка');done++}catch(e){status.textContent='Ошибка отправки: '+e.message;break}}if(done)status.textContent='Отправлено страниц: '+done+'. Можно снимать следующие.';input.value=''});
</script></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "DocuDeskLocalBridge/0.2"

    def log_message(self, fmt: str, *args: object) -> None:
        print("[DocuDesk Bridge] " + (fmt % args))

    def _headers(self, status: int, content_type: str = "application/json; charset=utf-8") -> None:
        self.send_response(status)
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename, X-Convert-To")
            self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        csp = "default-src 'none'"
        if urlsplit(self.path).path == "/mobile":
            csp = "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'"
        self.send_header("Content-Security-Policy", csp)
        self.end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers(status)
        self.wfile.write(body)

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin", "")
        if not origin or origin in ALLOWED_ORIGINS:
            return True
        host = self.headers.get("Host", "")
        return origin == "http://" + host and urlsplit(self.path).path.startswith("/mobile/")

    def do_OPTIONS(self) -> None:
        if self.headers.get("Origin", "") not in ALLOWED_ORIGINS:
            self._json(403, {"ok": False, "error": "Origin not allowed"})
            return
        self._headers(204)

    def do_GET(self) -> None:
        if not self._origin_allowed():
            self._json(403, {"ok": False, "error": "Origin not allowed"})
            return
        path = urlsplit(self.path).path
        query = urlsplit(self.path).query
        try:
            remote_is_loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            remote_is_loopback = False
        if not remote_is_loopback and path != "/mobile":
            self._json(403, {"ok": False, "error": "LAN access is limited to the paired phone upload page."})
            return
        if path == "/mobile":
            from urllib.parse import parse_qs
            token = parse_qs(query).get("token", [""])[0]
            with MOBILE_SESSION_LOCK:
                valid = token in MOBILE_SESSIONS
            if not valid:
                self._headers(403, "text/plain; charset=utf-8")
                self.wfile.write("Сеанс недействителен. Создайте новый QR-код.".encode("utf-8"))
                return
            self._headers(200, "text/html; charset=utf-8")
            self.wfile.write(MOBILE_PAGE.encode("utf-8"))
            return
        if path == "/mobile/poll":
            from urllib.parse import parse_qs
            token = parse_qs(query).get("token", [""])[0]
            with MOBILE_SESSION_LOCK:
                if token not in MOBILE_SESSIONS:
                    self._json(403, {"ok": False, "error": "Сеанс телефона завершён."})
                    return
                pages = MOBILE_SESSIONS[token][:]
                MOBILE_SESSIONS[token].clear()
            self._json(200, {"ok": True, "pages": pages})
            return
        if path == "/health":
            self._json(200, {"ok": True, "service": "DocuDesk Local Bridge", "version": "0.2"})
            return
        if path == "/devices":
            devices, scanner_warning = list_wia_scanners()
            network_devices = discover_escl_scanners()
            devices.extend(network_devices)
            self._json(200, {"ok": True, "devices": devices,
                             "scannerWarning": scanner_warning,
                             "networkWarning": None if network_devices else "Сетевые eSCL/AirScan устройства не обнаружены через Bonjour/mDNS."})
            return
        if path == "/capabilities":
            device_id = unquote(urlsplit(self.path).query.removeprefix("device="))
            device = ESCL_DEVICES.get(device_id)
            if not device:
                self._json(404, {"ok": False, "error": "Сначала обновите список сетевых сканеров."})
                return
            try:
                data, sources = escl_capabilities(device)
                self._json(200, {"ok": True, "name": device["name"], "protocol": "eSCL",
                                 "sources": sources, "xml": data.decode("utf-8", errors="replace")})
            except (OSError, TimeoutError, http.client.HTTPException, ScannerError, ET.ParseError) as exc:
                self._json(502, {"ok": False, "error": str(exc)})
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
        query = urlsplit(self.path).query
        try:
            remote_is_loopback = ipaddress.ip_address(self.client_address[0]).is_loopback
        except ValueError:
            remote_is_loopback = False
        if not remote_is_loopback and path != "/mobile/upload":
            self._json(403, {"ok": False, "error": "LAN access is limited to paired phone image uploads."})
            return
        if path == "/mobile/start":
            token = secrets.token_urlsafe(24)
            with MOBILE_SESSION_LOCK:
                MOBILE_SESSIONS[token] = []
            ip = local_lan_ip()
            if not ip:
                with MOBILE_SESSION_LOCK:
                    MOBILE_SESSIONS.pop(token, None)
                self._json(503, {"ok": False, "error": "Не удалось определить IP компьютера в локальной сети."})
                return
            self._json(200, {"ok": True, "token": token, "url": f"http://{ip}:{PORT}/mobile?token={token}", "ip": ip})
            return
        if path == "/mobile/upload":
            from urllib.parse import parse_qs
            token = parse_qs(query).get("token", [""])[0]
            with MOBILE_SESSION_LOCK:
                valid = token in MOBILE_SESSIONS
            if not valid:
                self._json(403, {"ok": False, "error": "Сеанс истёк. Создайте новый QR-код."})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                mime = self.headers.get("Content-Type", "").split(";")[0].lower()
                if mime not in ("image/jpeg", "image/png", "image/webp"):
                    raise ScannerError("Разрешены только JPG, PNG и WebP.")
                if length < 1 or length > MAX_MOBILE_IMAGE_BYTES:
                    raise ScannerError("Размер фотографии должен быть не более 12 МБ.")
                raw = self.rfile.read(length)
                if not raw.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n", b"RIFF")):
                    raise ScannerError("Файл не распознан как изображение.")
                item = {"name": "phone_scan", "content_type": mime, "size": len(raw), "data": base64.b64encode(raw).decode("ascii")}
                with MOBILE_SESSION_LOCK:
                    if token not in MOBILE_SESSIONS:
                        raise ScannerError("Сеанс завершён.")
                    if len(MOBILE_SESSIONS[token]) >= 20:
                        raise ScannerError("В одном сеансе можно передать не более 20 страниц.")
                    MOBILE_SESSIONS[token].append(item)
                self._json(200, {"ok": True})
            except (ValueError, ScannerError) as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            return
        if path == "/mobile/stop":
            from urllib.parse import parse_qs
            token = parse_qs(query).get("token", [""])[0]
            with MOBILE_SESSION_LOCK:
                MOBILE_SESSIONS.pop(token, None)
            self._json(200, {"ok": True})
            return
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
            device_id = str(request_data.get("deviceId", ""))
            if not device_id:
                raise ScannerError("Сначала выберите сканер и обновите список устройств.")
            source = request_data.get("source", "Platen")
            color_mode = request_data.get("colorMode", "RGB24")
            resolution = int(request_data.get("resolution", 300))
            paper_size = request_data.get("paperSize", "A4")
            if device_id.startswith("wia:"):
                if source == "Feeder":
                    raise ScannerError("Автоподатчик WIA пока не поддерживается универсально для всех драйверов. Выберите стекло или сетевой eSCL-сканер.")
                pages = scan_wia_one_page(device_id, color_mode, resolution)
                self._json(200, {"ok": True, "device": device_id, "pages": pages})
            elif device_id.startswith("escl:"):
                device = ESCL_DEVICES.get(device_id)
                if not device:
                    raise ScannerError("Сетевой сканер не найден. Нажмите «Обновить устройства».")
                pages = scan_escl(device, source, color_mode, resolution, paper_size)
                self._json(200, {"ok": True, "device": device_id, "pages": pages})
            else:
                raise ScannerError("Неизвестное устройство сканирования. Обновите список устройств.")
        except (ValueError, TypeError, json.JSONDecodeError, ScannerError,
                OSError, TimeoutError, http.client.HTTPException) as exc:
            self._json(502, {"ok": False, "error": str(exc) or "Ошибка сканирования."})


if __name__ == "__main__":
    print("DocuDesk Local Bridge 0.5 — Windows WIA + eSCL/AirScan + Phone Scan")
    print("Discovery: Windows WIA and network eSCL/AirScan (Bonjour/mDNS)")
    print(f"Local endpoint: http://127.0.0.1:{PORT} (phone access through the computer LAN address)")
    print("Keep this window open while using the scanner. Close it to stop the bridge.")
    try:
        ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\nDocuDesk Local Bridge stopped.")
