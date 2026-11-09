/** 触发浏览器下载 Blob：用于导出模板等需要鉴权头、不能用 <a href> 直接打开的场景。 */
export function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}
