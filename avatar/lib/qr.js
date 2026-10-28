// qr.js — Dibuja un código QR (SVG) con `uqr`, cargado por el importmap de index.html.
//
// Comentario en español: el SVG lo genera la librería a partir de la URL (solo
// rectángulos/paths; la URL nunca se inserta como HTML). Fondo blanco y módulo
// negro: máximo contraste para que cualquier celular lo lea desde la pantalla.
import { renderSVG } from "uqr";

/**
 * Pinta el QR de `url` dentro de `container` (reemplaza su contenido).
 * @param {HTMLElement} container
 * @param {string} url
 */
export function renderQr(container, url) {
  container.innerHTML = renderSVG(url, { border: 2 });
}
