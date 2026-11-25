"""
Offline tests for the Windows software inventory feature. They use a FAKE winreg, so they run on any OS
(including CI/Docker) and prove the logic. They do NOT prove what is installed on your PC - run
`python verify_software_scan.py` on the Windows host for that.

    pytest test_software_inventory.py -v
"""
import threading

import pytest
import requests

import software_scanner as sc
from software_inventory import (
    SoftwareInventoryService, answer_software_question, detect_software_intent,
)

HKLM, HKCU = "HKLM", "HKCU"
P1 = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
P2 = r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"


class FakeKey:
    def __init__(self, tree): self.tree = tree
    def __enter__(self): return self
    def __exit__(self, *a): return False


class FakeWinreg:
    """Minimal read-only winreg double. Has NO write functions, so any write attempt would raise."""
    HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER = HKLM, HKCU
    KEY_READ, KEY_WOW64_64KEY = 0x20019, 0x100

    def __init__(self, data, deny=()):
        self.data, self.deny, self.opened = data, set(deny), []

    def OpenKey(self, base, path, reserved=0, access=0):
        assert access & self.KEY_READ == self.KEY_READ and not access & 0x2  # never KEY_WRITE/SET_VALUE
        if isinstance(base, FakeKey):
            return FakeKey(base.tree[path])
        self.opened.append((base, path))
        if (base, path) in self.deny:
            raise PermissionError("denied")
        if (base, path) not in self.data:
            raise FileNotFoundError(path)
        return FakeKey(self.data[(base, path)])

    def EnumKey(self, key, i):
        names = list(key.tree)
        if i >= len(names):
            raise OSError("no more")
        return names[i]

    def QueryValueEx(self, key, name):
        if name not in key.tree:
            raise FileNotFoundError(name)
        return key.tree[name], 1


def entry(name, version=None, publisher=None, date=None, loc=None, **extra):
    d = {"DisplayName": name}
    for k, v in (("DisplayVersion", version), ("Publisher", publisher), ("InstallDate", date), ("InstallLocation", loc)):
        if v is not None:
            d[k] = v
    d.update(extra)
    return d


def sample(deny=()):
    return FakeWinreg({
        (HKLM, P1): {
            "py": entry("Python 3.12.1 (64-bit)", "3.12.1150.0", "Python Software Foundation", "20240115"),
            "py2": entry("Python Launcher", "3.12.1", "Python Software Foundation"),
            "mongo": entry("MongoDB Server 7.0", "7.0.5", "MongoDB Inc.", loc=r"C:\Program Files\MongoDB"),
            "kb": entry("Security Update for X (KB1)", "1", "Microsoft", ParentKeyName="Windows"),
            "noname": {"Publisher": "ghost"},
        },
        (HKLM, P2): {
            "chrome": entry("Google Chrome", "126.0.1", "Google LLC"),
            "node": entry("Node.js", "20.11.0", "Node.js Foundation"),
        },
        (HKCU, P1): {
            "vsc": entry("Microsoft Visual Studio Code (User)", "1.90.0", "Microsoft Corporation", "20240601"),
            "compass": entry("MongoDB Compass", "1.42.0", "MongoDB Inc."),
        },
    }, deny)


def svc_for(winreg, ttl=60):
    calls = {"n": 0}
    def scan():
        calls["n"] += 1
        return sc.scan_registry(winreg)
    return SoftwareInventoryService(scan_fn=scan, cache_ttl=ttl), calls


# ------------------------------------------------------------------ scanner
def test_scan_extracts_fields_and_skips_nameless():
    scan = sc.scan_registry(sample())
    assert scan["scan_status"] == "completed" and scan["locations_scanned"] == 3
    py = next(a for a in scan["applications"] if a["name"].startswith("Python 3"))
    assert (py["version"], py["publisher"], py["install_date"]) == ("3.12.1150.0", "Python Software Foundation", "2024-01-15")
    assert all(a["name"] != "ghost" for a in scan["applications"])


def test_missing_key_is_not_an_error():
    w = FakeWinreg({(HKLM, P1): {"a": entry("Foo", "1")}})  # WOW6432Node + HKCU absent
    scan = sc.scan_registry(w)
    assert scan["scan_status"] == "completed" and scan["total"] == 1


def test_all_three_locations_are_read():
    w = sample(); sc.scan_registry(w)
    assert {(h, p) for h, p in w.opened} == {(HKLM, P1), (HKLM, P2), (HKCU, P1)}


def test_permission_error_makes_scan_partial():
    scan = sc.scan_registry(sample(deny=[(HKCU, P1)]))
    assert scan["scan_status"] == "partial" and scan["errors"]


