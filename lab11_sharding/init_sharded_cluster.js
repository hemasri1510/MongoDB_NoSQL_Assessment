// Lab 11: Configure Sharded Cluster with Hashed & Compound Zone Shard Keys
sh.addShard("shard1ReplSet/shard1a:27018,shard1b:27018");
sh.addShard("shard2ReplSet/shard2a:27018,shard2b:27018");

sh.enableSharding("iot_telemetry");

// Hashed shard key to eliminate monotonic timestamp/ObjectId hotspots
sh.shardCollection("iot_telemetry.readings", { device_id: "hashed" });

// Compound shard key for range-localized multi-tenant queries
sh.shardCollection("iot_telemetry.tenant_logs", { tenant_id: 1, bucket_ts: 1 });

sh.status();
