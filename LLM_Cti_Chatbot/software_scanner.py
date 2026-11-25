"""
ThreatGuard AI - Windows installed-software scanner (READ-ONLY).

Reads the three standard "Uninstall" registry locations and extracts, per application:
name, display version, publisher, install date and install location. Nothing else is read.

Safety guarantees
-----------------
* Registry keys are opened with KEY_READ only (+ an explicit 64-bit view flag). There is no
  call anywhere in this module that creates, sets or deletes a registry key/value, and nothing
  that runs or uninstalls an application.
* No administrator rights are needed: all three locations are readable by standard users.
* No files, browser data, environment variables or credentials are touched.

The module is pure Python + the stdlib `winreg` module, so it only *scans* on Windows. On other
operating systems `scan_registry()` returns scan_status="failed" (never an empty "completed" scan),
which callers must treat as "Unable to verify".
"""

from __future__ import annotations

import re
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:  # winreg only exists on Windows
    import winreg as _winreg  # type: ignore
except ImportError:  # pragma: no cover - exercised on Linux/macOS
    _winreg = None

# (hive attribute name, registry path, human label)
UNINSTALL_LOCATIONS: List[Tuple[str, str, str]] = [
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM\\...\\Uninstall"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall", "HKLM\\...\\WOW6432Node\\...\\Uninstall"),
    ("HKEY_CURRENT_USER", r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", "HKCU\\...\\Uninstall"),
]

STATUS_INSTALLED = "Installed"
STATUS_NOT_INSTALLED = "Not Installed"
STATUS_UNVERIFIED = "Unable to verify"

SCAN_COMPLETED = "completed"
SCAN_PARTIAL = "partial"
SCAN_FAILED = "failed"


def _clean(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).replace("\x00", "").strip()
    text = re.sub(r"[\r\n\t]+", " ", text)
    return text or None


def _format_install_date(raw: Any) -> Optional[str]:
    """Registry InstallDate is usually YYYYMMDD. Return ISO date, else None (never guess)."""
    text = _clean(raw)
    if text and re.fullmatch(r"\d{8}", text):
        try:
            return datetime.strptime(text, "%Y%m%d").date().isoformat()
        except ValueError:
            return None
    return None


def _read_value(key: Any, name: str, winreg: Any) -> Any:
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:  # FileNotFoundError when the value does not exist
        return None


def _scan_location(winreg: Any, hive_name: str, path: str, label: str) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, str]], bool]:
    """
    Returns (apps, error, location_present).
    A missing key (FileNotFoundError) is NOT an error - e.g. HKCU may have no Uninstall key.
    """
    apps: List[Dict[str, Any]] = []
    hive = getattr(winreg, hive_name)
    # KEY_READ is read-only. KEY_WOW64_64KEY pins the 64-bit view so results are identical
    # whether the interpreter is 32-bit or 64-bit (the WOW6432Node path is then read explicitly).
    access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
    error: Optional[Dict[str, str]] = None
    try:
        root = winreg.OpenKey(hive, path, 0, access)
    except FileNotFoundError:
        return apps, None, False
    except OSError as exc:
        return apps, {"location": label, "error": f"{type(exc).__name__}: {exc}"}, False

    with root:
        index = 0
        while True:
            try:
                sub_name = winreg.EnumKey(root, index)
            except OSError:  # no more items
                break
            index += 1
            try:
                with winreg.OpenKey(root, sub_name, 0, access) as sub:
                    name = _clean(_read_value(sub, "DisplayName", winreg))
                    if not name:
                        continue
                    parent = _clean(_read_value(sub, "ParentKeyName", winreg))
                    release = (_clean(_read_value(sub, "ReleaseType", winreg)) or "").lower()
                    is_update = bool(parent) or release in {"security update", "update rollup", "hotfix", "update"}
                    sys_comp = _read_value(sub, "SystemComponent", winreg)
                    apps.append(
                        {
                            "name": name,
                            "version": _clean(_read_value(sub, "DisplayVersion", winreg)),
                            "publisher": _clean(_read_value(sub, "Publisher", winreg)),
                            "install_date": _format_install_date(_read_value(sub, "InstallDate", winreg)),
                            "install_location": _clean(_read_value(sub, "InstallLocation", winreg)),
                            "is_update": is_update,
                            "system_component": bool(sys_comp) and str(sys_comp) not in {"0", ""},
                            "source_key": label,
                        }
                    )
            except FileNotFoundError:
                continue  # entry removed while scanning
            except OSError as exc:
                error = {"location": label, "error": f"unreadable entry '{sub_name}': {type(exc).__name__}"}
    return apps, error, True


