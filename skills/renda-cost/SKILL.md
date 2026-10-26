---
name: renda-cost
description: How Renda bills cloud renders and how to keep them cheap. Covers prepaid credit, per-second metering by hardware class, estimates, quality presets, resolution, sample and frame tradeoffs, economy priority, and free cache hits. Use whenever the user asks what a Renda render costs, wants to reduce render spend, is about to start a large, final, high-resolution or video render, asks about usage, credit or refunds, or gets a payment_required or insufficient_credit error.
---

# Renda cost

## How billing works

- Renda is **prepaid**: rendering draws down the workspace's credit balance (USD). There is no
  free render time, not for trials and not on signup. `renda_whoami` shows `balance.creditUsd`.
- You pay for **container-seconds spent rendering a job** (scene load + render + encode),
  metered per second (prices are quoted per render-minute). Queue time and cold
  starts before Renda's code runs are free.
- The rate depends on the hardware class the job ran on. Get current prices from
  `renda_pricing`; today they are **cpu $0.015/min · gpu-l4 $0.035/min · gpu-l40s $0.075/min**.
- **Buying credit:** `renda_billing_link` (CLI `renda billing`) returns a Stripe Checkout link.
  Give it to the human; they choose the amount there ($5 to $1000, default $25) and the credit is
  added as soon as the payment succeeds. You can't pay yourself.
- **Not enough credit:** a job is refused with `payment_required` when the balance is $0 or less,
  and with `insufficient_credit` when the balance is below the low end of the job's estimate. In
  both cases get the checkout link and send it to the human (or make the job cheaper). A running
  job is stopped if the credit runs out mid-render.
- Credit doesn't expire. Unused credit is refundable in full on request within 30 days of
  purchase, and renders that failed because of a Renda fault are credited back on request.
- **Cache hits are free.** Resubmitting an identical package with the same asset bytes and
  engine version returns the previous outputs at $0 (`cached: true`). So don't add random
  seeds or names "just because". Change only what matters.
- Canceling stops billing; you pay only for seconds already used.
- `renda_usage` shows seconds, minutes billed and cost for any date range.
- Spend caps: `maxCostUsd` on a job refuses it if its estimate is higher, and an optional monthly
  spending limit (`PUT /api/v1/billing/limit`) is off by default (the balance already caps spend).
- Concurrency: 2 jobs at once until the workspace has bought credit, then 20.

## Before you spend

Call `renda_estimate` (free) before any of these, and tell the user the range when the
upper bound is above about $1, or above what they said they'd spend:
- `quality: "final"`
- resolution above 1920×1080
- any video, or more than 1 frame
- `blender-cycles` / `mitsuba` on large scenes
- `priority: "rush"`

## What drives cost (biggest first)

1. **Frame count.** Cost is roughly linear in frames. 4 s at 30 fps is 120 renders. Use
   `step: 2`, shorter clips, or 24 fps for previews.
2. **Pixels.** Linear in width × height: 4K costs 4× 1080p. Render at the size you'll
   actually display.
3. **Quality / samples.** `draft` ≪ `standard` < `final`. Denoising (on by default) lets
   lower presets look clean. Raise `quality` before hand-tuning `samples`.
4. **Engine.** workbench/threejs (CPU, cheapest) < eevee (fast GPU) < cycles/mitsuba
   (path tracing). Auto already picks the cheapest that fits.
5. **Priority.** `economy` allows cheaper hardware and batching (slower, lowest cost);
   `standard` default; `rush` = **fast mode** (`--fast`, `fast: true` in MCP/API): fans out as wide as it can
   on faster GPUs. Big stills (≥ ~4 MP, Cycles/Eevee/Mitsuba, no passes or screen-space post) split into
   tiles rendered in parallel and stitched; animations use up to 20 L40S containers; splat bakes fan the
   views out and refine on an L40S. Typically ~1.3–1.6× the cost for several times less wall time —
   `renda estimate` prints "fast: $X in Y vs standard: $A in B" so you can offer the trade to the user.

## Cheap-by-default habits

- Draft first, look, then final (the `renda` skill workflow). A draft is usually under 5% of a final.
  With `engine: auto`, drafts of mesh formats (glTF, USD, STL, OBJ, STEP, …) run on three.js on CPU — the cheapest
  rate and no wait for a GPU; `.blend/.fbx/.abc` drafts use Eevee.
- For video, check one still frame before rendering all frames.
- Reuse uploads: assets are content-addressed, so re-uploading the same file is instant and free.
- Batch variations as separate small jobs rather than one huge one; unchanged ones hit the cache.
- Use `output.passes` only when needed. Each extra pass adds render and encode time.
- Prefer `.glb`/`.usdz` over huge loose scenes. Scene load time is billed on every chunk.

## Explaining costs to users

Report actuals from the job: `billedSeconds`, `hardware`, `costUsd`, and `cached`.
For example: "Rendered in 42 s on an L4 GPU: $0.04." Don't make up prices. Use
`renda_pricing` or `renda_estimate`.
