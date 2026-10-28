import { describe, it, expect } from "vitest";
import { advanceReveal } from "./captionPacer.js";

describe("advanceReveal", () => {
  it("avanza rate*dt caracteres", () => {
    expect(advanceReveal(0, 100, 0.5, 16)).toBe(8);
  });

  it("no pasa el largo del texto recibido", () => {
    expect(advanceReveal(9, 10, 1, 16)).toBe(10);
  });

  it("no retrocede si ya reveló más de lo que tocaría", () => {
    expect(advanceReveal(5, 100, 0, 16)).toBe(5);
  });

  it("dt cero o negativo no avanza (no revela con el reloj parado/atrás)", () => {
    expect(advanceReveal(5, 100, 0, 16)).toBe(5);
    expect(advanceReveal(5, 100, -1, 16)).toBe(5);
  });

  it("respeta el rate configurado", () => {
    expect(advanceReveal(0, 100, 1, 20)).toBe(20);
  });
});
