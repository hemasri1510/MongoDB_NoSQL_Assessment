"""
Final Assessment (A.2): Threat Intelligence Embedding & Ingestion Pipeline
Ingests Anticipated, Active, Evolving, and Resolved threats across Linux, Windows,
macOS, Android, and iOS into MongoDB `knowledge` collection with vector embeddings.
"""

from __future__ import annotations

import hashlib
import math
import os
from datetime import datetime, timezone
from typing import List, Dict, Any

from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "threat_intel_chatbot")
VOYAGE_API_KEY = os.getenv("VOYAGE_API_KEY", "").strip()
EMBEDDING_DIM = 384

THREAT_INTELLIGENCE_CORPUS: List[Dict[str, Any]] = [
    {
        "doc_id": "CTI-LINUX-001",
        "title": "CVE-2026-21409: Linux Kernel io_uring Privilege Escalation",
        "platform": "Linux",
        "lifecycle": "Active",
        "severity": "Critical",
        "cvss": 9.8,
        "affected": "Linux Kernel 6.1.x - 6.8.4 (Ubuntu 22.04/24.04, RHEL 9)",
        "indicators": ["unexpected io_uring_setup syscalls", "unprivileged uid=0 child spawn", "SHA256:9f8a21c4e701..."],
        "mitigation_cmd": "sudo apt-get update && sudo apt-get install --only-upgrade linux-image-generic && sudo sysctl -w kernel.io_uring_disabled=2",
        "text": (
            "Active Critical Linux kernel vulnerability in io_uring subsystem allowing local unprivileged attackers "
            "to achieve root privilege escalation via use-after-free ring buffer race condition. Actively exploited "
            "in cloud container escapes. Immediate mitigation: disable unprivileged io_uring via sysctl "
            "kernel.io_uring_disabled=2 and upgrade kernel packages immediately."
        ),
    },
    {
        "doc_id": "CTI-LINUX-002",
        "title": "OpenSSH Pre-Auth Signal Handler Race Condition (RegreSSHion Variant)",
        "platform": "Linux",
        "lifecycle": "Resolved",
        "severity": "High",
        "cvss": 8.1,
        "affected": "OpenSSH 8.5p1 through 9.7p1 on glibc-based Linux distributions",
        "indicators": ["sshd SIGALRM timeout spikes", "repeated unauthenticated connection resets on port 22"],
        "mitigation_cmd": "sudo apt-get install --only-upgrade openssh-server && echo 'LoginGraceTime 0' | sudo tee -a /etc/ssh/sshd_config && sudo systemctl reload sshd",
        "text": (
            "Resolved remote unauthenticated code execution vulnerability in OpenSSH server (sshd) triggered by "
            "asynchronous signal handler race during LoginGraceTime expiry. Resolved in OpenSSH 9.8p1+. Mitigation: "
            "upgrade openssh-server and enforce LoginGraceTime 0 or restrict SSH access via nftables/ufw."
        ),
    },
    {
        "doc_id": "CTI-WIN-001",
        "title": "CVE-2026-24812: Windows SMBv3 Compression & NTLM Relay Chain",
        "platform": "Windows",
        "lifecycle": "Active",
        "severity": "Critical",
        "cvss": 9.6,
        "affected": "Windows Server 2019/2022, Windows 11 23H2/24H2",
        "indicators": ["TCP/445 outbound to untrusted IPs", "EventID 4624 NTLM Type 3 anomalous logon", "svchost.exe memory spike"],
        "mitigation_cmd": "Set-SmbServerConfiguration -EnableSMB1Protocol $false -RequireSecuritySignature $true -Force; Disable-NetAdapterBinding -Name '*' -ComponentID ms_server",
        "text": (
            "Active exploitation campaign chaining Windows SMBv3 memory corruption with forced NTLM authentication relay "
            "to compromise Domain Controllers. Mitigation procedure: enforce SMB signing (RequireSecuritySignature), "
            "block outbound TCP port 445 at perimeter firewalls, and apply the latest cumulative Windows Security Update."
        ),
    },
    {
        "doc_id": "CTI-WIN-002",
        "title": "ClickFix / PowerShell Encoded Loader & Living-off-the-Land Abuse",
        "platform": "Windows",
        "lifecycle": "Evolving",
        "severity": "High",
        "cvss": 8.4,
        "affected": "Windows 10 & Windows 11 Enterprise Endpoints",
        "indicators": ["powershell.exe -EncodedCommand", "mshta.exe spawning curl/bitsadmin", "RunMRU registry modification"],
        "mitigation_cmd": "Set-ExecutionPolicy AllSigned -Scope LocalMachine -Force; Set-MpPreference -AttackSurfaceReductionRules_Ids D4F940AB-401B-4EFC-AADC-AD5F3C50688A -AttackSurfaceReductionRules_Actions Enabled",
        "text": (
            "Evolving social-engineering and endpoint execution technique tricking Windows users into executing "
            "obfuscated PowerShell payloads via Win+R Run dialog. Continuously morphing payload staging via mshta.exe "
            "and WMI. Mitigation: enforce PowerShell Constrained Language Mode, AllSigned execution policy, and Defender ASR rules."
        ),
    },
    {
        "doc_id": "CTI-MAC-001",
        "title": "macOS TCC Bypass & Gatekeeper Quarantine Attribute Stripping",
        "platform": "macOS",
        "lifecycle": "Evolving",
        "severity": "High",
        "cvss": 8.2,
        "affected": "macOS Sonoma 14.x & Sequoia 15.0 - 15.2",
        "indicators": ["xattr -d com.apple.quarantine in shell history", "unauthorized TCC.db access attempts", "unsigned Mach-O binary in /tmp"],
        "mitigation_cmd": "sudo spctl --master-enable && sudo softwareupdate --install --recommended",
        "text": (
            "Evolving macOS infostealer and post-exploitation technique abusing AppleScript and Finder automation to "
            "bypass Transparency, Consent, and Control (TCC) prompts and strip com.apple.quarantine attributes. "
            "Mitigation: ensure Gatekeeper (spctl) and XProtect are active, restrict Terminal Full Disk Access, and update macOS."
        ),
    },
    {
        "doc_id": "CTI-AND-001",
        "title": "Android Accessibility Service Overlay & Zygote Injection Trojan",
        "platform": "Android",
        "lifecycle": "Active",
        "severity": "High",
        "cvss": 8.5,
        "affected": "Android 12, 13, 14, and 15 (sideloaded APKs outside Google Play)",
        "indicators": ["BIND_ACCESSIBILITY_SERVICE requested by non-assistive APK", "SYSTEM_ALERT_WINDOW overlay abuse"],
        "mitigation_cmd": "adb shell settings put secure enabled_accessibility_services '' && pm disable-user --user 0 <malicious.pkg>",
        "text": (
            "Active Android banking and credential harvesting malware distributed via fake browser/PDF updater APKs "
            "abusing Accessibility Services to capture 2FA codes and inject overlays. Mitigation: enable Google Play "
            "Protect, revoke Accessibility permissions for sideloaded apps, and restrict Unknown Sources installation."
        ),
    },
    {
        "doc_id": "CTI-IOS-001",
        "title": "iOS WebKit & ImageIO Zero-Click Memory Corruption Vector",
        "platform": "iOS",
        "lifecycle": "Anticipated",
        "severity": "Critical",
        "cvss": 9.3,
        "affected": "iOS 17.x and iOS 18.0 - 18.2 prior to rapid security response",
        "indicators": ["unexpected WebContent crash logs", "malformed WebP/HEIC attachment parsing"],
        "mitigation_cmd": "Enable Lockdown Mode (Settings > Privacy & Security > Lockdown Mode) and install iOS Rapid Security Response update",
        "text": (
            "Anticipated high-impact zero-click exploitation vector targeting WebKit JIT and ImageIO parser routines "
            "via crafted messaging attachments. High likelihood of targeted mercenary spyware adoption. Mitigation: "
            "enable iOS Lockdown Mode for high-risk profiles and apply Rapid Security Response patches immediately."
        ),
    },
    {
        "doc_id": "CTI-MULTI-001",
        "title": "Anticipated Post-Quantum Harvest-Now-Decrypt-Later TLS downgrade & AI-Automated API Fuzzing",
        "platform": "Linux",
        "lifecycle": "Anticipated",
        "severity": "Medium",
        "cvss": 7.4,
        "affected": "Linux, Windows, macOS public TLS 1.2 endpoints & unauthenticated REST/GraphQL APIs",
        "indicators": ["TLS 1.2 RSA key-exchange handshakes", "high-entropy automated API schema enumeration"],
        "mitigation_cmd": "Enforce TLS 1.3 with X25519MLKEM768 hybrid key exchange and per-IP token bucket rate limiting",
        "text": (
            "Anticipated threat trend combining automated LLM-assisted API vulnerability discovery with long-term "
            "capture of classical RSA/ECDH encrypted traffic. Mitigation: enforce TLS 1.3 with post-quantum hybrid "
            "key exchange, mTLS for internal microservices, and strict API rate limiting."
        ),
    },
]


