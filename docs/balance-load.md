# Balance Load, Affinity Rules and automatic balancing

How PyXie keeps a Proxmox cluster even, and how to stay in control of it. Everything here goes through the normal dry-run, approve, execute path
with the same hard checks (affinity rules, memory headroom, CPU compatibility, HA, maintenance mode) and the global write switch.

## Balance Load

Operations > **Balance Load** shows the cluster balance gauge and each node's memory and CPU, then lets you **Preview Balance Load**. Nothing moves until
you approve.

- **Cluster balance** is 100 minus how far node load strays from the cluster average, counting each node in proportion to its memory size (memory always,
  CPU once the busiest node passes 50%). 80 and above is Even, 60 to 79 "A little uneven", below 60 Skewed.
- **Memory by node** shows each node before (grey) and after (coloured) the plan, in percent and GB, and which guests leave and arrive.
- **Planned moves** are the moves in the plan, each checked on its own. Below them, one line summarises everything that was considered but not made:
  **No gain** (it would only trade places or not narrow the gap between two nodes by at least 5 points), **Blocked** (a rule refused it: affinity, headroom,
  CPU compatibility, HA, trust tier) and **Redirected** (the first choice was refused, another node was used). **Show all** opens the full list.
- **Left alone on purpose** lists guests that are pinned to their host, marked Do not move, moved recently, or already moved twice this week.
- You can change any destination in the plan card, set a line to Don't move, or **Re-score** (it re-checks each move's destination only). To re-read the
  whole cluster use **Replace the plan below with a new preview**. While automatic balancing is on, a plan nobody approves for 12 hours is cancelled and replaced at its next run.

### Why did nothing move?

A cluster can read "A little uneven" and still have nothing worth moving: a small node at a low percentage cannot take a large guest without ending fuller
than the node it came from. The refused list says why (for example "it would leave st-pve103 at 82% memory, fuller than st-pve101 is now").

## Affinity Rules

Operations > **Affinity & Pinning**: keep-apart and keep-together rules for guest pairs or for every guest carrying a tag, with the current status of each
(Holding, Broken, Not met, Nothing to check). Guests are shown as coloured pills that link to the guest; a **hard** rule that is broken is red, a **soft**
preference that is not met is amber. Balance Load reviews the rules on every run and plans the moves that fix any that are not met, before it balances load (a person approves the plan unless automatic approval is on); the rules are also checked on every new move.

## Pinned host and Do not move

On a workload's page, **Pinned host (soft)** keeps balancing from moving the guest away from that host (it can still move toward it, and Maintenance can
still move it). **Do not move** keeps Balance Load and automatic balancing from moving it at all. Both show on the Balance Load page.

## Automatic balancing

The card at the bottom of Balance Load, per cluster:

| Mode | What it does |
| --- | --- |
| Off | Nothing. |
| Recommend only | Prepares a Balance Load plan when the cluster drifts out of balance and waits for you to approve it. |
| Auto-approve | Approves and runs the plan itself, **one move at a time**. Off by default; turning it on needs an explicit confirmation (Aggressive needs a second one), asked again every time. |

| Level | Acts below score | Minimum benefit per move | Moves per plan (recommend) | Gap between plans | A moved guest rests |
| --- | --- | --- | --- | --- | --- |
| Conservative | 60 | 25 | 2 | 6 h | 7 days |
| Moderate | 70 | 15 | 4 | 2 h | 24 h |
| Aggressive | 80 | 10 | 8 | 30 min | 6 h |

You can also choose what to balance (most limited resource, memory, CPU, both), which nodes guests may be taken from, and the days and hours it may
act. It waits when the cluster is paused, outside the window, not quorate, or a plan is already waiting or running.

**How it decides.** It scores the cluster by each node's *busy* level over the last 7 days (the 90th percentile of its load), not by right now, so a server
that is only busy in business hours never looks idle at night. Auto-approve needs 24 hours of history on every node. It never moves a guest back to a host it
left in the last 72 hours, leaves a guest alone once it has moved twice in a week, and ignores moves made by maintenance mode when it counts.

