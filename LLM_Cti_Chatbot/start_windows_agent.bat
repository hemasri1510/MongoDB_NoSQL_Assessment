@echo off
REM ThreatGuard AI - read-only Windows software scanner agent (run on the Windows HOST, not in Docker).
REM Optional: set SOFTWARE_AGENT_TOKEN=some-long-random-string  (put the same value in .env for the backend)
cd /d "%~dp0"
python windows_agent.py
