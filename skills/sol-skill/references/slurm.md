# Slurm - SBATCH Job Scripts

## Overview

SBATCH job scripts are the standard way to run work on the
supercomputer. In these scripts you request a resource allocation and
define the work to be done. ASU uses the **Slurm** resource manager,
so all job scripts must be in the SBATCH format.

An SBATCH script is a Bash script with special `#SBATCH` headers that
Slurm interprets before executing the job on compute hardware.

> **Interactive vs batch.** This reference covers **batch** jobs
> (`sbatch`) and status commands. For **interactive** allocations, drive
> the `solx job` lifecycle (`start` / `list` / `time` / `jump` / `stop`)
> - see [solx.md](solx.md). `solx` deliberately doesn't wrap `sbatch`;
> for batch work, use the SBATCH path below directly.

## Submitting Jobs

```shell
sbatch myscript.sh
```

### Overriding Headers at Submission Time

```shell
sbatch -q debug -t 15 -c 16 bigjob.sh
```

This submits `bigjob.sh` to the `debug` QOS with a 15-minute time
limit and 16 CPU cores, overriding any `#SBATCH` headers inside the
script.

## Canceling a Job

Find your job ID with `myjobs`, then cancel it:

```shell
myjobs
scancel <jobID>
```

## Updating a Pending Job

Modify resources of a **pending** job with `scontrol`:

```shell
scontrol update job <jobID> ReqCores=4
```

More examples:

```shell
scontrol update job 11254871 QOS=private Partition=htc
scontrol update job 11254871 Gres=gpu:a100:2
```

## Example SBATCH Scripts

Additional templates live on the cluster at:

- `/packages/public/sol-sbatch-templates/templates/`
- `/packages/public/phx-sbatch-templates/templates/`

### Simple (Serial) Job

```shell
#!/bin/bash

#SBATCH -N 1            # number of nodes
#SBATCH -c 8            # number of cores
#SBATCH -t 0-01:00:00   # time in d-hh:mm:ss
#SBATCH -p public       # partition
#SBATCH -q public       # QOS
#SBATCH -o slurm.%j.out # STDOUT (%j = JobId)
#SBATCH -e slurm.%j.err # STDERR (%j = JobId)
#SBATCH --mail-type=ALL
#SBATCH --mail-user="%u@asu.edu"
#SBATCH --export=NONE   # purge the submitting shell environment

module load mamba/latest
source activate myEnv

cd ~/myResearchDir/test01
python myscript.py
```

### MPI (Parallel) Job

The main difference from a serial job is that parallel jobs allocate
tasks (`-n`) in addition to cores-per-task (`-c`). MPI processes bind
to tasks across one or more nodes.

```shell
#!/bin/bash

#SBATCH -N 3
#SBATCH -n 8            # number of tasks
#SBATCH -c 1            # cores per task (default 1)
#SBATCH -t 0-01:00:00
#SBATCH -p public
#SBATCH -q public
#SBATCH -o slurm.%j.out
#SBATCH -e slurm.%j.err
#SBATCH --mail-type=ALL
#SBATCH --mail-user="%u@asu.edu"
#SBATCH --export=NONE

module load openmpi/4.1.5
module load lammps
srun -n 8 --mpi=pmix software
```

### Job Arrays

Job arrays submit a collection of similar jobs as a single entity -
useful when running the same computation with different input
parameters. A **manifest file** lists the input parameters.

```shell
#!/bin/bash

#SBATCH -N 1
#SBATCH -c 1
#SBATCH --array=1-20
#SBATCH -t 0-01:00:00
#SBATCH -p public
#SBATCH -q public
#SBATCH -o slurm.%j.out
#SBATCH -e slurm.%j.err
#SBATCH --mail-type=ALL
#SBATCH --mail-user="%u@asu.edu"
#SBATCH --export=NONE

readonly manifest="/path/to/manifest_file"
readonly run_opts=($(getline $SLURM_ARRAY_TASK_ID "$manifest"))

module load mamba/latest
source activate myEnv

python myscript.py "${run_opts[@]}"
```

