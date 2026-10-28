import { describe, expect, it } from "vitest";
import {
  applyFrame,
  canStartSession,
  formatPrice,
  idleStep,
  initialView,
  safeHttpUrl,
  startSession,
  stockLabel,
  yearRange,
} from "./kioskView.js";

describe("applyFrame", () => {
  it("product_results muestra las tarjetas", () => {
    const v = applyFrame(startSession(), { type: "product_results", items: [{ id: "r1" }] });
    expect(v.main).toBe("products");
    expect(v.products).toEqual([{ id: "r1" }]);
  });

  it("applicability guarda el nombre y las motos", () => {
    const v = applyFrame(startSession(), {
      type: "applicability",
      product_name: "Pastillas",
      items: [{ brand: "Honda" }],
    });
    expect(v.main).toBe("applicability");
    expect(v.applicability).toEqual({ productName: "Pastillas", items: [{ brand: "Honda" }] });
  });

  it("quote_cart actualiza el carrito sin cambiar la vista principal", () => {
    const base = applyFrame(startSession(), { type: "product_results", items: [] });
    const v = applyFrame(base, { type: "quote_cart", items: [{ product_id: "r1" }], total: "18.5" });
    expect(v.main).toBe("products");
    expect(v.cart).toEqual({ items: [{ product_id: "r1" }], total: 18.5 });
  });

  it("quote_ready muestra el QR y vacía el carrito", () => {
    const withCart = applyFrame(startSession(), { type: "quote_cart", items: [{}], total: 5 });
    const v = applyFrame(withCart, {
      type: "quote_ready",
      url: "https://tienda.test/q.pdf",
      total: 43.9,
      currency: "USD",
      quote_id: "C1",
    });
    expect(v.main).toBe("quote_ready");
    expect(v.quote).toEqual({ url: "https://tienda.test/q.pdf", total: 43.9, currency: "USD", quoteId: "C1" });
    expect(v.cart.items).toEqual([]);
  });

  it("quote_ready con enlace no http(s) se ignora (nunca javascript:)", () => {
    const base = startSession();
    expect(applyFrame(base, { type: "quote_ready", url: "javascript:alert(1)" })).toBe(base);
    expect(applyFrame(base, { type: "quote_ready", url: "" })).toBe(base);
  });

  it("session_reset vuelve a la pantalla de atracción", () => {
    const busy = applyFrame(startSession(), { type: "product_results", items: [{}] });
    expect(applyFrame(busy, { type: "session_reset" })).toEqual(initialView());
  });

  it("frames raros o desconocidos no cambian nada", () => {
    const v = startSession();
    expect(applyFrame(v, null)).toBe(v);
    expect(applyFrame(v, { type: "audio" })).toBe(v);
    expect(applyFrame(v, { type: "product_results", items: "no-lista" }).products).toEqual([]);
  });
});

describe("helpers", () => {
  it("safeHttpUrl", () => {
    expect(safeHttpUrl("http://a.test/x")).toBe("http://a.test/x");
    expect(safeHttpUrl("file:///etc/passwd")).toBe("");
    expect(safeHttpUrl("no es url")).toBe("");
  });

  it("formatPrice formatea en dólares y tolera basura", () => {
    expect(formatPrice(18.5, "USD")).toMatch(/18[.,]50/);
    expect(formatPrice("abc")).toBe("");
  });

  it("stockLabel", () => {
    expect(stockLabel(null).level).toBe("unknown");
    expect(stockLabel(0).level).toBe("out");
    expect(stockLabel(2).level).toBe("low");
    expect(stockLabel(10)).toEqual({ text: "Disponible", level: "ok" });
  });

  it("yearRange", () => {
    expect(yearRange(2016, 2024)).toBe("2016–2024");
    expect(yearRange(2020, 2020)).toBe("2020");
    expect(yearRange(2016, null)).toBe("desde 2016");
    expect(yearRange(null, 2024)).toBe("hasta 2024");
    expect(yearRange(null, null)).toBe("");
  });

  it("idleStep avisa y luego reinicia", () => {
    const limits = { warnAfterMs: 50_000, resetAfterMs: 60_000 };
    expect(idleStep(10_000, limits)).toBe("none");
    expect(idleStep(50_000, limits)).toBe("warn");
    expect(idleStep(60_000, limits)).toBe("reset");
  });
});

describe("canStartSession", () => {
  it("solo con voz disponible", () => {
    expect(canStartSession("connected")).toBe(true);
    expect(canStartSession("working")).toBe(true);
    for (const bad of ["connecting", "offline", "fatal", "error", "mic_error", ""]) {
      expect(canStartSession(bad)).toBe(false);
    }
  });
});
