# 11 — Hardening round 7 (evaluation + fix), 2026-07

Round-7 adversarial evaluation after round 6 (`8687fb9` / upstream
`5ad25ff`), aimed at the freshest surfaces (rounds 2–6 exhausted the load
model). One real finding; the newest code held.

## Findings → fixes

| ID | Sev | Finding | Fix |
|----|-----|---------|-----|
| F35 | MED-LOW | **Sub-assembly toppling under overhang.** F30 keeps each floor box's CoM over the deck and the stacked rule keeps each box's CoM over its supporter — but nothing checks a connected sub-assembly. A box stacked on the overhanging part of a floor box has its CoM over the supporter yet past the deck edge; the combined `{floor+stacked}` CoG can project off the deck → the pair tips, `validate()` clean. Hand-proven at the DEFAULT config (combined CoG x=1063.6 > deck edge 1000). NOT engine-reachable (0/160; recenter + heavy-low realism avoid it). Also disproves the round-6 claim that F30 "transitively bounds the whole-pallet CoG" | Auto-activate the deck-footprint CoG envelope under overhang (adapter: `cog_envelope_fraction=0.5` → `[0,L]×[0,W]`, `cog_check_min_load_fraction=0.0`). The engine's existing per-placement running-CoG reject then AVOIDS off-deck-CoG layouts during decode; `validate()` re-checks it under overhang as the safety net (gated no-stricter-than-the-engine). Machinery reuse — no new engine/twin code. ADR D20 |

## Verified clean (no fix)

- **F31 multi-pallet objective — clean.** 1,120 of my multibin fitness
  checks (parity |Δ|<1e-12, n_used consistent, no NaN/inf, discriminates)
  + a full independent audit across 300+ instances (parity *exactly* 0.0,
  dominance proof holds, dust-degeneracy exactly as documented, group
  param-forwarding byte-identical, gate incl. `max_pallets ≤ 0` correct).
- **Cold-start JIT — refuted.** The ~4s I suspected was the local-search
  polish budget (cProfile: ~55k decode evals), not Numba compile: no-polish
  is 0.01s, `ls_budget=0.1` is 0.10s. The image uses Cython AOT `.so`
  decoders; `warmup_jit()` is a no-op; there is no runtime JIT.
- **Ops lifecycle — no new severe defect.** Unbounded queue / no 503
  backpressure (MED) and the ">24h queued" edge (LOW) are pre-existing
  documented backlog. Correction to the latter's framing: the observable is
  **stuck-`queued` → `job expired` failure**, not a 404 (the arq queue zset
  never expires; the job-data key TTLs at ~24h). Result size (~112 KB for
  500 boxes) fine; `max_tries=2` can't retry-loop.

## The fix (F35 / ADR D20)

The CoG envelope is a **per-placement running-CoG reject** in both the v2
engine (`_cog_ok`) and the JIT decoders (`_check_cog_envelope_njit`), so
activating it steers the search away from off-deck-CoG layouts during decode
rather than merely flagging them. `cog_envelope_fraction=0.5` resolves to
exactly the deck footprint `[0,L]×[0,W]`; `cog_check_min_load_fraction=0.0`
enforces it from the second box. The **fraction** path (not explicit cog
ranges) keeps the recenter realism pass alive. `validate()` re-checks it
only under overhang (where the constraint decoders enforce it), staying
no-stricter-than-the-engine. Whole-pallet scope covers every reachable case
(0/160); the isolated-sub-tower residual is documented deferred (ADR D20).
Inert without overhang → goldens/BR/battery bit-identical.

## Gate evidence

Service suite 140 host-tier (135 + 5 new F35 tests) / Redis-backed at ship;
ruff clean; upstream equivalence 2670/0 (validate.py has no twin — the cstr
CoG path is already swept), physics 0, BR + all three goldens bit-identical
(no overhang → CoG stays off); realism battery 19/22 unchanged; the F35
reachability probe → 0 (engine now avoids CoG-off-deck) and the hand case
rejected end-to-end. Probe artifacts: scratchpad `probe_subassembly_tip.py`,
`fuzz_r7_multibin.py`, `round7_findings.md`.