**Auto-approve leaves a plan for you** (the reason is on the card, in the audit log and in the notification) when the write switch is off, a move is not a live
migration or also changes storage, any node would end above 80% memory, or a node is locked by another operation. Conservative also never plans guests whose
downtime tolerance is Low.

**If something goes wrong:** the first failed or blocked automatic move stops it, sends a critical notification and switches the cluster back to Recommend only.

**Stopping it:** **Revoke auto-approve** (red button on the card) switches the cluster back to Recommend only immediately and drops the confirmation; a move
that has not started is cancelled, a migration already running finishes. **Pause for 24 h** and the global write switch (Settings) also stop it.

**Notifications.** Add a rule in Settings > Notifications for the category **Automatic balancing** with minimum severity **Warning** to be emailed when a plan is
ready, auto-approved, or revoked; a stop after a failure is critical. Audit events: `balance.auto_settings_changed`, `balance.auto_plan_created`,
`balance.auto_plan_approved`, `balance.auto_stopped`, `balance.auto_revoked`.

The design notes and the reasoning are in [auto-balance-design.md](auto-balance-design.md).

**Rule colors.** Each affinity rule has a color that paints its line and its guest pills (here and in the "where the guests sit" card). Pick one of the swatches, or any color, when creating or editing a rule; "Default" goes back to the automatic color. (v0.36.6)

**Storage in the plan.** Each plan line shows where the disks are now and where they will land, e.g. `local-lvm → intel-ssd-103`, or `on nas-ds01 (stays)` for a guest on shared storage. It follows the rules: a guest on shared storage stays put; a guest on local storage goes to the destination host's preferred (pinned) storage, else its local pool with the most free space. Changing the destination host updates it. It is informational: to steer it, set the host's default storage on the Nodes page or the guest's storage preference. (v0.36.6)

**Progress and sub-tasks.** In the Tasks panel a migration shows what it is doing: when its disks are on node-local storage you get two bars, "Disk copy → <storage>" (summed over every disk, with an average rate) and "VM memory (host to host)", and a note of which phase is running; a shared-storage move shows just the memory bar. Tasks started by another task (each migration of a Balance Load, evacuation or maintenance run, plus the steps still queued) are listed indented under their parent. (v0.36.6)

**History.** The page ends with a History card that records everything Balance Load does, not only the moves. Every check is a line: each automatic check
(on the schedule set under Automatic balancing) and each manual preview, with the outcome (Balanced, Skipped with the reason such as paused, outside the time window,
cooling down or not enough history, No move worth making, Plan prepared, Auto-approved, Stopped, Error), the balance score against the level's trigger, and who or what ran it.
A check that gives the same result as the one before is folded into one line with a count ("same result 288 times, from ... to ..."), so the list stays readable.
Open "What it looked at" for the node loads at that moment, the planned moves, the moves a safety rule refused and the guests left where they were. The moves that
followed are listed too (guest, from, to, storage, how long it took, who approved it). Filter by Everything, Checks or Moves. Kept for 90 days. A status strip at the top shows
whether automatic balancing is on, what the last check decided and when the next is due. API: `GET /api/auto-balance/history`. Table: `balance_checks` (migration 0050).

**How often to check.** Under Automatic balancing, *How often to check* sets the schedule per cluster: every 5, 15 or 30 minutes, every hour, 2, 4, 6 or 8 hours, or once a day (default every 5 minutes). Minute steps run on the clock (:00, :15, :30, :45). For hourly and longer steps you choose the time the slots are counted from: every 4 hours from 02:00 checks at 02:00, 06:00, 10:00, 14:00, 18:00 and 22:00; once a day checks at the time you pick. Times use the server's timezone (Settings). The status strip shows the schedule and when the next check is due. The schedule only says when PyXie looks; it can still prepare plans only inside the "When it may prepare plans" windows, and auto-approve still waits out its cooldown after a move, then plans the next move at the next scheduled check. Saving a new schedule does not run a check by itself: the next one happens at the next slot (or at once if none has run since the latest slot). Between scheduled checks nothing is recorded. API/config keys: `check_every` (`5m`, `15m`, `30m`, `1h`, `2h`, `4h`, `6h`, `8h`, `daily`) and `check_at` (`HH:MM`).


