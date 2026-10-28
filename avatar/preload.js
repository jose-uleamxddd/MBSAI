// norivy-kiosk preload — API minima expuesta al renderer.
// Todo el control de la atención fluye por el WebSocket al backend; aca solo
// exponemos la config de bootstrap (WS url + paths de assets).

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("stage", {
  /** @returns {Promise<{ wsUrl: string, vrmPath: string, animationsBase: string }>} */
  getConfig: () => ipcRenderer.invoke("stage:get-config"),
});
