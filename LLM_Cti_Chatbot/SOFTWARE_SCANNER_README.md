# Windows installed-software detection (ThreatGuard AI)

Read-only scan of the three Windows `Uninstall` registry locations (HKLM, HKLM\WOW6432Node, HKCU).
No registry writes, no uninstalling, no admin rights, no personal files / browser data / env vars.

## Architecture
A Docker container cannot read the Windows host registry, so:

    Streamlit (8501) -> FastAPI backend (Docker, 8000) -> http://host.docker.internal:8765 -> windows_agent.py (Windows host) -> registry

If you run the backend directly on Windows (`uvicorn main:app`), it reads the registry itself (`SOFTWARE_SCANNER_MODE=auto`) and the agent is not needed.

## Run (Docker backend)
1. On the Windows host (PowerShell, in this folder):
       python windows_agent.py            # or start_windows_agent.bat
   Optional shared secret (same value for both sides): `$env:SOFTWARE_AGENT_TOKEN="long-random"` before starting the agent,
   and `SOFTWARE_AGENT_TOKEN=long-random` in `.env` (add `- SOFTWARE_AGENT_TOKEN=${SOFTWARE_AGENT_TOKEN}` to the backend `environment:` if you use it).
2. `docker compose up --build`
3. Open http://localhost:8501 and ask: "Is VS Code installed on my laptop?"

## Endpoints
    GET /installed-software[?refresh=true&include_updates=true]
    GET /installed-software/search?name=Visual%20Studio%20Code[&refresh=true]
    GET /installed-software/health

## Verify on your machine
    python verify_software_scan.py           # real registry: 7 target apps + reg.exe cross-check
    pytest test_software_inventory.py -v     # logic tests (fake registry, any OS)

## Result semantics
Installed = a real registry entry matched. Not Installed = scan completed with no match (portable copies and some
Microsoft Store apps never register in Uninstall). Unable to verify = scan failed/partial or agent unreachable.
Scans are cached for 60 s (`SOFTWARE_CACHE_TTL`); say "refresh" in chat, use `?refresh=true`, or the sidebar button.