## Pinning (Affinity & Pinning page)

The Affinity Rules page is now **Affinity & Pinning**: *Where the guests sit now* runs the full width, then affinity rules on the left and pins on the right.

A **pin** ties one guest, or every guest carrying a tag, to **one or more hosts**:
- **Soft pin (default).** Those hosts are preferred. Balance Load and automatic balancing leave a guest that is on one of them alone, and placement adds a strong bonus for each of them when it picks a host (a guest pinned to st-pve101 or st-pve103 is happy on either). Maintenance and evacuation may still move it, so a soft pin never blocks a maintenance run.
- **Hard pin.** Only those hosts are allowed, maintenance included: every other host is refused as a destination ("hard pin: this guest may only run on ..."), so a maintenance plan that cannot honor it says so instead of moving the guest. Several hard pins on one guest combine to the hosts they all allow. A guest already outside a hard pin is shown as broken; Balance Load plans the move that puts it back (see "Rules are reviewed first" below).

In *Where the guests sit now* a pinned guest has a **dashed outline and a pin mark** in its pin's color (affinity members keep their solid fill), and each host box shows small dashed squares for the pins that name it. Pins have their own color set, deliberately different from the affinity colors; pick a swatch or any color when creating or editing a pin. Status per pin: Holding, Elsewhere (a soft pin whose guest is not on a pinned host), Broken (a hard pin), or Nothing to check.

The single-host pin on the workload page still works: it is shown here as a soft pin marked "set on the workload page", and clearing it on either page clears both. Editing it here into several hosts, a hard pin or a tag pin turns it into an ordinary pin. API: `GET/POST /api/placement/pin-rules`, `PUT/DELETE /api/placement/pin-rules/{id}`; table `placement_pin_rules` (migration 0051).

**Rules are reviewed first.** Every Balance Load run (a manual preview and every automatic check) first reviews the affinity rules and pins against where guests are now. A guest that breaks one is planned first, using the plan's move slots before any balancing move, and goes to the best host that meets the rule whatever the balance gain; its plan line is marked "fixes a rule". The load-balancing pass then works from where those fixes leave the guests. A rule-fix move moves the guest only: a guest on shared storage stays on it even if its own storage preference says local, so a pin fix never triggers a disk copy. A guest marked Do not move is reported but never moved. When the cluster is balanced but a rule is not met, the check plans only the rule fixes. In auto-approve mode a rule-fix move is approved and run on its own like any other automatic move, under the same safety gates (live migration only, no storage change, no node left above 80% memory, writes switched on); a plan that fails a gate waits for a person, and History says which gate. History shows what was reviewed and which rules were not met.

**Telemetry sanity check.** A running Proxmox host always uses some memory and a little CPU, so an online node whose memory reads **exactly 0%**, or has no reading at all, or whose stored CPU or memory history is exactly 0% for the last three samples, is not reporting properly, not idle. (A single live CPU poll of exactly 0% is a known Proxmox glitch, seen on this lab as one poll flickering 0 then 5% while the per-minute history stayed at 4-6%: PyXie ignores that one reading and keeps the last real value.) Automatic checks stop with "not checked: <node> is online but its memory reads exactly 0%" instead of balancing on a number that may be wrong, the History entry marks the node with a warning, and Health raises a warning for it ("Node X is online but ..."). A low but real value is fine: History now shows one decimal place (a quiet host can read 0.4%), and only an exact 0 counts. Every History entry's "What it looked at" also lists the affinity rules and pins that were reviewed and whether each was met.


**Tasks panel.** Recent now lists system activity as well as things people started: every Balance Load check, automatic or manual, appears as a "Balance Load check" line (Balanced, Skipped, Plan ready, Auto-approved, ...; repeats of the same result are folded into one line with a count; click it to open the History). Recent is a scrolling list of the last 7 days (up to 100 entries) instead of the last five, so you can read back through what happened; tasks started by another task are nested under their parent as before.
