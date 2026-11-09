'use strict'

// 只给本地页面（向导/错误页）暴露有限的 IPC，主窗口加载业务页面时不注入，
// 避免把桌面能力暴露给远端内容。
const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('erp', {
  setupGet: () => ipcRenderer.invoke('setup:get'),
  setupSave: (payload) => ipcRenderer.invoke('setup:save', payload),
  testServer: (url) => ipcRenderer.invoke('setup:test-server', url),
  quit: () => ipcRenderer.invoke('setup:quit'),

  getError: () => ipcRenderer.invoke('app:get-error'),
  retry: () => ipcRenderer.invoke('app:retry'),
  openLogs: () => ipcRenderer.invoke('app:open-logs'),
  openData: () => ipcRenderer.invoke('app:open-data'),
  openSetup: () => ipcRenderer.invoke('app:open-setup'),
  exportDiagnostics: () => ipcRenderer.invoke('app:export-diagnostics'),
  sendDiagnostics: () => ipcRenderer.invoke('app:send-diagnostics'),
  appInfo: () => ipcRenderer.invoke('app:info'),

  onErrorRefresh: (callback) => {
    const handler = () => callback()
    ipcRenderer.on('error:refresh', handler)
    return () => ipcRenderer.removeListener('error:refresh', handler)
  }
})
