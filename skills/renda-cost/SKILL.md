---
name: renda-cost
description: How Renda bills cloud renders and how to keep them cheap. Covers per-second metering by hardware class, the free tier, estimates, quality presets, resolution, sample and frame tradeoffs, economy priority, and free cache hits. Use whenever the user asks what a Renda render costs, wants to reduce render spend, is about to start a large, final, high-resolution or video render, asks about usage or invoices, or gets a payment_required error.
---

# Renda cost

## How billing works

- You pay for **container-seconds spent rendering a job** (scene load + render + encode),
  metered per second and summed into render-minutes on the invoice. Queue time and cold
  starts before Renda's code runs are free.
- The rate depends on the hardware class the job ran on. Get current prices from
  `renda_pricing`; defaults are about **cpu $0.02/min · gpu-l4 $0.06/min · gpu-l40s $0.15/min**.
- New orgs get **30 free render-minutes**, then a card is required (`renda_billing_link`
  gives the human a Stripe link).
- **Cache hits are free.** Resubmitting an identical package with the same asset bytes and
  engine version returns the previous outputs at $0 (`cached: true`). So don't add random
  seeds or names "just because". Change only what matters.
- Canceling stops billing; you pay only for seconds already used.
- `renda_usage` shows seconds, minutes billed and cost for any date range.

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
   `standard` default; `rush` uses bigger GPUs (fastest, costliest per minute, though often
   similar total for short jobs).

## Cheap-by-default habits

- Draft first, look, then final (the `renda` skill workflow). A draft is usually under 5% of a final.
- For video, check one still frame before rendering all frames.
- Reuse uploads: assets are content-addressed, so re-uploading the same file is instant and free.
- Batch variations as separate small jobs rather than one huge one; unchanged ones hit the cache.
- Use `output.passes` only when needed. Each extra pass adds render and encode time.
- Prefer `.glb`/`.usdz` over huge loose scenes. Scene load time is billed on every chunk.

## Explaining costs to users

Report actuals from the job: `billedSeconds`, `hardware`, `costUsd`, and `cached`.
For example: "Rendered in 42 s on an L4 GPU: $0.04." Don't make up prices. Use
`renda_pricing` or `renda_estimate`.
