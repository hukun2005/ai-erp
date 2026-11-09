'use strict'

// Electron 主进程：向导 → 启动后端（或连接服务器）→ 打开主窗口；
// 出错时打开诊断页，所有日志与数据都在 userData 目录，升级不丢。
const { app, BrowserWindow, Menu, dialog, ipcMain, shell } = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const path = require('node:path')

app.setName('AI-ERP')

const log = require('./logger')
const configStore = require('./config')
const backend = require('./backend')
const { exportDiagnostics, backupData } = require('./diagnostics')
const { sendReport } = require('./report')
const { checkForUpdates } = require('./updater')

const UI_DIR = path.join(__dirname, '..', 'ui')
const PRELOAD = path.join(__dirname, 'preload.js')

let mainWindow = null
let splashWindow = null
let setupWindow = null
let errorWindow = null
let healthTimer = null

let state = {
  mode: 'local',
  url: '',
  port: null,
  health: null,
  error: '',
  logs: [],
  lastExit: null
}

let healthFailures = 0

// ---------- 工具 ----------

function normalizeServerUrl(value) {
  let url = String(value || '').trim()
  if (!url) return ''
  if (!/^https?:\/\//i.test(url)) url = `http://${url}`
  return url.replace(/\/+$/, '')
}

function dataDirName() {
  return configStore.dataDir()
}

/**
 * 一键把诊断包上传到技术支持服务器；未配置或上传失败时退回桌面的 zip 文件。
 * 返回 { ok, path?, message }，向导/错误页/菜单都复用这里的结果。
 */
async function sendDiagnostics() {
  const config = configStore.readConfig()
  const state = collectState()

  if (!config.diagnosticUrl) {
    const target = exportDiagnostics(state)
    return { ok: false, path: target, message: '未配置诊断接收地址，诊断包已导出到桌面' }
  }

  const tempFile = exportDiagnostics(state, app.getPath('temp'))
  try {
    await sendReport(tempFile, { url: config.diagnosticUrl, token: config.diagnosticToken })
    fs.rmSync(tempFile, { force: true })
    log.info(`诊断包已发送：${config.diagnosticUrl}`)
    return { ok: true, message: '诊断报告已发送给技术支持' }
  } catch (error) {
    const reason = String((error && error.message) || error)
    log.error('发送诊断包失败', error)
    let fallback = ''
    try {
      fallback = path.join(app.getPath('desktop'), path.basename(tempFile))
      fs.copyFileSync(tempFile, fallback)
      fs.rmSync(tempFile, { force: true })
    } catch (copyError) {
      log.warn('诊断包转存桌面失败', copyError)
    }
    return {
      ok: false,
      path: fallback || tempFile,
      message: `发送失败：${reason}${fallback ? '，诊断包已保存到桌面' : ''}`
    }
  }
}

function buildMenu() {
  const template = [
    {
      label: 'AI-ERP',
      submenu: [
        {
          label: '使用模式设置',
          click: () => openSetupWindow()
        },
        {
          label: '检查更新',
          click: () => checkForUpdates({ interactive: true })
        },
        { type: 'separator' },
        {
          label: '允许局域网访问（放行防火墙）',
          click: () => allowFirewall(configStore.readConfig().port)
        },
        { type: 'separator' },
        { role: 'quit', label: '退出' }
      ]
    },
    {
      label: '数据',
      submenu: [
        { label: '打开数据目录', click: () => shell.openPath(dataDirName()) },
        {
          label: '备份数据（数据库 + 上传文件）',
          click: async () => {
            try {
              const target = backupData()
              const answer = await dialog.showMessageBox({
                type: 'info',
                title: '备份完成',
                message: '数据已备份到桌面',
                detail: target,
                buttons: ['打开所在文件夹', '关闭'],
                defaultId: 0
              })
              if (answer.response === 0) shell.showItemInFolder(target)
            } catch (error) {
              log.error('数据备份失败', error)
              dialog.showErrorBox('备份失败', String((error && error.message) || error))
            }
          }
        },
        {
          label: '一键发送诊断给技术支持',
          click: async () => {
            const result = await sendDiagnostics()
            const answer = await dialog.showMessageBox({
              type: result.ok ? 'info' : 'warning',
              title: result.ok ? '发送成功' : '未能发送',
              message: result.message,
              detail: result.path ? `文件：${result.path}` : '',
              buttons: result.path ? ['打开所在文件夹', '关闭'] : ['关闭'],
              defaultId: 0
            })
            if (result.path && answer.response === 0) shell.showItemInFolder(result.path)
          }
        },
        {
          label: '导出诊断包（发给技术支持）',
          click: async () => {
            try {
              const target = exportDiagnostics(collectState())
              const answer = await dialog.showMessageBox({
                type: 'info',
                title: '诊断包已导出',
                message: '请把这个文件发给技术支持',
                detail: target,
                buttons: ['打开所在文件夹', '关闭'],
                defaultId: 0
              })
              if (answer.response === 0) shell.showItemInFolder(target)
            } catch (error) {
              log.error('导出诊断包失败', error)
              dialog.showErrorBox('导出失败', String((error && error.message) || error))
            }
          }
        },
        { label: '打开日志目录', click: () => shell.openPath(configStore.logDir()) }
      ]
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '使用手册',
          click: () => {
            const manual = path.join(process.resourcesPath, 'docs', 'ai-erp-user-manual.pdf')
            if (fs.existsSync(manual)) shell.openPath(manual)
            else dialog.showMessageBox({ type: 'info', message: '安装包内未包含使用手册。' })
          }
        },
        {
          label: '关于',
          click: () =>
            dialog.showMessageBox({
              type: 'info',
              title: '关于 AI-ERP',
              message: `AI-ERP 智能管理系统 ${app.getVersion()}`,
              detail: `数据目录：${configStore.dataDir()}\n日志目录：${configStore.logDir()}`
            })
        }
      ]
    },
    {
      label: '视图',
      submenu: [
        { role: 'reload', label: '刷新' },
        { role: 'togglefullscreen', label: '全屏' },
        { type: 'separator' },
        { role: 'toggleDevTools', label: '开发者工具' }
      ]
    }
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
}

