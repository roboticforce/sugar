---
name: sugar-status
description: Show Sugar system health and queue metrics
---

# Sugar System Status

Display comprehensive Sugar system status including:

- Queue statistics (pending, active, completed, failed counts)
- Worker status (running, stopped)
- Last execution time
- Configuration summary

## Presentation

Format the status as a dashboard:

```
Sugar System Status
-------------------
Queue:     45 total (20 pending, 2 active, 22 completed, 1 failed)
Worker:    Running
Last Run:  5 minutes ago
Config:    .sugar/config.yaml

Health: Healthy / Warning / Alert
```

## Health Assessment

- **Healthy**: Tasks executing, low failure rate
- **Warning**: Growing backlog, worker stopped
- **Alert**: Multiple failures, configuration issues

## Recommendations

Based on status, suggest:

- Start worker if stopped: `/sugar-run`
- Review failures: `/sugar-list --status failed`
- Check task priorities if backlog growing