def _dedupe(apps: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for app in apps:
        key = (app["name"].lower(), (app["version"] or "").lower(), (app["publisher"] or "").lower())
        if key in merged:
            existing = merged[key]
            if app["source_key"] not in existing["source_key"].split(" + "):
                existing["source_key"] += " + " + app["source_key"]
            for field in ("install_date", "install_location"):
                existing[field] = existing[field] or app[field]
        else:
            merged[key] = dict(app)
    return sorted(merged.values(), key=lambda a: a["name"].lower())


def scan_registry(winreg_module: Any = None) -> Dict[str, Any]:
    """
    Perform one read-only scan. Never raises.

    scan_status:
      completed - every location was readable (or legitimately absent) -> a negative result is trustworthy
      partial   - some location/entry raised an error -> a negative result is NOT trustworthy
      failed    - could not scan at all (not Windows, or no location readable)
    """
    winreg = winreg_module if winreg_module is not None else _winreg
    started = time.perf_counter()
    base = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "platform": sys.platform,
        "applications": [],
        "total": 0,
        "errors": [],
        "locations_scanned": 0,
    }
    if winreg is None:
        base.update(
            scan_status=SCAN_FAILED,
            errors=[{"location": "winreg", "error": "The Windows registry is not accessible from this operating system. "
                                                  "Run the scanner on the Windows host."}],
            duration_ms=0,
        )
        return base

    all_apps: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    present = 0
    try:
        for hive_name, path, label in UNINSTALL_LOCATIONS:
            apps, err, found = _scan_location(winreg, hive_name, path, label)
            all_apps.extend(apps)
            present += 1 if found else 0
            if err:
                errors.append(err)
    except Exception as exc:  # defensive: never let a scan crash the API
        errors.append({"location": "scanner", "error": f"{type(exc).__name__}: {exc}"})

    if present == 0:
        status = SCAN_FAILED
        if not errors:
            errors.append({"location": "registry", "error": "No Uninstall registry location could be opened."})
    elif errors:
        status = SCAN_PARTIAL
    else:
        status = SCAN_COMPLETED

    deduped = _dedupe(all_apps)
    base.update(
        scan_status=status,
        applications=deduped,
        total=len(deduped),
        errors=errors,
        locations_scanned=present,
        duration_ms=round((time.perf_counter() - started) * 1000, 1),
    )
    return base


# ----------------------------------------------------------------------------------------------
# Matching (partial, case-insensitive). Aliases only widen the SEARCH; results always come from
# real registry entries, so nothing can be invented.
# ----------------------------------------------------------------------------------------------

ALIASES: Dict[str, List[str]] = {
    "vscode": ["visual studio code"],
    "vs code": ["visual studio code"],
    "microsoft vs code": ["visual studio code"],
    "jdk": ["jdk", "java development kit"],
    "dotnet": ["net sdk", "net runtime"],
    "node": ["node.js"],
}

DEV_TOOLS_CATALOG: List[Tuple[str, List[str]]] = [
    ("Visual Studio Code", ["visual studio code"]),
    ("Visual Studio", ["microsoft visual studio"]),
    ("Python", ["python"]),
    ("Node.js", ["node.js"]),
    ("Git", ["git"]),
    ("GitHub Desktop", ["github desktop"]),
    ("Docker Desktop", ["docker desktop"]),
    ("MongoDB", ["mongodb"]),
    ("Java / JDK", ["jdk", "java development kit", "openjdk"]),
    (".NET SDK", ["net sdk"]),
    ("Postman", ["postman"]),
    ("PyCharm", ["pycharm"]),
    ("IntelliJ IDEA", ["intellij idea"]),
    ("Android Studio", ["android studio"]),
    ("CMake", ["cmake"]),
    ("Notepad++", ["notepad++"]),
]


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _compact(text: str) -> str:
    return "".join(_tokens(text))


def expand_terms(query: str) -> List[str]:
    q = " ".join(_tokens(query))
    terms = [query.strip()]
    for alias, targets in ALIASES.items():
        if q == " ".join(_tokens(alias)):
            terms.extend(targets)
    return list(dict.fromkeys(t for t in terms if t))


def name_matches(app_name: str, term: str) -> bool:
    q_tokens = _tokens(term)
    if not q_tokens:
        return False
    n_tokens = _tokens(app_name)
    if all(any(nt.startswith(qt) for nt in n_tokens) for qt in q_tokens):
        return True
    compact_q = _compact(term)
    return len(compact_q) >= 4 and compact_q in _compact(app_name)


def search_applications(applications: List[Dict[str, Any]], query: str, include_updates: bool = False) -> List[Dict[str, Any]]:
    terms = expand_terms(query)
    hits = []
    for app in applications:
        if app.get("is_update") and not include_updates:
            continue
        for term in terms:
            if name_matches(app["name"], term):
                n_app, n_term = " ".join(_tokens(app["name"])), " ".join(_tokens(term))
                rank = 0 if n_app == n_term else 1 if n_app.startswith(n_term) else 2
                hits.append((rank, app["name"].lower(), app))
                break
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2] for h in hits]
