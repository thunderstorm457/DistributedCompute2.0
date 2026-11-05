# Compute Credit Model, Version 1

This is a pilot accounting unit, not a token, currency, or promise of cash
value. The model starts with CPU and reserved memory because those are the
first resources the platform can meter consistently. GPU, storage, and
network pricing remain out of scope until the network has benchmarks and
reliable usage measurements for them.

## What One Credit Buys

One Compute Credit (CC) buys either:

- 60 seconds of reference CPU time, or
- 60 GiB-seconds of reserved memory, equal to 1 GiB reserved for 1 minute.

When CPU and memory are used together, the charge is the larger of the two
resource quantities, not their sum. Thus, 1 reference CPU core with 1 GiB
reserved for one minute costs 1 CC. One core with 4 GiB reserved for one
minute costs 4 CC. Four cores with 4 GiB reserved for 30 minutes costs 120 CC.
For a distributed task, calculate each node's allocation separately and sum
the node quotes.

CPU is normalized by a versioned benchmark score:

```text
reference CPU seconds = measured CPU seconds * node benchmark score / reference benchmark score
CPU credits = reference CPU seconds / 60
memory credits = reserved GiB-seconds / 60
charge = max(CPU credits, memory credits), rounded up to 0.01 CC
```

A faster node uses fewer seconds to complete equivalent work, so it should
earn and charge roughly the same credits for that work. The exact benchmark
suite and reference score must be published before credits are issued; they
are intentionally not guessed here because NodeScout does not yet produce a
standard benchmark score.

## Economy Nodes

Economy nodes independently run the same versioned calculator over signed,
completed-task usage receipts. They attest to proposed quotes and settlements;
they do not create credits, alter balances, or set private prices. The ledger
accepts a settlement only after the network's configured quorum agrees on the
same receipt, model version, and result. Quorum size, operator diversity, and
anti-Sybil rules must be defined before this role is enabled in a public
network.

The pilot keeps the reference rate fixed. If a resource is scarce, tasks wait
or receive less capacity rather than silently paying a changing price. A
future version may publish epoch-based resource rates, but only from
observable supply and demand data with signed, reproducible calculations.

## Quotes and Settlement

Before a task starts, the service quotes a maximum using requested vCPUs,
maximum duration, reserved RAM, and the selected node's benchmark score. The
user sets a credit budget for the whole task, regardless of which nodes they
contributed. At completion, settlement sums measured CPU time and reserved RAM
time across all assigned nodes; work is stopped when its budget is exhausted.
Failed or unverified work is not credited to the contributor. Unused reserved
balance is released.

For example, a 4-vCPU, 4-GiB task with a 30-minute maximum on a reference node
quotes 120 CC. If it completes in 10 minutes using all four CPUs, the measured
CPU charge is about 40 CC and the RAM reservation is about 40 CC, so the final
charge is 40 CC.

A 64-vCPU task split into 64 one-vCPU, 1-GiB allocations for 10 minutes quotes
640 CC at the reference rate. The user does not need to own those 64 CPUs; one
account can spend its balance on capacity contributed by many providers.
However, a normal Docker container cannot be split automatically. The workload
must expose independent tasks or implement distributed communication. A
single shared-memory program requiring 64 cores on one machine cannot get that
memory space by combining 64 unrelated nodes.

## Contribution and Fairness

Contributors earn credits for completed, accepted work using the same resource
formula. Uptime without useful work does not mint ongoing credits. Credits do
not expire, and the pilot has no cash conversion, user-to-user transfer, token,
or platform fee. Credits earned by an account can be spent on any available
node in the network; they are not tied to the contributor's own machine. A user
who contributes more verified compute than they consume can build a balance;
a user who requests more than their balance can contribute more, wait for
available capacity, or reduce the task budget.

Persistent services reserve resources for longer periods and therefore spend
credits continuously. They need a separate reservation and scale-to-zero UX;
they should not be presented as free serverless functions.

## Not Yet Priced

This version does not price GPU time, storage, network egress, availability
reservations, or platform operations. Those must be measured and added as new
versioned resource rates, not hidden inside the CPU rate. The quote calculator
is separate from the API until trusted task telemetry and an auditable credit
ledger exist.