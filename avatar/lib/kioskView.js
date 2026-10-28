// kioskView.js — Lógica PURA de lo que muestra la pantalla del kiosco.
//
// Comentario en español: sin DOM ni timers, para poder testearla con Vitest.
// `applyFrame` convierte los frames del backend (product_results, applicability,
// quote_cart, quote_ready, session_reset) en el estado de la vista; el renderer
// solo pinta ese estado. `idleStep` decide cuándo avisar/cerrar por inactividad.

/** Vista inicial = pantalla de atracción (nadie atendido). */
export function initialView() {
  return {
    session: "attract", // attract | active
    main: "welcome", // welcome | products | applicability | quote_ready
    products: [],
    applicability: null, // { productName, items: [...] }
    cart: { items: [], total: 0 },
    quote: null, // { url, total, currency, quoteId }
  };
}

/** El cliente tocó la pantalla: empieza la atención con la bienvenida. */
export function startSession() {
  return { ...initialView(), session: "active" };
}

/** Solo URLs http(s): un enlace de la API nunca debe poder ser javascript:/file:. */
export function safeHttpUrl(value) {
  try {
    const url = new URL(String(value));
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : "";
  } catch {
    return "";
  }
}

/**
 * Aplica un frame del backend a la vista. Frames desconocidos o malformados se
 * ignoran (devuelve la misma vista): el backend nunca debe poder romper la UI.
 */
export function applyFrame(view, frame) {
  if (!frame || typeof frame !== "object") return view;
  switch (frame.type) {
    case "product_results":
      return {
        ...view,
        main: "products",
        products: Array.isArray(frame.items) ? frame.items : [],
      };
    case "applicability":
      return {
        ...view,
        main: "applicability",
        applicability: {
          productName: String(frame.product_name || ""),
          items: Array.isArray(frame.items) ? frame.items : [],
        },
      };
    case "quote_cart":
      return {
        ...view,
        cart: {
          items: Array.isArray(frame.items) ? frame.items : [],
          total: Number(frame.total) || 0,
        },
      };
    case "quote_ready": {
      const url = safeHttpUrl(frame.url);
      if (!url) return view; // sin enlace válido no hay QR que mostrar
      return {
        ...view,
        main: "quote_ready",
        cart: { items: [], total: 0 },
        quote: {
          url,
          total: Number(frame.total) || 0,
          currency: String(frame.currency || "USD"),
          quoteId: String(frame.quote_id || ""),
        },
      };
    }
    case "session_reset":
      return initialView();
    default:
      return view;
  }
}

/** Precio con moneda en formato local (ej. «$1,250.00»). */
export function formatPrice(amount, currency = "USD") {
  const value = Number(amount);
  if (!Number.isFinite(value)) return "";
  try {
    return new Intl.NumberFormat("es-EC", { style: "currency", currency }).format(value);
  } catch {
    return `${value.toFixed(2)} ${currency}`;
  }
}

/** Etiqueta de disponibilidad: stock null = la API no lo informa. */
export function stockLabel(stock) {
  if (stock === null || stock === undefined) return { text: "Consultar stock", level: "unknown" };
  if (stock <= 0) return { text: "Agotado", level: "out" };
  if (stock <= 3) return { text: `Últimas ${stock} unidades`, level: "low" };
  return { text: "Disponible", level: "ok" };
}

/** Rango de años de una aplicabilidad: «2016–2024», «desde 2016», «hasta 2024» o «». */
export function yearRange(from, to) {
  if (from && to) return from === to ? String(from) : `${from}–${to}`;
  if (from) return `desde ${from}`;
  if (to) return `hasta ${to}`;
  return "";
}

/**
 * Qué hacer según el tiempo sin actividad: "none" | "warn" | "reset".
 * `warnAfterMs` avisa «¿sigue ahí?»; `resetAfterMs` cierra la sesión.
 */
export function idleStep(idleMs, { warnAfterMs, resetAfterMs }) {
  if (idleMs >= resetAfterMs) return "reset";
  if (idleMs >= warnAfterMs) return "warn";
  return "none";
}

/** Estados del pill en los que Norivy puede atender (voz realmente disponible). */
const SESSION_READY_STATES = new Set(["connected", "listening", "speaking", "working"]);

/** ¿Se puede iniciar una atención? No con el backend caído ni con OpenAI fatal. */
export function canStartSession(pillState) {
  return SESSION_READY_STATES.has(pillState);
}
