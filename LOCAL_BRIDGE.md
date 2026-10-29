# DocuDesk Local Bridge — first milestone

This helper solves the first connection step between the GitHub Pages interface and the Canon MF440 Series network scanner.

## What it does now

- Listens only on `127.0.0.1:8765` (not exposed to the LAN).
- Allows browser requests only from `https://lomakobodiakov.github.io`.
- Requests `http://192.168.5.180/eSCL/ScannerCapabilities` from Windows and returns the XML to DocuDesk.
- Includes a health endpoint at `http://127.0.0.1:8765/health`.

This first milestone tests the local bridge and scanner capability response only. It does not yet create scan jobs or download scanned pages.

## Run on Windows

1. Download the repository or copy `local_bridge.py` and `start_local_bridge.bat` into the same folder.
2. Ensure Python 3 is installed.
3. Double-click `start_local_bridge.bat`.
4. Keep its console window open.
5. Open DocuDesk at `https://lomakobodiakov.github.io/DDesk/#scanner`.
6. In Chrome, allow local network access if a permission prompt appears.

The current bridge is configured for the known printer address `192.168.5.180`. It requires the PC and printer to be on the same reachable network.

## Security notes

- The bridge binds to loopback only.
- It uses a fixed printer IP and fixed eSCL path; it is not a general-purpose proxy.
- It permits CORS only for the GitHub Pages origin above.
- Do not expose this service by changing the bind address to `0.0.0.0`.

## Next milestone

After the health and capabilities tests pass from the web page, add eSCL scan-job creation, polling, and document download, then package the helper as a Windows executable.
