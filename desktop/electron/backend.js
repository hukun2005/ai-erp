'use strict'

// 后端子进程管理：定位可执行文件、挑端口、拉起进程、等健康检查、退出时清场。
const { app } = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const net = require('node:net')
const path = require('node:path')

const log = require('./logger')
const { dataDir } = require('./config')

let child = null
let currentPort = null
let tail = []
let lastExit = null
let stopping = false
const TAIL_LIMIT = 300

function backendDir() {
  if (process.env.AI_ERP_BACKEND_DIR) return process.env.AI_ERP_BACKEND_DIR
  if (app.isPackaged) return path.join(process.resourcesPath, 'backend')
  return path.join(__dirname, '..', '..', 'backend', 'dist', 'erp-server')
}

function backendExe() {
  return path.join(backendDir(), process.platform === 'win32' ? 'erp-server.exe' : 'erp-server')
}

function isPortFree(port) {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.once('error', () => resolve(false))
    server.once('listening', () => {
      server.close(() => resolve(true))
    })
    server.listen(port, '127.0.0.1')
  })
}

async function pickPort(preferred, allowShift) {
  const start = Number(preferred) || 8790
  const limit = allowShift ? start + 20 : start
  for (let port = start; port <= limit; port += 1) {
    if (await isPortFree(port)) return port
  }
  throw new Error(`端口 ${start}${allowShift ? `~${limit}` : ''} 已被其他程序占用`)
}

function pushTail(line) {
  tail.push(line)
  if (tail.length > TAIL_LIMIT) tail.shift()
}

function getTail() {
  return tail.slice(-80)
}

function getLastExit() {
  return lastExit
}

function startBackend(config) {
  return (async () => {
    const exe = backendExe()
    if (!fs.existsSync(exe)) {
      throw new Error(
        `未找到后端程序：${exe}\n请在项目根目录执行 desktop\\scripts\\build.ps1 重新打包。`
      )
    }

    // 服务端模式端口必须固定（客户端按这个端口连），本机模式占用时可自动顺延
    const port = await pickPort(config.port, config.mode === 'local' && !config.bindLan)
    const host = config.bindLan ? '0.0.0.0' : '127.0.0.1'
    currentPort = port
    tail = []
    lastExit = null
    stopping = false

    log.info(`启动后端：${exe} host=${host} port=${port} data=${dataDir()}`)

    child = spawn(exe, [], {
      cwd: backendDir(),
      windowsHide: true,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: {
        ...process.env,
        AI_ERP_DATA_DIR: dataDir(),
        AI_ERP_HOST: host,
        AI_ERP_PORT: String(port),
        PYTHONUNBUFFERED: '1',
        PYTHONIOENCODING: 'utf-8'
      }
    })

    const onLine = (stream) => (chunk) => {
      for (const line of String(chunk).split(/\r?\n/)) {
        const text = line.trim()
        if (!text) continue
        pushTail(`[${stream}] ${text}`)
        log.info(`[backend:${stream}] ${text}`)
      }
    }
    child.stdout.on('data', onLine('out'))
    child.stderr.on('data', onLine('err'))

    child.on('error', (error) => {
      lastExit = { code: null, error: String(error && error.message) }
      pushTail(`[spawn] ${lastExit.error}`)
      log.error('后端子进程启动失败', error)
    })

    child.on('exit', (code, signal) => {
      if (stopping) return
      lastExit = { code, signal }
      pushTail(`[exit] 后端进程退出 code=${code} signal=${signal}`)
      log.warn(`后端进程退出 code=${code} signal=${signal}`)
    })

    return { port, host }
  })()
}

async function waitForHealth(port, timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs
  let lastError = ''

  while (Date.now() < deadline) {
    if (!child) throw new Error('后端进程未启动')
    if (child.exitCode !== null) {
      throw new Error(`后端进程已退出（code=${child.exitCode}）`)
    }
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/health`, {
        signal: AbortSignal.timeout(2500)
      })
      if (response.ok) {
        const health = await response.json()
        log.info(`后端就绪：${JSON.stringify(health)}`)
        return health
      }
      lastError = `健康检查返回 HTTP ${response.status}`
    } catch (error) {
      lastError = String((error && error.message) || error)
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }

  throw new Error(`后端在 ${Math.round(timeoutMs / 1000)} 秒内没有就绪：${lastError}`)
}

function stopBackend() {
  stopping = true
  const target = child
  child = null
  if (!target || target.exitCode !== null) return
  try {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(target.pid), '/T', '/F'], { windowsHide: true })
    } else {
      target.kill('SIGTERM')
    }
  } catch (error) {
    log.warn('停止后端进程失败', error)
  }
}

function isRunning() {
  return Boolean(child && child.exitCode === null)
}

function getPort() {
  return currentPort
}

module.exports = {
  backendExe,
  startBackend,
  waitForHealth,
  stopBackend,
  isRunning,
  getPort,
  getTail,
  getLastExit
}
