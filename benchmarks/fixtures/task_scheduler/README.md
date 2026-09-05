# Task scheduler fixture

This small standard-library project parses timezone-aware start times, applies
scheduling policies, stores tasks, and exposes a command-line entry point.

Run the tests:

```powershell
python -m unittest discover -s tests -v
```

Create a task:

```powershell
python -m task_scheduler.cli "Team sync" "2030-01-02T09:00:00-08:00" 30
```
