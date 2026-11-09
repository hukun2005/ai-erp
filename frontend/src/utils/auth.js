/** 从本地缓存读取当前用户（供路由守卫等非组件场景使用） */
export function getStoredUser() {
  try {
    return JSON.parse(localStorage.getItem('erp_user') || 'null')
  } catch (err) {
    return null
  }
}

export function can(perm) {
  if (!perm) return true
  const user = getStoredUser()
  const perms = user?.permissions || []
  return perms.includes('*') || perms.includes(perm)
}

export function canAny(perms) {
  return (perms || []).some((p) => can(p))
}
