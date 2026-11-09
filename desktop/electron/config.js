'use strict'

// 桌面端配置：全部放在 %APPDATA%\AI-ERP\config.json，安装目录只读，
// 升级时不会被覆盖；客户数据（数据库/上传文件/日志）也在同一目录下。
const { app } = require('electron')
const crypto = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')

const DEFAULT_PORT = 8790

// 打包时预置的默认值（大模型 Key 等），由 scripts/build.ps1 从 backend/.env 生成
let presets = {}
try {
  presets = require('./defaults.json')
} catch {
  presets = {}
}

const DEFAULTS = {
  mode: 'local', // local=本机运行，数据存客户电脑；remote=连接已有服务器
  serverUrl: '',
  port: DEFAULT_PORT,
  bindLan: false, // 本机模式是否允许局域网其他电脑访问
  llmApiKey: String(presets.llmApiKey || ''),
  llmBaseUrl: String(presets.llmBaseUrl || ''),
  llmModel: String(presets.llmModel || ''),
  updateUrl: '', // 更新源（放 latest.yml 的静态目录）
  autoUpdate: true,
  diagnosticUrl: 'https://wine-clocks-glenn-also.trycloudflare.com/upload', // 公网诊断接收地址（Cloudflare 临时隧道，重启隧道后地址会变）
  diagnosticToken: '7fb18a3ada757924eb95b740d59d33fe', // 接收端校验令牌，与 tools/start-diagnostic-server.ps1 一致
  setupDone: true // 安装后直接以本机模式启动，不再弹使用模式向导
}

function configPath() {
  return path.join(app.getPath('userData'), 'config.json')
}

function dataDir() {
  return path.join(app.getPath('userData'), 'data')
}

function logDir() {
  return path.join(app.getPath('userData'), 'logs')
}

function ensureDirs() {
  for (const dir of [app.getPath('userData'), dataDir(), logDir()]) {
    fs.mkdirSync(dir, { recursive: true })
  }
}

function readConfig() {
  try {
    const raw = JSON.parse(fs.readFileSync(configPath(), 'utf8'))
    return { ...DEFAULTS, ...raw }
  } catch {
    return { ...DEFAULTS }
  }
}

function writeConfig(patch) {
  ensureDirs()
  const next = { ...readConfig(), ...patch }
  fs.writeFileSync(configPath(), JSON.stringify(next, null, 2), 'utf8')
  return next
}

function maskConfig(config) {
  const masked = { ...config }
  if (masked.llmApiKey) {
    masked.llmApiKey = `${String(masked.llmApiKey).slice(0, 4)}****${String(masked.llmApiKey).slice(-4)}`
  }
  return masked
}

// ---- .env：由向导写入数据目录，后端启动时读取 ----

function envPath() {
  return path.join(dataDir(), '.env')
}

function readEnv() {
  const file = envPath()
  if (!fs.existsSync(file)) return {}
  const result = {}
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const match = line.match(/^([A-Z0-9_]+)\s*=\s*(.*)$/)
    if (match) result[match[1]] = match[2]
  }
  return result
}

function writeEnv(entries) {
  ensureDirs()
  const current = readEnv()
  Object.assign(current, entries)
  const body = Object.entries(current)
    .filter(([, value]) => value !== undefined && value !== null)
    .map(([key, value]) => `${key}=${value}`)
    .join('\n')
  fs.writeFileSync(envPath(), `${body}\n`, 'utf8')
}

/** 首次运行生成一份最小 .env：随机 JWT 密钥 + 向导里填的大模型配置。 */
function ensureEnvFromConfig(config) {
  const current = readEnv()
  const patch = {}
  if (!current.SECRET_KEY) {
    patch.SECRET_KEY = crypto.randomBytes(32).toString('hex')
  }
  // API Key 允许留空（清空即关闭 AI 功能），端点与模型留空则用后端默认值
  patch.LLM_API_KEY = String(config.llmApiKey || '')
  if (config.llmBaseUrl) patch.LLM_BASE_URL = config.llmBaseUrl
  if (config.llmModel) patch.LLM_MODEL = config.llmModel
  writeEnv(patch)
  return envPath()
}

module.exports = {
  DEFAULT_PORT,
  DEFAULTS,
  configPath,
  dataDir,
  logDir,
  ensureDirs,
  readConfig,
  writeConfig,
  maskConfig,
  envPath,
  readEnv,
  writeEnv,
  ensureEnvFromConfig
}
