# Project Instructions

## Long-running scripts

When asked to run a long-running script (simulations, training, alignment, benchmarks, etc.):

1. Run it with `nohup` so it survives terminal disconnects, redirecting output to a log file
2. Log the run to `script_runs.txt` in the project root with:
   - Timestamp
   - Script name and parameters
   - Any notable changes made before this run (e.g. code fixes, config tweaks)
3. Example log entry:
   ```
   2026-03-13 14:30 | align_trojkad.py | rewrote patterns.md to stabilize intent
   ```
4. Example nohup invocation:
   ```
   nohup /home/predator/repo/preferans/.venv/bin/python3 script.py > script_output.log 2>&1 &
   ```
