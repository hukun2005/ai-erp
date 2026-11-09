'use strict'

// 客户报错时最怕「只有一句打不开」。这里把日志、配置、数据库和运行环境
// 打进一个 zip，客户点一下就能发回来。
const { app } = require('electron')
const AdmZip = require('adm-zip')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const log = require('./logger')
const { dataDir, logDir, maskConfig, readConfig, readEnv } = require('./config')

function stamp() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    '-',
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds())
  ].join('')
}

function desktopDir() {
  try {
    return app.getPath('desktop')
  } catch {
    return app.getPath('userData')
  }
}

function addIfExists(zip, file, entryName) {
  try {
    if (fs.existsSync(file) && fs.statSync(file).isFile()) {
      zip.addLocalFile(file, '', entryName)
    }
  } catch (error) {
    log.warn(`诊断包添加文件失败：${file}`, error)
  }
}

function addLogs(zip, prefix) {
  for (const dir of [logDir(), path.join(dataDir(), 'logs')]) {
    if (!fs.existsSync(dir)) continue
    for (const name of fs.readdirSync(dir)) {
      if (!name.endsWith('.log')) continue
      addIfExists(zip, path.join(dir, name), path.posix.join(prefix, name))
    }
  }
}

function systemInfo(state) {
  const health = state && state.health ? JSON.stringify(state.health, null, 2) : '无（后端未就绪）'
  const tail = state && state.logs && state.logs.length ? state.logs.join('\n') : '无'
  const lastExit = state && state.lastExit ? JSON.stringify(state.lastExit) : '无'
  return [
    '=== AI-ERP 诊断信息 ===',
    `时间：${new Date().toLocaleString('zh-CN')}`,
    `应用版本：${app.getVersion()}`,
    `Electron：${process.versions.electron}  Node：${process.versions.node}  Chromium：${process.versions.chrome}`,
    `系统：${os.type()} ${os.release()} ${os.arch()}  主机名=${os.hostname()}`,
    `运行模式：${state && state.mode}`,
    `服务地址：${state && state.url}`,
    `后端端口：${state && state.port}`,
    `用户数据目录：${app.getPath('userData')}`,
    `后端进程退出信息：${lastExit}`,
    '',
    '=== 健康检查 ===',
    health,
    '',
    '=== 后端最近输出 ===',
    tail
  ].join('\n')
}

function diagnosticFileName() {
  return `AI-ERP-诊断包-${stamp()}.zip`
}

function exportDiagnostics(state, targetDir) {
  const zip = new AdmZip()

  addLogs(zip, 'logs')
  addIfExists(zip, path.join(app.getPath('userData'), 'config.json'), 'config.json')
  addIfExists(zip, path.join(dataDir(), 'erp.db'), path.posix.join('data', 'erp.db'))

  const env = readEnv()
  const safeEnv = Object.entries(env)
    .map(([key, value]) => {
      if (/KEY|SECRET|TOKEN|PASSWORD/.test(key) && value) {
        return `${key}=****（已隐藏）`
      }
      return `${key}=${value}`
    })
    .join('\n')
  zip.addFile('env.txt', Buffer.from(safeEnv || '（.env 不存在）', 'utf8'))
  zip.addFile(
    'system.txt',
    Buffer.from(systemInfo(state), 'utf8')
  )
  zip.addFile(
    'config-masked.json',
    Buffer.from(JSON.stringify(maskConfig(readConfig()), null, 2), 'utf8')
  )

  const target = path.join(targetDir || desktopDir(), diagnosticFileName())
  zip.writeZip(target)
  log.info(`诊断包已导出：${target}`)
  return target
}

function backupData() {
  const zip = new AdmZip()
  const root = dataDir()
  const db = path.join(root, 'erp.db')
  addIfExists(zip, db, 'erp.db')
  addIfExists(zip, path.join(root, 'erp.db-wal'), 'erp.db-wal')
  addIfExists(zip, path.join(root, 'erp.db-shm'), 'erp.db-shm')
  addIfExists(zip, path.join(root, '.env'), '.env')

  const uploads = path.join(root, 'uploads')
  if (fs.existsSync(uploads)) {
    zip.addLocalFolder(uploads, 'uploads')
  }

  const target = path.join(desktopDir(), `AI-ERP-数据备份-${stamp()}.zip`)
  zip.writeZip(target)
  log.info(`数据备份已导出：${target}`)
  return target
}

module.exports = { exportDiagnostics, backupData, diagnosticFileName }
