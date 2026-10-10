const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('desktopBridge', {
  isDesktop: true,
  getBackendUrl: () => ipcRenderer.invoke('backend-url'),
  toggleVisibility: () => ipcRenderer.invoke('toggle-visibility'),
  setWindowSize: (width, height) => ipcRenderer.invoke('set-window-size', { width, height }),
  setIgnoreMouse: (ignore) => ipcRenderer.invoke('set-ignore-mouse', Boolean(ignore)),
  quit: () => ipcRenderer.invoke('quit-app'),
  onShortcut: (callback) => {
    const listener = (_event, action) => callback(action)
    ipcRenderer.on('shortcut', listener)
    return () => ipcRenderer.removeListener('shortcut', listener)
  },
})
