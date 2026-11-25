"""
Run this ON THE WINDOWS HOST (not in Docker) to verify the scanner against your real registry.

    python verify_software_scan.py            # table for the 7 target apps + independent cross-check
    python verify_software_scan.py "Slack"    # also check extra application names

Step 1 uses the same read-only scanner the backend uses.
Step 2 cross-checks by asking Windows' own `reg.exe query` (read-only) for every DisplayName in the three
Uninstall locations, then compares it with what the scanner found. A mismatch means a scanner bug.
Nothing is modified; no admin rights are needed.
"""
import re
import subprocess
import sys

import software_scanner as sc
from software_scanner import UNINSTALL_LOCATIONS, search_applications

TARGETS = ["Visual Studio Code", "Python", "MongoDB", "MongoDB Compass", "Docker Desktop", "Google Chrome", "Node.js"]
HIVE_SHORT = {"HKEY_LOCAL_MACHINE": "HKLM", "HKEY_CURRENT_USER": "HKCU"}


def reg_exe_display_names(hive: str, path: str) -> set:
    cmd = ["reg", "query", f"{HIVE_SHORT[hive]}\\{path}", "/s", "/v", "DisplayName", "/reg:64"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60, shell=False)
    if proc.returncode != 0:  # key absent
        return set()
    names = set()
    for line in proc.stdout.splitlines():
        m = re.match(r"^\s+DisplayName\s+REG_(?:EXPAND_)?SZ\s+(.*\S)\s*$", line)
        if m:
            names.add(m.group(1))
    return names


def main() -> int:
    if sys.platform != "win32":
        print("This must run on Windows (the registry does not exist here).")
        return 2
    scan = sc.scan_registry()
    print(f"Scan status: {scan['scan_status']} | applications: {scan['total']} | locations readable: "
          f"{scan['locations_scanned']}/3 | {scan.get('duration_ms')} ms")
    for err in scan["errors"]:
        print("  error:", err)
    if scan["scan_status"] == "failed":
        return 1

    print("\n== Target applications ==")
    for q in TARGETS + sys.argv[1:]:
        hits = search_applications(scan["applications"], q)
        if hits:
            for h in hits[:6]:
                print(f"  {q:<20} INSTALLED      {h['name']} | v{h['version']} | {h['publisher']} | {h['source_key']}")
        else:
            verdict = "NOT INSTALLED" if scan["scan_status"] == "completed" else "UNABLE TO VERIFY"
            print(f"  {q:<20} {verdict}")

    print("\n== Independent cross-check with reg.exe ==")
    ok = True
    for hive, path, label in UNINSTALL_LOCATIONS:
        ref = reg_exe_display_names(hive, path)
        mine = {a["name"] for a in scan["applications"] if label in a["source_key"].split(" + ")}
        missing, extra = ref - mine, mine - ref
        flag = "OK" if not missing and not extra else "MISMATCH"
        ok &= flag == "OK"
        print(f"  {label:<40} reg.exe={len(ref):<4} scanner={len(mine):<4} {flag}")
        for n in sorted(missing)[:5]:
            print("     missing from scanner:", n)
        for n in sorted(extra)[:5]:
            print("     not seen by reg.exe :", n)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
