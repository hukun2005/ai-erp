'use strict'

const path = require('node:path')
const log = require('electron-log')
const { logDir } = require('./config')

// 主进程日志落盘，客户报错时连同 backend.log 一起导出诊断包
log.transports.file.resolvePathFn = () => path.join(logDir(), 'main.log')
log.transports.file.maxSize = 10 * 1024 * 1024
log.transports.file.level = 'info'
log.transports.console.level = 'info'

if (log.errorHandler && typeof log.errorHandler.startCatching === 'function') {
  log.errorHandler.startCatching({ showDialog: false })
}

module.exports = log