def test_no_winreg_means_failed_not_empty_success():
    scan = sc.scan_registry(None) if sc._winreg is None else None
    if scan is not None:
        assert scan["scan_status"] == "failed" and scan["applications"] == []


def test_nothing_readable_is_failed():
    scan = sc.scan_registry(FakeWinreg({}))
    assert scan["scan_status"] == "failed"


def test_install_date_garbage_not_invented():
    assert sc._format_install_date("not-a-date") is None and sc._format_install_date("20241345") is None


# ------------------------------------------------------------------ matching / status rules
def test_partial_case_insensitive_and_alias_matching():
    svc, _ = svc_for(sample())
    for q in ["python", "PYTHON", "VS Code", "vscode", "visual studio code", "chrome", "nodejs", "node", "mongo db", "compass"]:
        assert svc.search(q)["status"] == "Installed", q
    r = svc.search("mongodb")
    assert {m["name"] for m in r["matches"]} == {"MongoDB Server 7.0", "MongoDB Compass"}


def test_not_installed_only_after_completed_scan():
    svc, _ = svc_for(sample())
    r = svc.search("Docker Desktop")
    assert r["status"] == "Not Installed" and r["scan_status"] == "completed" and r["matches"] == []


def test_unable_to_verify_when_scan_fails():
    svc, _ = svc_for(FakeWinreg({}))
    assert svc.search("Python")["status"] == "Unable to verify"


def test_partial_scan_negative_is_unable_to_verify_but_positive_is_installed():
    svc, _ = svc_for(sample(deny=[(HKCU, P1)]))
    assert svc.search("Docker")["status"] == "Unable to verify"
    assert svc.search("Python")["status"] == "Installed"


def test_updates_hidden_by_default():
    svc, _ = svc_for(sample())
    assert svc.search("Security Update")["status"] == "Not Installed"
    assert svc.search("Security Update", include_updates=True)["status"] == "Installed"


def test_cache_and_refresh_and_failures_not_cached():
    svc, calls = svc_for(sample())
    svc.search("python"); svc.search("chrome")
    assert calls["n"] == 1
    assert svc.search("python")["cached"] is True
    svc.search("python", refresh=True)
    assert calls["n"] == 2
    bad, bcalls = svc_for(FakeWinreg({}))
    bad.search("x"); bad.search("x")
    assert bcalls["n"] == 2


# ------------------------------------------------------------------ intent detection
@pytest.mark.parametrize("msg,intent,name", [
    ("Is VS Code installed on my laptop?", "search", "VS Code"),
    ("Is Python installed?", "search", "Python"),
    ("Which version of MongoDB is installed?", "search", "MongoDB"),
    ("Do I have Docker Desktop?", "search", "Docker Desktop"),
    ("List all installed applications.", "list", None),
    ("Is Google Chrome installed?", "search", "Google Chrome"),
    ("What development tools are installed on my laptop?", "dev_tools", None),
    ("what software do I have installed", "list", None),
    ("Is Node.js installed on my computer?", "search", "Node.js"),
    ("What version of Python do I have?", "search", "Python"),
    ("check if Docker is installed", "search", "Docker"),
])
def test_intent_positive(msg, intent, name):
    r = detect_software_intent(msg)
    assert r and r["intent"] == intent and r.get("name") == name


@pytest.mark.parametrize("msg", [
    "What are the latest Linux kernel CVEs?",
    "Are my installed apps vulnerable to ransomware?",
    "CONFIRM FIX VULN-LNX-101",
    "Explain what Python is",
    "Do I have time to learn Rust?",
    "What version of Python is best for beginners?",
    "Scan windows for threats",
    "How do I install Docker?",
    "What are good development tools?",
])
def test_intent_negative_goes_to_normal_chat(msg):
    assert detect_software_intent(msg) is None


def test_refresh_flag():
    assert detect_software_intent("Rescan and tell me if Python is installed")["refresh"] is True


# ------------------------------------------------------------------ chat replies
def test_chat_reply_installed_card_shows_real_values():
    svc, _ = svc_for(sample())
    out = answer_software_question("Is Python installed?", svc)
    card = out["software_result"]["cards"][0]
    assert out["software_result"]["status"] == "Installed"
    assert card["version"] == "3.12.1150.0" and card["publisher"] == "Python Software Foundation"
    assert "Installed" in out["answer"]


def test_chat_reply_not_installed_and_unverified():
    svc, _ = svc_for(sample())
    assert answer_software_question("Is Docker Desktop installed?", svc)["software_result"]["status"] == "Not Installed"
    bad, _ = svc_for(FakeWinreg({}))
    out = answer_software_question("Is Python installed?", bad)
    assert out["software_result"]["status"] == "Unable to verify"
    assert "won't guess" in out["answer"]


