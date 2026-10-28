import { describe, it, expect } from "vitest";
import { computeStartTime } from "./audioSchedule.js";

describe("computeStartTime", () => {
  it("respects nextStartTime when it is far enough in the future", () => {
    // now=10, nextStartTime=15 (>= 10 + 0.02) → reuse 15 (gapless continuation).
    expect(computeStartTime(15, 10)).toBe(15);
  });

  it("falls back to currentTime + gap when nextStartTime is 0 (nothing scheduled)", () => {
    // 0 is falsy → fallback. now=10, gap=0.03 → 10.03.
    expect(computeStartTime(0, 10)).toBeCloseTo(10.03, 10);
  });

  it("falls back when nextStartTime is stale (behind the min lead)", () => {
    // now=10, nextStartTime=10.01 (< 10 + 0.02) → stale → 10.03.
    expect(computeStartTime(10.01, 10)).toBeCloseTo(10.03, 10);
  });

  it("falls back on the exact min-lead boundary (strict <)", () => {
    // nextStartTime === now + minLead is NOT < now + minLead → still valid, reused.
    expect(computeStartTime(10.02, 10)).toBeCloseTo(10.02, 10);
  });

  it("honors custom minLeadS and gapS overrides", () => {
    // now=5, nextStartTime=5.4 < 5 + 0.5 → stale → 5 + 1.0 = 6.0.
    expect(computeStartTime(5.4, 5, { minLeadS: 0.5, gapS: 1.0 })).toBeCloseTo(6.0, 10);
  });
});
