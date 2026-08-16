# Run recovery and durable tasks

Every domain mapping unit is stored in SQLite before execution. A worker claims one task with a
lease, performs provider collection and evidence projection, marks the domain processed only after
all work is persisted, and then marks the task succeeded.

If ReconRelate or the machine stops during a run, restart with the same root domain:

```powershell
reconrelate run example.com --resume
```

Resume accepts both explicitly interrupted runs and crash-abandoned `running` runs. It requeues
unfinished leased tasks immediately, skips completed domains, and keeps the original run ID. Task
enqueue uses a run-scoped idempotency key, while observations, claims, graph edges, and pivot
decisions tolerate redelivery without duplication.

Inspect recovery state through either report format:

```powershell
reconrelate report <run-id>
reconrelate report <run-id> --format json
```

The `task_summary` contains `pending`, `in_progress`, `succeeded`, and `failed` counts. A run that
stops because work remains is `partial`; exhausted tasks or provider failures produce
`completed_degraded`. Preserve the database and run `reconrelate db check` before manual recovery if
task state looks inconsistent.

Tasks have bounded attempts. Expired leases below the attempt limit return to pending; an expired
lease at the limit becomes failed. Do not directly edit `run_tasks` in a production database.
