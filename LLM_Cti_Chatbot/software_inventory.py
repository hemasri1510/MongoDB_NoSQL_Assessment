"""
ThreatGuard AI - installed-software inventory service for the FastAPI backend.

Scan sources (SOFTWARE_SCANNER_MODE):
  local - backend itself runs on Windows -> read the registry directly (software_scanner.py)
  agent - backend runs in Docker/Linux  -> ask the native Windows agent (windows_agent.py)
  auto  - (default) local on Windows, otherwise agent

Honesty rules enforced here:
  * "Installed"        -> at least one real registry entry matched
  * "Not Installed"    -> ONLY when scan_status == completed and nothing matched
  * "Unable to verify" -> scan failed or was partial and nothing matched
  * Every name/version/publisher shown comes from a scanned registry entry; nothing is invented.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Any, Dict, List, Optional

import requests

import software_scanner as sc
from software_scanner import (
    DEV_TOOLS_CATALOG,
    SCAN_COMPLETED,
    SCAN_FAILED,
    SCAN_PARTIAL,
    STATUS_INSTALLED,
    STATUS_NOT_INSTALLED,
    STATUS_UNVERIFIED,
    expand_terms,
    name_matches,
    search_applications,
)

DEFAULT_AGENT_URL = "http://host.docker.internal:8765"
MAX_SEARCH_CARDS = 6
MAX_LIST_CARDS = 60


# ----------------------------------------------------------------------------------------------
# Service (scan source + short cache)
# ----------------------------------------------------------------------------------------------
class SoftwareInventoryService:
    def __init__(self, mode: str = "auto", agent_url: str = DEFAULT_AGENT_URL, agent_token: str = "",
                 cache_ttl: float = 60.0, agent_timeout: float = 15.0, scan_fn=None):
        self.requested_mode = mode.lower()
        self.agent_url = agent_url.rstrip("/")
        self._token = agent_token
        self.cache_ttl = cache_ttl
        self.agent_timeout = agent_timeout
        self._scan_fn = scan_fn  # injectable for tests
        self._lock = threading.Lock()
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_time = 0.0

    @classmethod
    def from_env(cls) -> "SoftwareInventoryService":
        def _f(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, default))
            except ValueError:
                return default
        return cls(
            mode=os.getenv("SOFTWARE_SCANNER_MODE", "auto"),
            agent_url=os.getenv("SOFTWARE_AGENT_URL", DEFAULT_AGENT_URL),
            agent_token=os.getenv("SOFTWARE_AGENT_TOKEN", ""),
            cache_ttl=_f("SOFTWARE_CACHE_TTL", 60.0),
        )

    @property
    def mode(self) -> str:
        if self.requested_mode in ("local", "agent"):
            return self.requested_mode
        return "local" if (sys.platform == "win32" and sc._winreg is not None) else "agent"

    # -- scanning -------------------------------------------------------------------------
    def _failed(self, source: str, message: str) -> Dict[str, Any]:
        return {
            "scan_status": SCAN_FAILED, "applications": [], "total": 0, "source": source,
            "scanned_at": None, "errors": [{"location": source, "error": message}],
        }

    def _scan_via_agent(self) -> Dict[str, Any]:
        source = "windows-agent"
        headers = {"X-Agent-Token": self._token} if self._token else {}
        try:
            resp = requests.get(f"{self.agent_url}/inventory", headers=headers, timeout=self.agent_timeout)
        except requests.RequestException as exc:
            return self._failed(source, (
                f"Windows scanner agent not reachable at {self.agent_url} ({type(exc).__name__}). "
                "Start it on the Windows host with: python windows_agent.py"))
        if resp.status_code in (401, 403):
            return self._failed(source, "Windows scanner agent rejected the request (check SOFTWARE_AGENT_TOKEN).")
        if resp.status_code != 200:
            return self._failed(source, f"Windows scanner agent returned HTTP {resp.status_code}.")
        try:
            scan = resp.json().get("scan")
            apps = scan["applications"]
            status = scan["scan_status"]
            if not isinstance(apps, list) or status not in (SCAN_COMPLETED, SCAN_PARTIAL, SCAN_FAILED):
                raise ValueError("bad shape")
            clean_apps = [a for a in apps if isinstance(a, dict) and isinstance(a.get("name"), str) and a["name"].strip()]
        except Exception:
            return self._failed(source, "Windows scanner agent returned an unreadable response.")
        scan = dict(scan)
        scan["applications"] = clean_apps
        scan["total"] = len(clean_apps)
        scan["source"] = source
        scan.setdefault("errors", [])
        return scan

    def _scan_now(self) -> Dict[str, Any]:
        if self._scan_fn is not None:
            scan = self._scan_fn()
            scan.setdefault("source", "test")
            return scan
        if self.mode == "local":
            scan = sc.scan_registry()
            scan["source"] = "local-registry"
            return scan
        return self._scan_via_agent()

    def get_inventory(self, refresh: bool = False) -> Dict[str, Any]:
        """Return a scan (cached for cache_ttl seconds). Failed scans are never cached."""
        with self._lock:
            now = time.monotonic()
            if (not refresh and self._cache is not None and self.cache_ttl > 0
                    and now - self._cache_time < self.cache_ttl):
                return {**self._cache, "cached": True, "cache_age_seconds": round(now - self._cache_time, 1)}
            scan = self._scan_now()
            if scan.get("scan_status") != SCAN_FAILED:
                self._cache, self._cache_time = scan, time.monotonic()
            return {**scan, "cached": False, "cache_age_seconds": 0.0}

    # -- queries --------------------------------------------------------------------------
    def list_all(self, refresh: bool = False, include_updates: bool = False) -> Dict[str, Any]:
        inv = self.get_inventory(refresh)
        apps = [a for a in inv["applications"] if include_updates or not a.get("is_update")]
        return {**inv, "applications": apps, "total": len(apps)}

    def search(self, name: str, refresh: bool = False, include_updates: bool = False) -> Dict[str, Any]:
        inv = self.get_inventory(refresh)
        matches = search_applications(inv["applications"], name, include_updates)
        scan_status = inv["scan_status"]
        if matches:
            status = STATUS_INSTALLED
        elif scan_status == SCAN_COMPLETED:
            status = STATUS_NOT_INSTALLED
        else:
            status = STATUS_UNVERIFIED
        return {
            "query": name, "searched_terms": expand_terms(name), "status": status, "scan_status": scan_status,
            "scanned_at": inv.get("scanned_at"), "source": inv.get("source"), "cached": inv.get("cached", False),
            "cache_age_seconds": inv.get("cache_age_seconds", 0.0), "errors": inv.get("errors", []),
            "match_count": len(matches), "matches": matches,
        }

    def health(self) -> Dict[str, Any]:
        mode = self.mode
        info: Dict[str, Any] = {"scanner_mode": mode, "cache_ttl_seconds": self.cache_ttl, "read_only": True}
        if mode == "local":
            ok = sc._winreg is not None
            info.update(scanner_available=ok, status="ok" if ok else "unavailable",
                        detail="Reading the local Windows registry." if ok else "winreg is not available on this OS.")
        else:
            info["agent_url"] = self.agent_url
            headers = {"X-Agent-Token": self._token} if self._token else {}
            try:
                r = requests.get(f"{self.agent_url}/health", headers=headers, timeout=3)
                reachable = r.status_code == 200
                body = r.json() if reachable else {}
                on_windows = body.get("platform") == "win32"
                info.update(
                    scanner_available=reachable and on_windows,
                    agent_reachable=reachable,
                    agent_platform=body.get("platform"),
                    status="ok" if (reachable and on_windows) else "degraded",
                    detail=("Windows agent reachable." if reachable and on_windows else
                            "Agent reachable but not running on Windows." if reachable else
                            f"Agent returned HTTP {r.status_code} (check SOFTWARE_AGENT_TOKEN)."),
                )
            except Exception as exc:
                info.update(scanner_available=False, agent_reachable=False, status="unavailable",
                            detail=f"Agent not reachable ({type(exc).__name__}). Run: python windows_agent.py on the Windows host.")
        with self._lock:
            if self._cache is not None:
                info["last_scan"] = {
                    "scan_status": self._cache.get("scan_status"), "scanned_at": self._cache.get("scanned_at"),
                    "total_applications": self._cache.get("total"),
                    "age_seconds": round(time.monotonic() - self._cache_time, 1),
                }
            else:
                info["last_scan"] = None
        return info


_service: Optional[SoftwareInventoryService] = None
_service_lock = threading.Lock()


def get_service() -> SoftwareInventoryService:
    global _service
    with _service_lock:
        if _service is None:
            _service = SoftwareInventoryService.from_env()
        return _service


# ----------------------------------------------------------------------------------------------
# Chat intent detection
# ----------------------------------------------------------------------------------------------
# Threat/remediation questions keep going to the existing CTI + Gemini path.
_THREAT_WORDS = re.compile(r"\b(cve|vuln\w*|exploit\w*|malware|ransomware|patch\w*|mitigat\w*|remediat\w*|confirm\s+fix)\b", re.I)
_CONTEXT = re.compile(r"\b(installed|laptop|computer|pc|machine|system|my\s+device|this\s+device)\b", re.I)
_HAVE_CONTEXT = re.compile(r"\b(installed|have|on\s+my|in\s+my|laptop|computer|pc|machine)\b", re.I)
_INSTALL_CONTEXT = re.compile(r"\b(installed|on\s+my|in\s+my|laptop|computer|pc|machine)\b", re.I)
_REFRESH = re.compile(r"\b(refresh|re-?scan|scan\s+again)\b", re.I)
_DEV_TOOLS = re.compile(r"\b(development|dev|developer|programming|coding)\s+(tools?|software|environments?|apps?|applications?)\b", re.I)
_LIST_NOUN = re.compile(r"\b(applications?|apps|software|programs)\b", re.I)
_LIST_VERB = re.compile(r"\b(list|show|display|give|see|what|which|all)\b", re.I)
_GENERIC = {"it", "this", "that", "any", "anything", "something", "software", "application", "applications",
            "app", "apps", "program", "programs", "all", "everything", "tool", "tools"}

_NAME_PATTERNS = [
    # (regex, kind) - kind: "direct" (explicit "installed" wording), "version" (needs install context),
    # "have" (needs install context or a well-known app name)
    (re.compile(r"\b(?:which|what)\s+version\s+of\s+(?:the\s+)?(.+?)(?:\s+(?:is|are|do|am|have|did)\b.*|\s+on\s+my\b.*|\s+installed\b.*)?\s*[?.!]*$", re.I), "version"),
    (re.compile(r"\bdo\s+i\s+(?:have|got)\s+(?:the\s+)?(.+?)(?:\s+installed\b.*|\s+on\s+my\b.*|\s+in\s+my\b.*|\s+on\s+this\b.*)?\s*[?.!]*$", re.I), "have"),
    (re.compile(r"\b(?:if|whether)\s+(?:the\s+)?(.+?)\s+(?:is|are)\s+(?:currently\s+)?installed\b", re.I), "direct"),
    (re.compile(r"\b(?:is|are|was)\s+(?:the\s+)?(.+?)\s+(?:currently\s+)?(?:installed|present)\b", re.I), "direct"),
    (re.compile(r"\bhave\s+i\s+(?:got\s+)?installed\s+(?:the\s+)?(.+?)\s*[?.!]*$", re.I), "direct"),
    (re.compile(r"\b(?:check|verify|detect|find|confirm)\s+(?:if|whether|that)?\s*(?:i\s+have\s+)?(?:the\s+)?(.+?)\s+(?:is\s+|are\s+)?installed\b", re.I), "direct"),
    (re.compile(r"^\s*((?:(?!\b(?:is|are|which|what|version)\b).)+?)\s+installed\s*\?\s*$", re.I), "direct"),
]

_KNOWN_LABELS = [label for label, _ in DEV_TOOLS_CATALOG] + [
    "Google Chrome", "Mozilla Firefox", "Microsoft Edge", "MongoDB Compass", "VS Code", "VSCode", "Docker",
    "Node", "NodeJS", "Microsoft Office", "Zoom", "Slack", "VLC media player", "7-Zip", "WinRAR",
]


def _clean_name(raw: str) -> Optional[str]:
    name = raw.strip().strip("\"'`?.! ")
    name = re.sub(r"\s+(?:on|in)\s+(?:my|this|the)\s+\w+(?:\s+\w+)?$", "", name, flags=re.I)
    name = re.sub(r"^(?:my|an?|the)\s+", "", name, flags=re.I)
    name = re.sub(r"\s+(?:application|app|software|program|tool)$", "", name, flags=re.I)
    name = name.strip("\"'`?.! ")
    if not name or name.lower() in _GENERIC or len(name) > 60 or len(name.split()) > 6:
        return None
    return name


def detect_software_intent(message: str) -> Optional[Dict[str, Any]]:
    """Return {'intent': 'search'|'list'|'dev_tools', 'name'?: str, 'refresh': bool} or None."""
    msg = message.strip()
    if not msg or _THREAT_WORDS.search(msg):
        return None
    refresh = bool(_REFRESH.search(msg))

    if _DEV_TOOLS.search(msg) and (_CONTEXT.search(msg) or re.search(r"\b(do\s+i\s+have|i\s+have)\b", msg, re.I)):
        return {"intent": "dev_tools", "refresh": refresh}

    if _LIST_NOUN.search(msg) and _LIST_VERB.search(msg) and _CONTEXT.search(msg) and not re.search(r"\bversion\b", msg, re.I):
        # "what software is installed" / "list all installed applications" - but not "is X software installed"
        m = re.search(r"\b(?:is|are)\s+(?:the\s+)?(.+?)\s+(?:software|app|application|program)\s+installed\b", msg, re.I)
        if not m:
            return {"intent": "list", "refresh": refresh}

    for pattern, kind in _NAME_PATTERNS:
        m = pattern.search(msg)
        if not m:
            continue
        name = _clean_name(m.group(1))
        if not name:
            continue
        if kind == "version" and not _HAVE_CONTEXT.search(msg):
            continue  # "what version of Python is best?" is a general question, not an inventory one
        if kind == "have" and not _INSTALL_CONTEXT.search(msg) and not any(name_matches(k, name) for k in _KNOWN_LABELS):
            continue  # "do I have time to ..." is not a software question
        return {"intent": "search", "name": name, "refresh": refresh}
    return None


# ----------------------------------------------------------------------------------------------
# Result shaping (cards + markdown answer)
# ----------------------------------------------------------------------------------------------
def _md(text: Optional[str]) -> str:
    """Neutralise markdown/HTML control characters in registry-supplied strings."""
    if not text:
        return ""
    return re.sub(r"([\\`*_{}\[\]<>()#+!|~])", r"\\\1", text)


def _card(app: Optional[Dict[str, Any]], status: str, scan_status: str, fallback_name: str = "",
          extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    card = {
        "name": app["name"] if app else fallback_name,
        "status": status,
        "version": app.get("version") if app else None,
        "publisher": app.get("publisher") if app else None,
        "install_date": app.get("install_date") if app else None,
        "install_location": app.get("install_location") if app else None,
        "scan_status": scan_status,
    }
    if extra:
        card.update(extra)
    return card


def _scan_line(res: Dict[str, Any]) -> str:
    when = res.get("scanned_at") or "n/a"
    cached = " (cached - say \"refresh\" to rescan)" if res.get("cached") else ""
    return f"_Scan status: {res.get('scan_status')} • source: {res.get('source')} • {when}{cached}_"


def _first_error(res: Dict[str, Any]) -> str:
    errs = res.get("errors") or []
    return errs[0]["error"] if errs and isinstance(errs[0], dict) else "unknown error"


def build_search_reply(res: Dict[str, Any]) -> Dict[str, Any]:
    q, status, scan_status = res["query"], res["status"], res["scan_status"]
    matches = res["matches"][:MAX_SEARCH_CARDS]
    if status == STATUS_INSTALLED:
        cards = [_card(a, STATUS_INSTALLED, scan_status) for a in matches]
        lines = [f"**{_md(q)}: Installed** ✅ — {res['match_count']} matching entr{'y' if res['match_count'] == 1 else 'ies'} found in the Windows software inventory."]
        for a in matches:
            lines.append(f"- **{_md(a['name'])}** — version `{_md(a.get('version')) or 'not reported'}` • publisher: {_md(a.get('publisher')) or 'not reported'}")
        if res["match_count"] > len(matches):
            lines.append(f"- …and {res['match_count'] - len(matches)} more (see `GET /installed-software/search?name=...`).")
        if scan_status == SCAN_PARTIAL:
            lines.append("\n⚠️ The scan was partial, so the list above may be incomplete.")
    elif status == STATUS_NOT_INSTALLED:
        cards = [_card(None, STATUS_NOT_INSTALLED, scan_status, fallback_name=q)]
        lines = [f"**{_md(q)}: Not Installed** ❌ — the scan completed and no installed application matches this name.",
                 "_Note: this scan reads the Windows uninstall registry. Portable copies and some Microsoft Store apps do not register there._"]
    else:
        cards = [_card(None, STATUS_UNVERIFIED, scan_status, fallback_name=q)]
        lines = [f"**{_md(q)}: Unable to verify** ⚠️ — the Windows software scan {'was incomplete' if scan_status == SCAN_PARTIAL else 'failed'}: {_md(_first_error(res))}",
                 "I won't guess whether it is installed."]
    lines.append(_scan_line(res))
    return {"answer": "\n".join(lines),
            "software_result": {"intent": "search", "query": q, "status": status, "scan_status": scan_status,
                                "scanned_at": res.get("scanned_at"), "source": res.get("source"),
                                "cached": res.get("cached", False), "cards": cards}}


def build_list_reply(res: Dict[str, Any]) -> Dict[str, Any]:
    scan_status = res["scan_status"]
    if scan_status == SCAN_FAILED:
        answer = (f"**Installed applications: Unable to verify** ⚠️ — the Windows software scan failed: {_md(_first_error(res))}\n"
                  f"{_scan_line(res)}")
        return {"answer": answer, "software_result": {
            "intent": "list", "status": STATUS_UNVERIFIED, "scan_status": scan_status, "scanned_at": res.get("scanned_at"),
            "source": res.get("source"), "cached": res.get("cached", False), "total": 0, "cards": []}}
    apps = res["applications"]
    shown = apps[:MAX_LIST_CARDS]
    cards = [_card(a, STATUS_INSTALLED, scan_status) for a in shown]
    answer = f"Found **{len(apps)}** installed applications (updates/hotfixes excluded)."
    if len(apps) > len(shown):
        answer += f" Showing the first {len(shown)} alphabetically — the full list is at `GET /installed-software`."
    if scan_status == SCAN_PARTIAL:
        answer += "\n\n⚠️ The scan was partial, so this list may be incomplete."
    answer += "\n\n" + _scan_line(res)
    return {"answer": answer, "software_result": {
        "intent": "list", "status": STATUS_INSTALLED, "scan_status": scan_status, "scanned_at": res.get("scanned_at"),
        "source": res.get("source"), "cached": res.get("cached", False), "total": len(apps),
        "truncated": len(apps) > len(shown), "cards": cards}}


def build_dev_tools_reply(res: Dict[str, Any]) -> Dict[str, Any]:
    scan_status = res["scan_status"]
    if scan_status == SCAN_FAILED:
        out = build_list_reply(res)
        out["answer"] = out["answer"].replace("Installed applications", "Development tools")
        out["software_result"]["intent"] = "dev_tools"
        return out
    cards, lines = [], []
    for label, terms in DEV_TOOLS_CATALOG:
        found: Dict[int, Dict[str, Any]] = {}
        for term in terms:
            for a in search_applications(res["applications"], term):
                found[id(a)] = a
        if not found:
            continue
        ordered = sorted(found.values(), key=lambda a: a["name"].lower())
        best = ordered[0]
        extra = {"category": label, "related_entries": len(ordered) - 1}
        cards.append(_card(best, STATUS_INSTALLED, scan_status, extra=extra))
        lines.append(f"- **{_md(label)}** — {_md(best['name'])}, version `{_md(best.get('version')) or 'not reported'}`"
                     + (f" (+{len(ordered) - 1} related entries)" if len(ordered) > 1 else ""))
    if cards:
        answer = f"**Development tools detected: {len(cards)}**\n" + "\n".join(lines)
        answer += f"\n\n_Checked against a fixed list of {len(DEV_TOOLS_CATALOG)} common dev tools; others may exist - ask me about any by name._"
    else:
        answer = "No common development tools from my checklist were found in the completed scan." \
            if scan_status == SCAN_COMPLETED else "No dev tools found, but the scan was partial so I can't be sure."
    if scan_status == SCAN_PARTIAL:
        answer += "\n\n⚠️ The scan was partial, so this list may be incomplete."
    answer += "\n\n" + _scan_line(res)
    return {"answer": answer, "software_result": {
        "intent": "dev_tools", "status": STATUS_INSTALLED if cards else (STATUS_NOT_INSTALLED if scan_status == SCAN_COMPLETED else STATUS_UNVERIFIED),
        "scan_status": scan_status, "scanned_at": res.get("scanned_at"), "source": res.get("source"),
        "cached": res.get("cached", False), "total": len(cards), "cards": cards}}


def answer_software_question(message: str, service: Optional[SoftwareInventoryService] = None) -> Optional[Dict[str, Any]]:
    """Blocking. Returns {'answer', 'software_result'} for software questions, else None (-> normal chat)."""
    intent = detect_software_intent(message)
    if intent is None:
        return None
    svc = service or get_service()
    if intent["intent"] == "search":
        return build_search_reply(svc.search(intent["name"], refresh=intent["refresh"]))
    if intent["intent"] == "list":
        return build_list_reply(svc.list_all(refresh=intent["refresh"]))
    return build_dev_tools_reply(svc.list_all(refresh=intent["refresh"]))
