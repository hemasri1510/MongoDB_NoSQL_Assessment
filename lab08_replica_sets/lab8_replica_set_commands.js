// ============================================================================
// LAB 8.1: Initialize 3-Node Replica Set & Verify Replication
// Run inside: docker exec -it mongo1 mongosh --port 27017
// ============================================================================

// 1. Initiate the 3-node replica set
rs.initiate({
  _id: "rs0",
  members: [
    { _id: 0, host: "mongo1:27017" },
    { _id: 1, host: "mongo2:27018" },
    { _id: 2, host: "mongo3:27019" }
  ]
});

// Wait ~5 seconds for initial election, then check status
rs.status();

// 2. Insert a document into the Primary
use cti_replicated;
db.threat_alerts.insertOne({
  alert_id: "CVE-2026-9910",
  severity: "CRITICAL",
  source: "ctidigest.com",
  replicated_at: new Date()
});

// 3. Verify on Secondaries (run on mongo2:27018 and mongo3:27019):
// db.getMongo().setReadPref("secondaryPreferred");
// use cti_replicated;
// db.threat_alerts.find().pretty();

// ============================================================================
// ACTIVITY 8.1: Add an Arbiter Node
// ============================================================================
rs.addArb("mongo-arbiter:27020");
rs.status().members.map(m => ({ name: m.name, stateStr: m.stateStr, health: m.health }));

// ============================================================================
// LAB 8.2 & ACTIVITY 8.2: Priority Configuration & Forced Failover
// ============================================================================

// Configure different priorities so mongo2 (priority 5) is favored over mongo1 (2) & mongo3 (1)
let cfg = rs.conf();
cfg.members[0].priority = 2; // mongo1:27017
cfg.members[1].priority = 5; // mongo2:27018 -> Predicted winner on stepDown!
cfg.members[2].priority = 1; // mongo3:27019
rs.reconfig(cfg);

// Force primary to step down for 60 seconds and watch election
rs.stepDown(60);

// Verify the new primary (should be mongo2:27018)
rs.status().members.map(m => ({ name: m.name, stateStr: m.stateStr, health: m.health }));
