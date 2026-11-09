'use strict'

// 把诊断包直接上传到技术支持服务器（PUT 二进制），客户不用自己找文件、发文件。
// 不依赖 Electron，便于用 node 单独验证。
const fs = require('node:fs')
const path = require('node:path')

async function sendReport(zipPath, { url, token }, { timeoutMs = 120000 } = {}) {
  if (!url) throw new Error('未配置诊断接收地址')
  if (!/^https?:\/\//i.test(url)) throw new Error('诊断接收地址需要以 http:// 或 https:// 开头')

  const body = fs.readFileSync(zipPath)
  const headers = {
    'Content-Type': 'application/zip',
    'X-ERP-Filename': encodeURIComponent(path.basename(zipPath)),
    'X-ERP-Size': String(body.length)
  }
  if (token) headers['X-ERP-Token'] = token

  const response = await fetch(url, {
    method: 'PUT',
    headers,
    body,
    signal: AbortSignal.timeout(timeoutMs)
  })
  if (!response.ok) {
    throw new Error(`服务器返回 HTTP ${response.status}`)
  }
  return { status: response.status, size: body.length }
}

module.exports = { sendReport }