function collectState() {
  return {
    ...state,
    logs: backend.getTail(),
    lastExit: backend.getLastExit()
  }
}

function closeWindow(win) {
  if (win && !win.isDestroyed()) win.close()
}

function openSplash() {
  if (splashWindow && !splashWindow.isDestroyed()) return
  splashWindow = new BrowserWindow({
    width: 420,
    height: 260,
    frame: false,
    resizable: false,
    show: false,
    alwaysOnTop: true,
    backgroundColor: '#0f172a',
    webPreferences: { sandbox: true }
  })
  splashWindow.loadFile(path.join(UI_DIR, 'splash.html'))
  splashWindow.once('ready-to-show', () => splashWindow.show())
}

function closeSplash() {
  closeWindow(splashWindow)
  splashWindow = null
}

function createMainWindow(url, { withPreload = false } = {}) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.focus()
    return mainWindow
  }

  mainWindow = new BrowserWindow({
    width: 1360,
    height: 860,
    minWidth: 1024,
    minHeight: 680,
    show: false,
    backgroundColor: '#f5f7fb',
    title: 'AI-ERP 智能管理系统',
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      preload: withPreload ? PRELOAD : undefined
    }
  })

  mainWindow.once('ready-to-show', () => {
    closeSplash()
    mainWindow.show()
    mainWindow.focus()
    log.info('主窗口已显示')
  })

  mainWindow.on('closed', () => {
    log.info('主窗口已关闭')
  })

  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    log.error(`渲染进程退出：${JSON.stringify(details)}`)
  })

  mainWindow.webContents.setWindowOpenHandler(({ url: target }) => {
    shell.openExternal(target)
    return { action: 'deny' }
  })

  mainWindow.webContents.on('did-fail-load', (_event, code, description, failedUrl, isMainFrame) => {
    if (!isMainFrame || code === -3) return
    log.error(`页面加载失败 code=${code} ${description} url=${failedUrl}`)
    showErrorPage(`无法打开页面：${description || code}\n地址：${failedUrl}`)
  })

  mainWindow.loadURL(url)
  return mainWindow
}

function showErrorPage(message) {
  state.error = message || state.error || '启动失败'
  closeWindow(mainWindow)
  mainWindow = null

  if (errorWindow && !errorWindow.isDestroyed()) {
    errorWindow.webContents.send('error:refresh')
    errorWindow.show()
    return
  }

  errorWindow = new BrowserWindow({
    width: 900,
    height: 640,
    show: false,
    title: 'AI-ERP 启动失败',
    backgroundColor: '#ffffff',
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      preload: PRELOAD
    }
  })
  errorWindow.once('ready-to-show', () => {
    closeSplash()
    errorWindow.show()
  })
  errorWindow.on('closed', () => {
    errorWindow = null
  })
  errorWindow.loadFile(path.join(UI_DIR, 'error.html'))
}

