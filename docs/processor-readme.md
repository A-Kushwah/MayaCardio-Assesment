# MayaCardio Processing Module

Processing engine for cardiac monitoring device (ARM64 Pi, 2GB RAM, 4 cores).

## Design Tradeoffs

- **Same logic online/offline**: `Processor` takes `mode` at init; statistics and HR algorithm are identical regardless. This satisfies the spec's "Same logic runs online and offline" and avoids divergence.
- **History buffers for HR**: Peak detection requires looking back 800 samples. We keep up to 2800 samples (5 sec + 800) in `deque` with `maxlen`, which is memory-safe on 2GB RAM.
- **No persistence/network here**: This module is pure computation. Persistence and sync are handled by other agents.
- **Null safety**: Every statistic returns `None` when no valid samples exist; `complete` is `True` only when both sensors have zero missing in the 400-sample window.
- **Peak detection strictness**: A peak requires 800 previous samples, 1 after, larger than both neighbours, larger than half of previous max, and at least 100 samples after previous peak. The `process_window()` only computes HR for window `w < 4` (needs previous 5 seconds), and requires zero missing samples in the 5-second history slice.