def deterministic_embedding(text: str, dim: int = EMBEDDING_DIM) -> List[float]:
    """Generates a normalized 384-D semantic-hash embedding when Voyage API key is absent."""
    vec = [0.0] * dim
    tokens = [t.strip(".,:;()[]\"'").lower() for t in text.split() if t.strip()]
    if not tokens:
        return vec
    for tok in tokens:
        digest = hashlib.sha256(tok.encode("utf-8")).digest()
        for i in range(0, len(digest), 2):
            idx = ((digest[i] << 8) | digest[i + 1]) % dim
            sign = 1.0 if (digest[i] & 1) == 0 else -1.0
            vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


def get_embeddings(texts: List[str]) -> List[List[float]]:
    if VOYAGE_API_KEY:
        try:
            from voyageai import Client as VoyageClient
            vc = VoyageClient(api_key=VOYAGE_API_KEY)
            return vc.embed(texts, model="voyage-3").embeddings
        except Exception as exc:
            print(f"[WARN] Voyage API fallback to deterministic embeddings: {exc}")
    return [deterministic_embedding(t) for t in texts]


def chunk_text(text: str, chunk_size: int = 500) -> List[str]:
    return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)] or [text]


def ingest_corpus() -> int:
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    knowledge = db["knowledge"]
    threats = db["threats"]

    knowledge.create_index([("doc_id", ASCENDING), ("chunk_index", ASCENDING)], unique=True)
    knowledge.create_index([("platform", ASCENDING), ("lifecycle", ASCENDING)])
    threats.create_index([("doc_id", ASCENDING)], unique=True)
    threats.create_index([("platform", ASCENDING), ("lifecycle", ASCENDING), ("severity", ASCENDING)])

    total_chunks = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    for item in THREAT_INTELLIGENCE_CORPUS:
        threats.update_one(
            {"doc_id": item["doc_id"]},
            {
                "$set": {
                    **item,
                    "updated_at": now_iso,
                }
            },
            upsert=True,
        )
        full_doc_text = (
            f"[{item['lifecycle']} | {item['platform']} | {item['severity']} (CVSS {item['cvss']})] "
            f"{item['title']}. Affected: {item['affected']}. {item['text']} "
            f"Mitigation Command: {item['mitigation_cmd']}"
        )
        chunks = chunk_text(full_doc_text, chunk_size=500)
        embeddings = get_embeddings(chunks)
        for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
            knowledge.update_one(
                {"doc_id": item["doc_id"], "chunk_index": idx},
                {
                    "$set": {
                        "doc_id": item["doc_id"],
                        "title": item["title"],
                        "platform": item["platform"],
                        "lifecycle": item["lifecycle"],
                        "severity": item["severity"],
                        "cvss": item["cvss"],
                        "mitigation_cmd": item["mitigation_cmd"],
                        "chunk_index": idx,
                        "text": chunk,
                        "embedding": emb,
                        "updated_at": now_iso,
                    }
                },
                upsert=True,
            )
            total_chunks += 1

    print(f"Ingested {len(THREAT_INTELLIGENCE_CORPUS)} threat advisories ({total_chunks} vector chunks) into '{MONGO_DB}'.")
    return total_chunks


if __name__ == "__main__":
    ingest_corpus()