function openSetupWindow() {
  if (setupWindow && !setupWindow.isDestroyed()) {
    setupWindow.focus()
    return
  }
  setupWindow = new BrowserWindow({
    width: 1000,
    height: 760,
    minWidth: 900,
    minHeight: 640,
    show: false,
    title: 'AI-ERP 使用模式设置',
    backgroundColor: '#f5f7fb',
    webPreferences: {
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      preload: PRELOAD
    }
  })
  setupWindow.once('ready-to-show', () => {
    closeSplash()
    setupWindow.show()
  })
  setupWindow.on('closed', () => {
    setupWindow = null
  })
  setupWindow.loadFile(path.join(UI_DIR, 'setup.html'))
}

function relaunch(delayMs = 400) {
  setTimeout(() => {
    app.relaunch()
    app.exit(0)
  }, delayMs)
}

async function launch(config) {
  state = { mode: config.mode, url: '', port: null, health: null, error: '', logs: [], lastExit: null }
  healthFailures = 0

  if (config.mode === 'remote') {
    const url = normalizeServerUrl(config.serverUrl)
    if (!url) {
      openSetupWindow()
      return
    }
    state.url = url
    openSplash()
    const health = await probeHealth(url, 12000)
    if (!health.ok) {
      showErrorPage(
        `无法连接服务器：${url}\n${health.error || ''}\n\n` +
          '请确认服务器电脑已打开 AI-ERP（且已勾选“允许局域网访问”），并检查两台电脑是否在同一网络。'
      )
      return
    }
    state.health = health.data
    createMainWindow(url)
    return
  }

  openSplash()
  try {
    configStore.ensureEnvFromConfig(config)
    const { port } = await backend.startBackend(config)
    state.port = port
    const health = await backend.waitForHealth(port)
    state.health = health
    state.url = `http://127.0.0.1:${port}`
    createMainWindow(state.url, { withPreload: false })
    startHealthMonitor()
  } catch (error) {
    log.error('本机后端启动失败', error)
    state.error = String((error && error.message) || error)
    state.logs = backend.getTail()
    state.lastExit = backend.getLastExit()
    showErrorPage(state.error)
  }
}

function startHealthMonitor() {
  if (healthTimer) clearInterval(healthTimer)
  healthTimer = setInterval(async () => {
    if (!backend.isRunning() || !state.port) return
    try {
      const response = await fetch(`http://127.0.0.1:${state.port}/api/health`, {
        signal: AbortSignal.timeout(3000)
      })
      if (response.ok) {
        healthFailures = 0
        return
      }
      healthFailures += 1
    } catch {
      healthFailures += 1
    }
    if (healthFailures === 2) {
      log.warn('后端健康检查连续失败')
      const answer = await dialog.showMessageBox({
        type: 'warning',
        title: '后台服务异常',
        message: '本机后台服务没有响应，部分功能可能无法使用。',
        detail: '可以先尝试重启应用；如果反复出现，请导出诊断包发给技术支持。',
        buttons: ['重启应用', '导出诊断包', '继续等待'],
        defaultId: 0,
        cancelId: 2
      })
      if (answer.response === 0) relaunch()
      if (answer.response === 1) {
        try {
          const target = exportDiagnostics(collectState())
          shell.showItemInFolder(target)
        } catch (error) {
          dialog.showErrorBox('导出失败', String((error && error.message) || error))
        }
      }
    }
  }, 30000)
}

async function probeHealth(url, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs
  let error = ''
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(2500) })
      if (response.ok) return { ok: true, data: await response.json() }
      error = `服务器返回 HTTP ${response.status}`
    } catch (e) {
      error = String((e && e.message) || e)
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  return { ok: false, error }
}

function allowFirewall(port) {
  return new Promise((resolve) => {
    const rule = `AI-ERP ${port}`
    log.info(`尝试放行防火墙端口 ${port}`)
    // 需要管理员权限：弹 UAC 让用户确认，失败就提示手动放行
    const script =
      "Start-Process -FilePath netsh -ArgumentList 'advfirewall firewall add rule " +
      `name=\"${rule}\" dir=in action=allow protocol=TCP localport=${port}' -Verb RunAs -Wait`
    const child = spawn('powershell.exe', ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', script], {
      windowsHide: true
    })
    child.on('error', (error) => {
      log.warn('防火墙放行失败', error)
      resolve(false)
    })
    child.on('exit', (code) => {
      log.info(`防火墙放行退出码 ${code}`)
      resolve(code === 0)
    })
  })
}

// ---------- IPC ----------

