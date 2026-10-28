import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { base64ToInt16 } from "./pcm.js";

// Helper: build a base64 string from raw bytes (PCM16 LE payload).
function bytesToBase64(bytes) {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary);
}

describe("base64ToInt16", () => {
  beforeEach(() => {
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("decodes valid PCM16 little-endian into signed samples", () => {
    // 0x0001 LE = 256, 0xFFFF LE = -1, 0x0080 LE = -32768 (0x8000).
    const bytes = [0x00, 0x01, 0xff, 0xff, 0x00, 0x80];
    const result = base64ToInt16(bytesToBase64(bytes));
    expect(Array.from(result)).toEqual([256, -1, -32768]);
  });

  it("returns an empty Int16Array on corrupt base64 without throwing", () => {
    const result = base64ToInt16("!!!not-valid-base64!!!");
    expect(result).toBeInstanceOf(Int16Array);
    expect(result.length).toBe(0);
    expect(console.error).toHaveBeenCalled();
  });

  it("returns an empty Int16Array for empty/nullish input", () => {
    expect(base64ToInt16("").length).toBe(0);
    expect(base64ToInt16(null).length).toBe(0);
    expect(base64ToInt16(undefined).length).toBe(0);
  });

  it("drops the trailing byte on odd length and warns", () => {
    // 3 bytes → 1 full sample (0x0102 LE = 513), 1 byte discarded.
    const bytes = [0x02, 0x01, 0x7f];
    const result = base64ToInt16(bytesToBase64(bytes));
    expect(Array.from(result)).toEqual([258]);
    expect(console.warn).toHaveBeenCalled();
  });
});
