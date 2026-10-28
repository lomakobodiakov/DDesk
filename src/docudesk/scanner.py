from __future__ import annotations

import os
import tempfile
from pathlib import Path

from PIL import Image


def _require_windows_dependencies():
    if os.name != "nt":
        raise RuntimeError("Сканирование через WIA доступно только в Windows.")
    try:
        import win32com.client  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Для сканирования установите pywin32 и запустите программу в Windows.") from exc


def list_scanners() -> list[dict[str, str]]:
    """List WIA scanner devices visible to Windows; network scanners need a WIA driver."""
    _require_windows_dependencies()
    import win32com.client

    manager = win32com.client.Dispatch("WIA.DeviceManager")
    devices: list[dict[str, str]] = []
    for info in manager.DeviceInfos:
        try:
            if int(info.Type) != 1:  # ScannerDeviceType
                continue
            name = "Сканер"
            device_id = str(info.DeviceID)
            for prop in info.Properties:
                if str(prop.Name).lower() == "name":
                    name = str(prop.Value)
                    break
            devices.append({"id": device_id, "name": name})
        except Exception:
            continue
    return devices


def scan_one_page(device_id: str, output_path: str | Path, color: bool = True, dpi: int = 300) -> Path:
    """Scan one page using WIA. ADF batch/duplex depends on driver and is planned for the next iteration."""
    _require_windows_dependencies()
    import win32com.client

    manager = win32com.client.Dispatch("WIA.DeviceManager")
    selected = None
    for info in manager.DeviceInfos:
        if str(info.DeviceID) == device_id:
            selected = info
            break
    if selected is None:
        raise RuntimeError("Сканер не найден. Проверьте подключение и драйвер.")

    device = selected.Connect()
    item = device.Items(1)
    # WIA properties: 6147 horizontal resolution, 6148 vertical resolution, 6146 color intent.
    for property_id, value in ((6147, dpi), (6148, dpi), (6146, 1 if color else 4)):
        try:
            item.Properties(f"{property_id}").Value = value
        except Exception:
            pass  # Many drivers expose a different supported set of properties.
    image = item.Transfer()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # WIA drivers may return BMP regardless of the chosen filename extension.
    # Save the raw transfer first, then use Pillow to create a correctly encoded output.
    with tempfile.TemporaryDirectory(prefix="docudesk_scan_") as temp_dir:
        raw_path = Path(temp_dir) / "wia_transfer.bmp"
        image.SaveFile(str(raw_path))
        with Image.open(raw_path) as scanned:
            suffix = output_path.suffix.lower()
            if suffix in {".jpg", ".jpeg"}:
                scanned.convert("RGB").save(output_path, quality=95)
            elif suffix in {".tif", ".tiff"}:
                scanned.save(output_path, format="TIFF")
            else:
                scanned.save(output_path, format="PNG")
    return output_path