function registerIpc() {
  ipcMain.handle('setup:get', () => {
    const config = configStore.readConfig()
    return {
      config,
      dataDir: configStore.dataDir(),
      logDir: configStore.logDir(),
      appVersion: app.getVersion(),
      isPackaged: app.isPackaged,
      defaultPort: configStore.DEFAULT_PORT
    }
  })

  ipcMain.handle('setup:save', async (_event, payload) => {
    const next = {
      mode: payload && payload.mode === 'remote' ? 'remote' : 'local',
      serverUrl: normalizeServerUrl(payload && payload.serverUrl),
      port: Number((payload && payload.port) || configStore.DEFAULT_PORT),
      bindLan: Boolean(payload && payload.bindLan),
      llmApiKey: String((payload && payload.llmApiKey) || '').trim(),
      llmBaseUrl: String((payload && payload.llmBaseUrl) || '').trim(),
      llmModel: String((payload && payload.llmModel) || '').trim(),
      updateUrl: String((payload && payload.updateUrl) || '').trim(),
      autoUpdate: payload ? Boolean(payload.autoUpdate) : true,
      diagnosticUrl: normalizeServerUrl(payload && payload.diagnosticUrl),
      diagnosticToken: String((payload && payload.diagnosticToken) || '').trim(),
      setupDone: true
    }

    if (next.mode === 'remote' && !next.serverUrl) {
      return { ok: false, message: '请填写服务器地址，例如 http://192.168.1.10:8790' }
    }
    if (next.mode === 'local' && (!Number.isInteger(next.port) || next.port < 1024 || next.port > 65535)) {
      return { ok: false, message: '端口必须是 1024~65535 之间的整数' }
    }

    configStore.writeConfig(next)
    configStore.ensureEnvFromConfig(next)
    log.info(`配置已保存：${JSON.stringify(configStore.maskConfig(next))}`)

    if (next.mode === 'local' && next.bindLan) {
      await allowFirewall(next.port)
    }

    relaunch(120)
    return { ok: true }
  })

  ipcMain.handle('setup:test-server', async (_event, url) => {
    const target = normalizeServerUrl(url)
    if (!target) return { ok: false, message: '请先填写服务器地址' }
    const health = await probeHealth(target, 10000)
    if (!health.ok) return { ok: false, message: health.error || '连接失败' }
    return { ok: true, message: `连接成功：${health.data.app || 'AI-ERP'} v${health.data.version || ''}` }
  })

  ipcMain.handle('setup:quit', () => {
    app.quit()
    return true
  })

  ipcMain.handle('app:info', () => ({
    appVersion: app.getVersion(),
    dataDir: configStore.dataDir(),
    logDir: configStore.logDir(),
    mode: state.mode,
    url: state.url,
    port: state.port
  }))

  ipcMain.handle('app:get-error', () => collectState())

  ipcMain.handle('app:retry', () => {
    relaunch()
    return true
  })

  ipcMain.handle('app:open-logs', () => shell.openPath(configStore.logDir()))

  ipcMain.handle('app:open-data', () => shell.openPath(configStore.dataDir()))

  ipcMain.handle('app:open-setup', () => {
    openSetupWindow()
    return true
  })

  ipcMain.handle('app:export-diagnostics', () => {
    try {
      const target = exportDiagnostics(collectState())
      shell.showItemInFolder(target)
      return { ok: true, path: target }
    } catch (error) {
      log.error('导出诊断包失败', error)
      return { ok: false, message: String((error && error.message) || error) }
    }
  })

  ipcMain.handle('app:send-diagnostics', () => sendDiagnostics())

  ipcMain.handle('app:check-update', () => {
    checkForUpdates({ interactive: true })
    return true
  })
}

// ---------- 生命周期 ----------

const gotLock = app.requestSingleInstanceLock()
if (!gotLock) {
  app.quit()
} else {
  app.on('second-instance', () => {
    const win = mainWindow || setupWindow || errorWindow
    if (win && !win.isDestroyed()) {
      if (win.isMinimized()) win.restore()
      win.focus()
    }
  })

  app.whenReady().then(async () => {
    configStore.ensureDirs()
    registerIpc()
    buildMenu()

    const config = configStore.readConfig()
    log.info(`应用启动 v${app.getVersion()} mode=${config.mode} packaged=${app.isPackaged}`)

    if (!config.setupDone) {
      openSetupWindow()
      return
    }

    await launch(config)

    if (config.autoUpdate) {
      setTimeout(() => checkForUpdates({ interactive: false }), 30000)
    }
  })

  app.on('before-quit', () => {
    log.info('应用准备退出')
    if (healthTimer) clearInterval(healthTimer)
    backend.stopBackend()
  })

  app.on('window-all-closed', () => {
    log.info('所有窗口已关闭，退出应用')
    app.quit()
  })
}
