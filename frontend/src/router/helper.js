import { can } from '../utils/auth'
import { canSeeRoute } from '../utils/nav'
import { workspaceRoutes } from './routes'

/** 当前账号可进入的路由；传入 perms 时按该权限列表判断（用户信息刷新前使用） */
export function accessibleRoutes(perms) {
  const hasPerm = perms ? (perm) => perms.includes('*') || perms.includes(perm) : can
  const hasAnyPerm = perms
    ? (list) => (list || []).some((perm) => perms.includes('*') || perms.includes(perm))
    : (list) => (list || []).some((perm) => can(perm))
  return workspaceRoutes.filter((route) => canSeeRoute(route, hasPerm, hasAnyPerm))
}

export function firstAccessiblePath(perms) {
  const list = accessibleRoutes(perms)
  return list.length ? `/${list[0].path}` : null
}

export function defaultPath() {
  return firstAccessiblePath() || '/dashboard'
}
