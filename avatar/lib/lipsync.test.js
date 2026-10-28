import { describe, it, expect } from "vitest";
import { rmsToMouthAmp } from "./lipsync.js";

// Renderer defaults, for reference in assertions.
const GAIN = 5.0;
const SMOOTHING = 0.5;

describe("rmsToMouthAmp", () => {
  it("returns 0 on silence from a rested mouth", () => {
    // rms=0, prevAmp=0 → 0*0.5 + 0*0.5 = 0.
    expect(rmsToMouthAmp(0, { gain: GAIN, prevAmp: 0, smoothing: SMOOTHING })).toBe(0);
  });

  it("clamps the gained target to 1 on saturation", () => {
    // rms=1 * gain=5 = 5 → min(1, 5)=1. prevAmp=1 → 1*0.5 + 1*0.5 = 1.
    expect(rmsToMouthAmp(1, { gain: GAIN, prevAmp: 1, smoothing: SMOOTHING })).toBe(1);
    // Even from a rested mouth the smoothed value cannot exceed 1.
    expect(
      rmsToMouthAmp(10, { gain: GAIN, prevAmp: 0, smoothing: SMOOTHING }),
    ).toBeLessThanOrEqual(1);
  });

  it("interpolates between prevAmp and target with the smoothing weight", () => {
    // rms=0.1 * gain=5 = 0.5 (target). prevAmp=0, smoothing=0.5 → 0*0.5 + 0.5*0.5 = 0.25.
    expect(
      rmsToMouthAmp(0.1, { gain: GAIN, prevAmp: 0, smoothing: SMOOTHING }),
    ).toBeCloseTo(0.25, 10);
    // Same target but prevAmp=0.4 → 0.4*0.5 + 0.5*0.5 = 0.45 (pulled toward prev).
    expect(
      rmsToMouthAmp(0.1, { gain: GAIN, prevAmp: 0.4, smoothing: SMOOTHING }),
    ).toBeCloseTo(0.45, 10);
  });

  it("decays toward 0 as the target drops below prevAmp", () => {
    // rms=0 (target 0), prevAmp=0.8, smoothing=0.5 → 0.8*0.5 + 0 = 0.4.
    expect(
      rmsToMouthAmp(0, { gain: GAIN, prevAmp: 0.8, smoothing: SMOOTHING }),
    ).toBeCloseTo(0.4, 10);
  });

  it("with smoothing=0 snaps directly to the clamped target", () => {
    expect(rmsToMouthAmp(0.1, { gain: GAIN, prevAmp: 0.9, smoothing: 0 })).toBeCloseTo(
      0.5,
      10,
    );
  });
});
