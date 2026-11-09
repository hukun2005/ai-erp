'use strict'

// 自动更新：安装包里只带 build 时写入的占位更新源，真正地址在客户 config.json
// 的 updateUrl（本机模式一般指向你自己的 HTTPS 静态目录）。更新下载完成后
// 先自动做一份数据备份，避免新版本改表结构时把老数据弄坏。
const { app, dialog } = require('electron')
const { autoUpdater } = require('electron-updater')

const log = require('./logger')
const { readConfig } = require('./config')
const { backupData } = require('./diagnostics')

let initialized = false
let interactiveCheck = false
let downloading = false

function normalizeUrl(url) {
  return String(url || '').trim().replace(/\/+$/, '')
}

function init() {
  if (initialized) return true
  initialized = true
  autoUpdater.autoDownload = false
  autoUpdater.autoInstallOnAppQuit = true
  autoUpdater.logger = log

  autoUpdater.on('update-available', async (info) => {
    log.info(`发现新版本 ${info.version}`)
    downloading = true
    const answer = await dialog.showMessageBox({
      type: 'info',
      title: '发现新版本',
      message: `发现新版本 ${info.version}，是否现在下载？`,
      detail: '下载在后台进行，不影响当前使用；下载完成后会提示重启安装。',
      buttons: ['立即下载', '稍后'],
      defaultId: 0,
      cancelId: 1
    })
    if (answer.response === 0) {
      autoUpdater.downloadUpdate().catch((error) => {
        downloading = false
        log.error('下载更新失败', error)
        dialog.showErrorBox('更新下载失败', String(error.message || error))
      })
    } else {
      downloading = false
    }
  })

  autoUpdater.on('update-not-available', () => {
    downloading = false
    if (interactiveCheck) {
      dialog.showMessageBox({ type: 'info', title: '检查更新', message: '当前已是最新版本' })
    }
    interactiveCheck = false
  })

  autoUpdater.on('update-downloaded', async (info) => {
    downloading = false
    let backupPath = ''
    try {
      backupPath = backupData()
    } catch (error) {
      log.warn('更新前数据备份失败', error)
    }
    const answer = await dialog.showMessageBox({
      type: 'info',
      title: '更新已下载',
      message: `新版本 ${info.version} 已下载完成，是否立即重启安装？`,
      detail: backupPath
        ? `安装前已自动备份数据：\n${backupPath}\n\n选择「稍后」会在退出应用时安装。`
        : '选择「稍后」会在退出应用时安装。',
      buttons: ['立即重启安装', '退出时安装'],
      defaultId: 0,
      cancelId: 1
    })
    if (answer.response === 0) {
      setImmediate(() => autoUpdater.quitAndInstall(false, true))
    }
  })

  autoUpdater.on('error', (error) => {
    downloading = false
    log.error('自动更新失败', error)
    if (interactiveCheck) {
      dialog.showErrorBox('更新检查失败', String((error && error.message) || error))
    }
    interactiveCheck = false
  })

  return true
}

async function checkForUpdates({ interactive = false } = {}) {
  if (!app.isPackaged) {
    if (interactive) {
      dialog.showMessageBox({
        type: 'info',
        title: '检查更新',
        message: '开发模式不检查更新，请安装正式安装包后测试。'
      })
    }
    return
  }

  const config = readConfig()
  const url = normalizeUrl(config.updateUrl)
  if (!url) {
    if (interactive) {
      dialog.showMessageBox({
        type: 'warning',
        title: '尚未配置更新源',
        message: '当前安装包没有配置更新地址。',
        detail:
          '请在 userData/config.json 里填写 updateUrl（例如 https://你的服务器/ai-erp/），' +
          '并把 electron-builder 生成的 latest.yml 和安装包一起上传到该目录。'
      })
    }
    return
  }

  if (!init() || downloading) return
  interactiveCheck = interactive
  try {
    log.info(`检查更新：${url}`)
    autoUpdater.setFeedURL({ provider: 'generic', url })
    await autoUpdater.checkForUpdates()
  } catch (error) {
    log.error('检查更新异常', error)
    if (interactive) {
      dialog.showErrorBox('检查更新失败', String((error && error.message) || error))
    }
    interactiveCheck = false
  }
}

module.exports = { checkForUpdates }
