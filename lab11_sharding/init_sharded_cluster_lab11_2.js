// ============================================================================
// LAB 11.2: Initialize Sharded Cluster (Config Server, Shard A, Shard B, Mongos)
// ============================================================================
// Step 1: Run on configsvr (docker exec -it configsvr mongosh --port 27019):
// rs.initiate({ _id: "configReplSet", configsvr: true, members: [{ _id: 0, host: "configsvr:27019" }] });

// Step 2: Run on shardA (docker exec -it shardA mongosh --port 27018):
// rs.initiate({ _id: "shardAReplSet", members: [{ _id: 0, host: "shardA:27018" }] });

// Step 3: Run on shardB (docker exec -it shardB mongosh --port 27020):
// rs.initiate({ _id: "shardBReplSet", members: [{ _id: 0, host: "shardB:27020" }] });

// Step 4: Run on mongos router (docker exec -it mongos mongosh --port 27017):
sh.addShard("shardAReplSet/shardA:27018");
sh.addShard("shardBReplSet/shardB:27020");

// Enable sharding on target database
sh.enableSharding("cti_sharding_lab");

// Create indexes & shard both collections
use cti_sharding_lab;

// 1. Monotonically increasing timestamp shard key (creates write hotspot)
db.hotspot_data.createIndex({ ts: 1 });
sh.shardCollection("cti_sharding_lab.hotspot_data", { ts: 1 });

// 2. Hashed shard key (distributes writes evenly across Shard A & Shard B)
db.even_data.createIndex({ user_id: "hashed" });
sh.shardCollection("cti_sharding_lab.even_data", { user_id: "hashed" });

sh.status();
