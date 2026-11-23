// Lab 12: Custom RBAC Roles, Least-Privilege Users & PII-Redacted Views
use admin;

db.createRole({
  role: "ctiAnalystReadOnly",
  privileges: [
    { resource: { db: "cti_prod", collection: "sanitized_users_view" }, actions: ["find"] }
  ],
  roles: []
});

db.createUser({
  user: "analyst_alice",
  pwd: passwordPrompt(),
  roles: [{ role: "ctiAnalystReadOnly", db: "admin" }]
});

use cti_prod;
db.createView("sanitized_users_view", "users", [
  {
    $project: {
      username: 1,
      department: 1,
      role: 1,
      email_masked: {
        $concat: [{ $substrCP: ["$email", 0, 2] }, "***@***"]
      }
    }
  }
]);
