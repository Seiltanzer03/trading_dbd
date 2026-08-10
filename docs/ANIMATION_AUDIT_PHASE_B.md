# Phase B animation audit

Market-information motion was separated from weak UI indication.

| Surface | Previous clock-driven cue | Classification | Phase B contract |
|---|---|---|---|
| Cross-Asset network | Edge packets had a constant base speed; live ring expanded from `now` | Replace | Mirrored, non-causal packet phase advances only with measured correlation velocity. At zero velocity it stops. Live ring radius is a bounded function of the latest measured impulse. |
| Wavelet FLOW | Particles advanced with `(now / 900) % 1` | Replace | Flow source, destination, rate, particle count, brightness, thickness and phase come from observed energy-share transfer per elapsed time. Flat energy leaves phase unchanged. |
| GEX migration | Price glow used `sin(now)`; take-path particles had constant speed, random resets and clock wobble | Replace | Flow is driven by measured price velocity toward take, obstruction and field alignment, with inertia/damping. Turbulence uses field force, adverse acceleration and observed wall/flip migration, then decays. Particle layout is deterministic. |
| Macro live probe | Frame interpolation between tick targets | Keep | Target changes only on a real price packet. Frames provide inertia/damping and stop after convergence. No automatic camera or trajectory motion. |
| RND Strike Landscape | Historical density ridges breathed with `sin(now)` | Replace | Distribution snapshots are static until market/model data changes. |
| Fan/RND live marker and tail-zone glow | Low-amplitude pulse | UI context only | May identify the current marker/zone, but does not move geometry or encode magnitude/direction. |
| Lattice | Smooth interpolation | Keep | Existing frame-budgeted market/model interpolation is preserved; core is unchanged. |

Automated contracts cover: stationary motion decay, shock response, real Wavelet
rate, full observed Cross-Asset topology, direction-neutral packets, absence of
decorative packet base speed, unified 3D controls, and camera-guard ownership.
