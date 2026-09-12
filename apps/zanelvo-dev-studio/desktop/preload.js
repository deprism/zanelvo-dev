"use strict";
// The renderer is the existing web frontend, which talks to the backend over plain HTTP (see
// main.js's injected window.__DESKTOP_API_BASE__) and needs no Node/Electron APIs — except during
// startup, before that frontend is even loaded: main.js's own inline loading page (LOADING_HTML)
// needs a safe way to receive live "starting local database… / installing dependencies… / raw pip
// output" status updates, which is what onStatus exposes. contextIsolation stays on; only this one
// narrow, read-only channel is bridged.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("electronAPI", {
  onStatus: (callback) => {
    ipcRenderer.on("startup-status", (_event, status, log) => callback(status, log));
  },
});
