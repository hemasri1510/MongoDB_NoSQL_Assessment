// LAB 7.1 & LAB 7.2 MongoDB Shell Script
use cti_platform;

db.threat_sources.insertMany([
  { _id: ObjectId("6740a1b2c3d4e5f600000011"), name: "CISA Advisories & CERT-EU", domain: "cisa.gov", reliability_tier: "A1", is_confirmed_threat: true }
]);

db.digest_articles.insertMany([
  {
    _id: ObjectId("6740a1b2c3d4e5f600000101"),
    title: "APT29 Exploits Cloud Identity Tokens in European Diplomacy Campaign",
    published_at: ISODate("2026-11-20T08:00:00Z"),
    author_summary: { name: "CTI Analyst Desk", role: "Senior Threat Researcher" },
    mitre_tactics: ["TA0006 - Credential Access", "TA0001 - Initial Access"],
    tags: ["APT29", "OAuth", "Phishing", "Cloud"],
    source_id: ObjectId("6740a1b2c3d4e5f600000011"),
    ioc_ids: [ObjectId("6740a1b2c3d4e5f600000901")]
  }
]);

db.digest_articles.aggregate([
  { $project: { title: 1, docSizeBytes: { $bsonSize: "$$ROOT" } } },
  { $group: { _id: null, avgSizeBytes: { $avg: "$docSizeBytes" }, maxSizeBytes: { $max: "$docSizeBytes" } } },
  { $project: { _id: 0, avgSizeKB: { $round: [{ $divide: ["$avgSizeBytes", 1024] }, 2] }, maxSizeKB: { $round: [{ $divide: ["$maxSizeBytes", 1024] }, 2] } } }
]);

use cti_working_set;
const bulk = db.security_reports.initializeUnorderedBulkOp();
const padding = "X".repeat(1850);
for (let i = 0; i < 100000; i++) {
  bulk.insert({ report_id: `ASR-2026-${i}`, title: `Annual Threat Landscape Report #${i}`, payload: padding });
}
bulk.execute();
