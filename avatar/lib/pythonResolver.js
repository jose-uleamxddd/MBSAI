"use strict";
// Resuelve el intérprete Python para spawnear el backend de norivy-kiosk.
// CommonJS a propósito: lo consume `main.js` (Electron main process, CJS), NO el
// renderer. Los demás módulos de `lib/` son ES modules (los usa el renderer).
const path = require("path");
const fs = require("fs");

/**
 * Elige el Python con el que lanzar `python -m norivy_kiosk`.
 * Prioridad: `env.PYTHON` (override explícito) > el python del `.venv` del
 * proyecto (donde viven uvicorn/fastapi/...) > el python global del sistema
 * (que casi nunca tiene las deps → ModuleNotFoundError).
 * @param {object} opts
 * @param {string} opts.projectRoot  Raíz del proyecto (donde vive `.venv/`).
 * @param {string} [opts.platform]  `process.platform`.
 * @param {Record<string, string | undefined>} [opts.env]  `process.env`.
 * @param {(p: string) => boolean} [opts.exists]  `fs.existsSync` (inyectable en tests).
 * @returns {string} Comando o ruta absoluta del intérprete a usar.
 */
function resolvePython({
  projectRoot,
  platform = process.platform,
  env = process.env,
  exists = fs.existsSync,
} = {}) {
  if (env && env.PYTHON) return env.PYTHON;
  const isWin = platform === "win32";
  const venvPython = isWin
    ? path.join(projectRoot, ".venv", "Scripts", "python.exe")
    : path.join(projectRoot, ".venv", "bin", "python");
  if (exists(venvPython)) return venvPython;
  return isWin ? "python" : "python3";
}

module.exports = { resolvePython };
