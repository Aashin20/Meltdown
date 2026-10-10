const { spawn } = require('node:child_process')
const fs = require('node:fs/promises')
const path = require('node:path')

const root = path.resolve(__dirname, '..')
const builder = path.join(root, 'node_modules', 'electron-builder', 'cli.js')
const output = path.join(root, 'release', `build-${process.pid}-${Date.now()}`)
const stagingOutput = path.join(output, 'win-unpacked.tmp')
const unpackedOutput = path.join(output, 'win-unpacked')

function runBuilder() {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [
      builder,
      '--publish',
      'never',
      '--config.directories.output',
      output,
    ], {
      cwd: root,
      stdio: 'inherit',
      windowsHide: false,
    })
    child.on('exit', (code, signal) => resolve(code ?? (signal ? 1 : 0)))
  })
}

async function main() {
  process.stdout.write(`Packaging to ${output}\n`)
  let code = await runBuilder()
  if (code === 0) {
    process.exitCode = 0
    return
  }

  // electron-builder extracts into win-unpacked.tmp and then renames it.
  // Windows Defender can reject that final rename even though a direct move
  // succeeds. Complete the move ourselves before asking the builder to retry.
  try {
    await fs.rm(unpackedOutput, { recursive: true, force: true })
    await fs.rename(stagingOutput, unpackedOutput)
    console.warn('Recovered electron-builder staging directory after EPERM.')
  } catch (error) {
    try {
      await fs.rm(stagingOutput, { recursive: true, force: true })
    } catch (cleanupError) {
      console.error(`Could not clean ${stagingOutput}: ${cleanupError.message}`)
      process.exitCode = code
      return
    }
    console.warn(`Staging recovery was unavailable (${error.message}); retrying clean.`)
  }
  console.warn('Retrying electron-builder after recovering its staging directory.')
  code = await runBuilder()
  process.exitCode = code
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
