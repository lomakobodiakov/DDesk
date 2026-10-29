# DocuDesk Local Bridge — Canon MF440 Series

This Windows helper connects the DocuDesk GitHub Pages interface to the Canon MF440 Series scanner at `192.168.5.180` using eSCL.

## Current features

- Listens only on `127.0.0.1:8765`.
- Allows browser requests only from `https://lomakobodiakov.github.io`.
- Checks scanner capabilities via `/capabilities`.
- Creates an eSCL scan job via `POST /scan`.
- Retrieves JPEG/PNG pages from the scanner and returns them to the browser.
- The web app previews pages and can assemble them into a PDF locally in the browser.

## Run

1. Download the latest repository ZIP from GitHub and extract it.
2. Ensure Python 3 is installed on Windows.
3. Double-click `start_local_bridge.bat` and keep its console open.
4. Open `https://lomakobodiakov.github.io/DDesk/#scanner`.
5. Click **Проверить подключение**, then choose source, paper size, color mode and DPI.
6. Put paper on the platen or in the feeder, then click **Сканировать**.
7. Review the page previews and click **Сохранить все страницы в PDF**.

## Notes and troubleshooting

- The configured scanner address is fixed to `192.168.5.180`; the PC must be able to reach it on the LAN.
- If Chrome asks to allow local-network access, allow it for the DocuDesk site.
- The scanner's exact supported modes can vary by model, firmware and capability response. If the device rejects a scan job, try 300 dpi, A4, Color, and Platen first, then share the exact error.
- This first implementation requests JPEG pages and supports JPEG/PNG responses. PDF generation is performed in the browser.
- The helper caps each image at 12 MiB and one job at 20 pages.
- It binds only to loopback. Do not change the bind address to `0.0.0.0`; do not expose the helper to the LAN or internet.
- Only the exact GitHub Pages origin is allowed for CORS.

## API

- `GET /health` — local helper status.
- `GET /capabilities` — scanner capability XML and discovered input sources.
- `POST /scan` — starts a scan. JSON body example:

```json
{
  "source": "Platen",
  "colorMode": "RGB24",
  "resolution": 300,
  "paperSize": "A4"
}
```

The `/scan` request can take several minutes while the scanner works.
