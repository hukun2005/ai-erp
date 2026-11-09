/** 用 Blob 打开 PDF 预览：PDF 请求需要带鉴权头，不能直接 window.open 后端地址。 */
export function openPdfBlob(blob) {
  if (!blob || !blob.size) return false
  const url = URL.createObjectURL(new Blob([blob], { type: 'application/pdf' }))
  const win = window.open(url, '_blank')
  // 新标签页被拦截时退回下载，避免用户点了没反应
  if (!win) {
    const link = document.createElement('a')
    link.href = url
    link.download = 'print.pdf'
    document.body.appendChild(link)
    link.click()
    link.remove()
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000)
  return true
}
