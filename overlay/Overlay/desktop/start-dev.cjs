const { spawn } = require('node:child_process')
const http = require('node:http')

const url = 'http://127.0.0.1:5173/'
const electron = require('electron')
let attempts = 0

function waitForVite() {
  const request = http.get(url, (response) => {
    response.resume()
    if (response.statusCode >= 200 && response.statusCode < 500) {
      const child = spawn(electron, ['.'], { stdio: 'inherit', windowsHide: false })
      child.on('exit', (code, signal) => process.exit(code ?? (signal ? 1 : 0)))
      return
    }
    retry()
  })
  request.on('error', retry)
  request.setTimeout(500, () => {
    request.destroy()
    retry()
  })
}

function retry() {
  attempts += 1
  if (attempts >= 120) {
    console.error('Vite did not become ready at http://127.0.0.1:5173 within 60 seconds.')
    process.exit(1)
  }
  setTimeout(waitForVite, 500)
}

waitForVite()
