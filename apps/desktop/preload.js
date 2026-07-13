// SHIMS Desktop preload — exposes a minimal, safe bridge to the renderer.
// The frontend's custom titlebar calls window.shimsDesktop.{minimize,maximize,close}.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('shimsDesktop', {
  minimize: () => ipcRenderer.send('win:minimize'),
  maximize: () => ipcRenderer.send('win:maximize'),
  close: () => ipcRenderer.send('win:close'),
  platform: process.platform,
  isDesktop: true,
});