Manifest file (one input path per line):

```shell
/path/to/inputData0001.csv
/path/to/inputData0002.csv
/path/to/inputData0020.csv
```

## Troubleshooting

### DOS Line Breaks

If you created the script on Windows you may see:

```shell
sbatch: error: Batch script contains DOS line breaks (\r\n)
```

Fix with `dos2unix`:

```shell
dos2unix myjob.sh
```

### Missing Shell-Bang Line

```shell
sbatch: error: This does not look like a batch script.
```

Ensure the very first line of every SBATCH script is:

```shell
#!/bin/bash
```

### Invalid Feature Specification

```shell
sbatch: error: Batch job submission failed: Invalid feature specification
```

Check that your script uses the correct partition and QOS for the
target supercomputer.
See [Sol Partitions and QOS](https://asurc.atlassian.net/wiki/spaces/RC/pages/1640103937).

## Slurm Exit Codes

| Code | Meaning |
|-----------|------------------------------------------------|
| 0 | Success |
| 1 | General failure |
| 2 | Incorrect use of shell builtins |
| 3–124 | Application-specific error (check software docs) |
| 125 | Out of memory |
| 126 | Command cannot execute |
| 127 | Command not found |
| 128 | Invalid argument to `exit` |
| 129–192 | Terminated by Linux signal (subtract 128) |

Run `kill -l` to list signal codes, or `man signal` for details.

> **Note:** running `sleep` commands in SBATCH jobs violates
> ASU policy and the job will be purged.

## Situation-aware job management

The load-bearing rules are in SKILL.md ("Situation-Aware Job
Management"); this is the backing detail.

### When a job is pending

The load-bearing routine is in SKILL.md ("When a job is PENDING -
diagnose cause + ETA"); this is the backing detail. Two commands get
cause + ETA, both parseable (no color):

```shell
squeue --me -t PD -O "JobID,Reason:50,StartTime"   # full reason + estimated start
scontrol show job <jobid>                          # all fields for one job (Reason=..., StartTime=...)
#   Reason=Resources  StartTime=2026-06-18T16:56:36   ← next in line; backfill estimate
#   Reason=Priority   StartTime=Unknown               ← deep in the queue; no estimate yet
```

Widen `Reason` (`:50`) so a multi-word reason
(`ReqNodeNotAvail, UnavailableNodes:sc013`) isn't truncated - a plain
`grep 'Reason=[^ ]+'` stops at the first space.

`StartTime` is the scheduler's *current* estimate - it moves as the
queue changes, and `Unknown` means it can't estimate one yet. The
`Reason` classes you'll actually see, and what each implies:

| `Reason` | Class | Implication |
|---|---|---|
| `Priority` | priority-bound | Other jobs outrank yours (usually low fairshare). No partition change beats a per-user priority cap - report the ETA and wait. |
| `ReqNodeNotAvail, UnavailableNodes:<n>` | node unavailable | A required node is **drained/down** (the common `UnavailableNodes:` form - may need an admin, no predictable clear time) or held by a reservation. SLURM uses a separate `Reservation` reason for waiting on an advanced reservation. Reroute to a partition whose nodes are healthy. |
| `Resources` | capacity-bound | The QOS/partition is full right now (usually still carries a backfill `StartTime` estimate). A reroute to a partition with free nodes for a QOS you hold *can* help; so can right-sizing. |
| `QOSMaxJobsPerUserLimit` / other `QOSMax...` | QOS limit | You're at a per-QOS cap (e.g. `debug` allows one job at a time). Wait for the running one, or pick a QOS you're not capped on. |
| `AssocGrp...` (e.g. `AssocGrpGRES` for a group GPU cap) | group limit | Your group's allocation cap is hit; another partition under the same account won't help. |

Don't cancel-and-resubmit to chase a slot - the resubmit inherits the
same priority and spends more fairshare. If you do test a reroute,
validate it and compare estimated starts *before* cancelling the
original, and only cancel the loser:

```shell
scontrol show job <jobid> | grep -o 'StartTime=[^ ]*'   # current estimate
sbatch --test-only other.sh                             # validates + prints an estimated start, submits nothing
```

### Fairshare

`myfairshare` prints a table; the 0–1 score to read is the rightmost
**`RealFairShare`** column (the dampened priority value - the wrapper
reads the current `DampeningFactor`, so prefer it over hand-computing
from `sshare`).
Heavy recent usage drives the score down, which makes your jobs queue
behind everyone else's - a long pending time is usually fairshare, not a
stuck cluster.

**Below ~0.05 is bad** - treat the account as throttled:

- Don't spam the scheduler. A pile of submissions, or auto-resubmitting
  on every failure, burns more fairshare and makes the next job queue
  even longer. Submit fewer, right-sized jobs and let them run.
- Right-size requests: don't take `public`/GPU/large nodes for work that
  fits on `htc`; over-asking costs more fairshare per job.
- Don't cancel-and-resubmit a pending job to chase a better slot - the
  new job inherits the same priority; you've only spent fairshare.
- Surface the number to the user instead of quietly firing more work.

### Remaining time on the current allocation

Inside an allocation you're on borrowed wall-time - when it expires
Slurm kills the job and anything not on durable storage is lost. Check
what's left:

```shell
solx job time                          # remaining wall-time (e.g. 2:14:09; D-HH:MM:SS once over a day)
squeue -h -j "$SLURM_JOB_ID" -o %L     # no-solx equivalent (TimeLeft, no padding)
scontrol show job "$SLURM_JOB_ID" | grep -o 'TimeLimit=[^ ]*'   # the cap
```

- **Don't start what can't finish** in the remaining window - chunk it
  with checkpoints, or request more time up front (a fresh
  `solx job start` with a longer `time`, or a batch job).
- **Wrap up early:** as time gets short, stop starting work and flush
  results/outputs to `/scratch`, checkpoint state, and write a short
  status summary. `/home` and `/scratch` survive the allocation; the
  node's local state does not.
- **Hand off:** leave a resume note or small script (what ran, what's
  left, the command to continue) so the next session or a follow-up
  batch job resumes cleanly.

## Helpful Sol commands

ASU ships convenience wrappers on top of Slurm
(<https://docs.rc.asu.edu/helpful-slurm-commands>). Prefer the native
Slurm command when it's just as short; reach for a wrapper when its
formatting or calculation earns it. Call these **directly** - `solx`
doesn't (and shouldn't) wrap them.

**Audience matters for an agent.** The color-coded wrappers (`showparts`,
`showgpus`, `myfairshare`) are built for human eyes and fight `awk`/`grep` -
use them to *show a user*. For anything you'll **parse**, prefer the
SLURM-native or `--json` form (e.g. GPU types per partition:
`sinfo -h -o "%P %t %G"`, with `GresUsed` for the free count - `%G`
alone is *configured*, not free). The audience-tagged table is in
SKILL.md ("Asking the Cluster About Yourself and Your Jobs").

| Command | What it does |
|---|---|
| `myjobs` | Your current jobs in the queue (priority/QOS/GPU columns). |
| `summary` | Per-state counts of your jobs (RUNNING / PENDING / ...). |
| `sq` | The queue; `sq -u $USER` filters to you (a `squeue` wrapper). |
| `thisjob <jobid>` | Job info including the estimated start time. |
| `seff <jobid>` | Slurm efficiency (CPU + memory used) for a finished job. |
| `myfairshare` | Your real fairshare score. |
| `beegfs-ctl --getquota --uid $USER` | Your `/scratch` (BeeGFS) quota - there is no `myquota` wrapper. |
| `sinfo` / `showparts` | Cluster / partition capacity (`showparts` is color-coded). |
| `showgpus` | Free GPUs per node (color-coded). |
| `ns` | Command-line version of the cluster status page. |
| `mysacct` | Historical job activity (a preset `sacct` format). |
| `myaccounts` | Accounts and QOS you can submit under. |
| `showlimited` | Cluster-wide capacity holds (why jobs are stuck pending). |

Verify against the upstream page if a wrapper behaves unexpectedly - ASU
maintains them and the set changes over time.
