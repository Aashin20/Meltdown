const { app, BrowserWindow, globalShortcut, ipcMain, Menu, screen, shell, Tray } = require('electron')
const { spawn } = require('node:child_process')
const path = require('node:path')
const http = require('node:http')

const isDev = !app.isPackaged
const backendPort = Number(process.env.MELTDOWN_BACKEND_PORT || 8000)
const backendUrl = `http://127.0.0.1:${backendPort}`
let mainWindow
let tray
let backendProcess

const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!mainWindow) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.showInactive()
    mainWindow.focus()
  })
}

function createWindow() {
  const display = screen.getPrimaryDisplay()
  const { width, height } = display.workAreaSize
  mainWindow = new BrowserWindow({
    width: 420,
    height: 420,
    x: Math.max(0, width - 460),
    y: Math.max(0, height - 460),
    transparent: true,
    backgroundColor: '#00000000',
    frame: false,
    resizable: false,
    movable: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    show: false,
    hasShadow: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, 'preload.cjs'),
    },
  })
  mainWindow.setAlwaysOnTop(true, 'floating')
  mainWindow.setMenuBarVisibility(false)
  mainWindow.on('closed', () => { mainWindow = undefined })
  mainWindow.webContents.on('did-fail-load', (_event, code, description, url) => {
    console.error(`Renderer load failed (${code}): ${description} (${url})`)
  })
  mainWindow.webContents.on('console-message', (_event, level, message) => {
    console.log(`[renderer:${level}] ${message}`)
  })
  mainWindow.once('ready-to-show', () => mainWindow.showInactive())
  setTimeout(() => {
    if (mainWindow && !mainWindow.isVisible()) mainWindow.showInactive()
  }, 3000)
  if (process.env.MELTDOWN_DEBUG === '1') {
    mainWindow.webContents.openDevTools({ mode: 'detach' })
  }

  if (isDev) {
    loadDevRenderer()
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html')).catch((error) => {
      console.error(`Packaged renderer failed to load: ${error.message}`)
    })
  }
}

function loadDevRenderer(attempt = 0) {
  mainWindow.loadURL('http://localhost:5173').catch(() => {
    if (attempt < 30 && mainWindow) setTimeout(() => loadDevRenderer(attempt + 1), 250)
  })
}

function createTray() {
  const iconPath = isDev
    ? path.join(__dirname, '..', 'public', 'favicon.ico')
    : path.join(__dirname, '..', 'dist', 'favicon.ico')
  try {
    tray = new Tray(iconPath)
    tray.setToolTip('Meltdown Voice Orb')
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: 'Show orb', click: () => mainWindow?.showInactive() },
      { label: 'Hide orb', click: () => mainWindow?.hide() },
      { type: 'separator' },
      { label: 'Quit', click: () => app.quit() },
    ]))
    tray.on('double-click', () => mainWindow?.showInactive())
  } catch (error) {
    console.error(`Tray creation failed: ${error.message}`)
  }
}

function registerShortcuts() {
  const registered = [
    ['CommandOrControl+Alt+Space', () => toggleVisibility()],
  ].map(([accelerator, action]) => [accelerator, globalShortcut.register(accelerator, action)])
  registered.filter(([, success]) => !success).forEach(([accelerator]) => {
    mainWindow?.webContents.send('shortcut', { action: 'registration-failed', accelerator })
  })
}

function toggleVisibility() {
  if (!mainWindow) return
  if (mainWindow.isVisible()) mainWindow.hide()
  else mainWindow.showInactive()
}

function waitForBackend() {
  return new Promise((resolve) => {
    const request = http.get(`${backendUrl}/health`, (response) => {
      response.resume()
      resolve(response.statusCode === 200)
    })
    request.on('error', () => resolve(false))
    request.setTimeout(500, () => { request.destroy(); resolve(false) })
  })
}

function startBackend() {
  if (process.env.MELTDOWN_BACKEND_AUTO_START !== '1') return
  const command = process.env.MELTDOWN_BACKEND_COMMAND
  if (!command) return
  backendProcess = spawn(command, ['-m', 'uvicorn', 'server.app:app', '--host', '127.0.0.1', '--port', String(backendPort)], {
    cwd: process.env.MELTDOWN_BACKEND_CWD || path.join(__dirname, '..', '..', '..'),
    stdio: 'ignore',
    windowsHide: true,
    shell: false,
  })
  backendProcess.on('error', (error) => {
    mainWindow?.webContents.send('shortcut', { action: 'backend-error', message: error.message })
  })
}

if (gotLock) {
  app.whenReady().then(async () => {
    try {
      ipcMain.handle('backend-url', () => backendUrl)
      ipcMain.handle('toggle-visibility', () => toggleVisibility())
      ipcMain.handle('set-window-size', (_event, { width, height }) => {
        if (!mainWindow) return
        const b = mainWindow.getBounds()
        const area = screen.getDisplayMatching(b).workArea
        const w = Math.min(Math.round(width), area.width)
        const h = Math.min(Math.round(height), area.height)
        const x = Math.max(area.x, Math.min(b.x + b.width - w, area.x + area.width - w))
        const y = Math.max(area.y, Math.min(b.y + b.height - h, area.y + area.height - h))
        mainWindow.setBounds({ x, y, width: w, height: h })
      })
      ipcMain.handle('set-ignore-mouse', (_event, ignore) => {
        mainWindow?.setIgnoreMouseEvents(Boolean(ignore), { forward: true })
      })
      ipcMain.handle('quit-app', () => app.quit())
      startBackend()
      createWindow()
      createTray()
      registerShortcuts()
      if (await waitForBackend()) mainWindow?.webContents.send('shortcut', { action: 'backend-ready' })
    } catch (error) {
      console.error(`Desktop startup failed: ${error.stack || error.message}`)
      mainWindow?.showInactive()
    }
  }).catch((error) => {
    console.error(`Electron ready handler failed: ${error.stack || error.message}`)
  })
}

app.on('will-quit', () => {
  globalShortcut.unregisterAll()
  if (backendProcess && !backendProcess.killed) backendProcess.kill()
})

app.on('window-all-closed', (event) => event.preventDefault())
app.on('activate', () => mainWindow?.showInactive())
app.on('web-contents-created', (_event, contents) => {
  contents.setWindowOpenHandler(({ url }) => {
    if (url.startsWith('https://')) shell.openExternal(url)
    return { action: 'deny' }
  })
})
