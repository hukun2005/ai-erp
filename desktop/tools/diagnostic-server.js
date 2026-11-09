'use strict'

// 技术支持端（放在你自己的电脑/服务器上）：接收客户发来的诊断包。
// 用法：
//   node desktop/tools/diagnostic-server.js
// 可选环境变量：
//   DIAG_PORT   监听端口，默认 8799
//   DIAG_TOKEN  接收令牌，设置后客户端必须填写相同令牌
//   DIAG_DIR    保存目录，默认本脚本旁的 diagnostics 目录
//
// 客户端的「诊断接收地址」填：http://你的公网IP:8799/upload
// 局域网内可直接用 http://你的内网IP:8799/upload
const fs = require('node:fs')
const http = require('node:http')
const path = require('node:path')

const PORT = Number(process.env.DIAG_PORT || 8799)
const TOKEN = process.env.DIAG_TOKEN || ''
const DIR = path.resolve(process.env.DIAG_DIR || path.join(__dirname, 'diagnostics'))
const MAX_SIZE = 500 * 1024 * 1024

fs.mkdirSync(DIR, { recursive: true })

function listReports() {
  return fs
    .readdirSync(DIR)
    .filter((name) => name.endsWith('.zip'))
    .map((name) => {
      const stat = fs.statSync(path.join(DIR, name))
      return { name, sizeMB: Math.round((stat.size / 1024 / 1024) * 10) / 10, time: stat.mtime.toLocaleString('zh-CN') }
    })
    .sort((a, b) => b.time.localeCompare(a.time))
    .slice(0, 50)
}

function stamp() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, '0')
  return `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
}

const server = http.createServer((req, res) => {
  if (req.method === 'GET') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
    res.end(JSON.stringify({ ok: true, dir: DIR, tokenRequired: Boolean(TOKEN), reports: listReports() }, null, 2))
    return
  }

  if (req.method !== 'PUT') {
    res.writeHead(405, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('只支持 PUT 上传')
    return
  }

  if (TOKEN && req.headers['x-erp-token'] !== TOKEN) {
    req.resume()
    res.writeHead(401, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('令牌不正确')
    return
  }

  const raw = decodeURIComponent(req.headers['x-erp-filename'] || '') || 'AI-ERP-诊断包.zip'
  const safe = path.basename(raw).replace(/[\\/:*?"<>|]/g, '_')
  const ip = String(req.socket.remoteAddress || 'unknown').replace(/[^0-9a-zA-Z.:-]/g, '')
  const dest = path.join(DIR, `${stamp()}-${ip}-${safe}`)

  let size = 0
  let aborted = false
  const out = fs.createWriteStream(dest)

  req.on('data', (chunk) => {
    size += chunk.length
    if (size > MAX_SIZE && !aborted) {
      aborted = true
      req.destroy()
      out.destroy()
      fs.rm(dest, { force: true }, () => {})
    }
  })
  req.pipe(out)

  out.on('finish', () => {
    if (aborted) return
    console.log(`[${new Date().toLocaleString('zh-CN')}] 收到诊断包 ${dest} (${Math.round(size / 1024)} KB)`)
    res.writeHead(200, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('OK')
  })
  out.on('error', (error) => {
    console.error('保存诊断包失败', error)
    if (!res.headersSent) {
      res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' })
      res.end('保存失败')
    }
  })
})

server.listen(PORT, '0.0.0.0', () => {
  console.log(`诊断包接收服务已启动：http://0.0.0.0:${PORT}/upload`)
  console.log(`保存目录：${DIR}`)
  console.log(`令牌校验：${TOKEN ? '已启用' : '未启用（建议设置 DIAG_TOKEN）'}`)
  console.log('客户端的「诊断接收地址」填写上面的 /upload 地址；公网使用时记得在路由器/防火墙放行端口。')
})