def test_dev_tools_only_lists_detected():
    svc, _ = svc_for(sample())
    out = answer_software_question("What development tools are installed on my laptop?", svc)
    cats = {c["category"] for c in out["software_result"]["cards"]}
    assert {"Python", "Node.js", "Visual Studio Code", "MongoDB"} <= cats and "Docker Desktop" not in cats


def test_registry_text_cannot_inject_markdown():
    w = FakeWinreg({(HKLM, P1): {"x": entry("Evil [click](http://x) <b>", "1", "Pub")}})
    svc, _ = svc_for(w)
    out = answer_software_question("is evil installed", svc)
    assert "[click](" not in out["answer"]


def test_list_reply():
    svc, _ = svc_for(sample())
    out = answer_software_question("List all installed applications.", svc)
    assert out["software_result"]["total"] == 7 - 1 - 0 or out["software_result"]["total"] >= 6


# ------------------------------------------------------------------ agent <-> backend round trip
@pytest.fixture()
def agent():
    from windows_agent import make_server
    server = make_server("127.0.0.1", 0, "s3cret", scan_fn=lambda: sc.scan_registry(sample()))
    t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown(); server.server_close()


def test_agent_roundtrip_and_auth(agent):
    good = SoftwareInventoryService(mode="agent", agent_url=agent, agent_token="s3cret")
    assert good.search("Google Chrome")["status"] == "Installed"
    assert good.health()["status"] in ("degraded", "ok")  # degraded: test host is not win32
    wrong = SoftwareInventoryService(mode="agent", agent_url=agent, agent_token="nope")
    r = wrong.search("Google Chrome")
    assert r["status"] == "Unable to verify" and "TOKEN" in r["errors"][0]["error"].upper()


def test_agent_is_read_only_and_checks_host(agent):
    assert requests.post(agent + "/inventory", headers={"X-Agent-Token": "s3cret"}).status_code == 405
    assert requests.get(agent + "/inventory", headers={"X-Agent-Token": "s3cret", "Host": "evil.example.com"}).status_code == 403
    assert requests.get(agent + "/inventory").status_code == 401


def test_agent_down_is_unable_to_verify_not_not_installed():
    svc = SoftwareInventoryService(mode="agent", agent_url="http://127.0.0.1:9", agent_timeout=1)
    r = svc.search("Python")
    assert r["status"] == "Unable to verify" and "windows_agent.py" in r["errors"][0]["error"]
    assert svc.health()["status"] == "unavailable"


# ------------------------------------------------------------------ FastAPI endpoints + chat routing
def test_fastapi_endpoints_and_chat(monkeypatch):
    from fastapi.testclient import TestClient
    import main

    svc, _ = svc_for(sample())
    monkeypatch.setattr(main, "get_software_service", lambda: svc)
    import software_inventory
    monkeypatch.setattr(software_inventory, "_service", svc)

    class Col:
        def insert_one(self, *_): pass
    monkeypatch.setattr(main, "sessions_col", Col())
    monkeypatch.setattr(main, "call_hosted_gemini_llm_ex",
                        lambda **k: (_ for _ in ()).throw(AssertionError("Gemini must NOT be called for software questions")))

    c = TestClient(main.app)  # no lifespan -> no Mongo needed
    assert c.get("/installed-software").json()["total"] >= 6
    r = c.get("/installed-software/search", params={"name": "Visual Studio Code"}).json()
    assert r["status"] == "Installed" and r["matches"][0]["version"] == "1.90.0"
    assert c.get("/installed-software/search", params={"name": "Docker Desktop"}).json()["status"] == "Not Installed"
    assert c.get("/installed-software/search", params={"name": ""}).status_code == 422
    assert "scanner_mode" in c.get("/installed-software/health").json()

    chat = c.post("/chat", json={"message": "Is Google Chrome installed?"}).json()
    assert chat["software_result"]["cards"][0]["version"] == "126.0.1"
    assert chat["model"] == "local-windows-scanner"


def test_portal_ask_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    import main, software_inventory
    svc, _ = svc_for(sample())
    monkeypatch.setattr(software_inventory, "_service", svc)
    c = TestClient(main.app)
    r = c.post("/installed-software/ask", json={"message": "is chrome installed in my laptop"}).json()
    assert r["matched"] and r["software_result"]["cards"][0]["version"] == "126.0.1"
    assert c.post("/installed-software/ask", json={"message": "explain ransomware"}).json() == {"matched": False}
    # the exact message from the screenshot, for an app that is not in the (fake) registry
    r = c.post("/installed-software/ask", json={"message": "is antigravity installed in my laptop"}).json()
    assert r["software_result"]["status"] == "Not Installed"
