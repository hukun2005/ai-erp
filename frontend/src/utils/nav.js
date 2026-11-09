/**
 * 导航数据源：权限过滤、业务分组、最近访问。
 * 侧边栏、命令面板、路由守卫共用同一套口径，避免三处各写一遍过滤规则。
 */
import { navGroupMeta, workspaceRoutes } from '../router/routes'

const RECENT_KEY = 'erp.nav.recent'
const RECENT_LIMIT = 5

/** 权限判断：meta.perm 命中即可；只有 meta.permAny 时任一命中即可 */
export function canSeeRoute(route, hasPerm, hasAnyPerm) {
  const { perm, permAny } = route.meta
  if (perm) return hasPerm(perm)
  if (permAny && permAny.length) return hasAnyPerm(permAny)
  return true
}

/** 当前账号可见的路由，保持路由表里的业务顺序 */
export function visibleRoutes(hasPerm, hasAnyPerm) {
  return workspaceRoutes.filter((route) => canSeeRoute(route, hasPerm, hasAnyPerm))
}

/** 路由 → 导航项；path 用绝对路径，便于 router-link 与激活态比较 */
export function toNavItem(route) {
  return {
    name: route.name,
    path: `/${route.path}`,
    title: route.meta.title,
    icon: route.meta.icon,
    desc: route.meta.desc || '',
    group: route.meta.group || '其他',
  }
}

/** 按 meta.group 归组，返回按 navGroupMeta.order 排序的分组数组 */
export function buildNavGroups(routes) {
  const groups = new Map()
  for (const route of routes) {
    const name = route.meta.group || '其他'
    if (!groups.has(name)) {
      groups.set(name, {
        name,
        order: navGroupMeta[name]?.order ?? 99,
        hint: navGroupMeta[name]?.hint || '',
        items: [],
      })
    }
    groups.get(name).items.push(toNavItem(route))
  }
  return [...groups.values()].sort((a, b) => a.order - b.order)
}

/** 最近访问的名字列表；存储损坏时当作空列表，不让导航崩掉 */
export function readRecentNames() {
  try {
    const value = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]')
    return Array.isArray(value) ? value.filter((item) => typeof item === 'string') : []
  } catch {
    return []
  }
}

/** 记录一次访问：同名去重后放到最前，只保留最近 5 条 */
export function rememberVisit(name) {
  if (!name) return
  const list = readRecentNames().filter((item) => item !== name)
  list.unshift(name)
  localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, RECENT_LIMIT)))
}

/** 最近访问对应的导航项；已无权访问或已下线的页面自动跳过 */
export function recentNavItems(routes) {
  return readRecentNames()
    .map((name) => routes.find((route) => route.name === name))
    .filter(Boolean)
    .map(toNavItem)
}

/**
 * 快捷新建入口：从任何页面一步进入开单。
 * perm 是该动作需要的权限，没有权限的入口不展示。
 */
export const QUICK_ACTIONS = [
  { label: '新建销售订单', icon: 'Sell', path: '/sales-orders', query: { new: '1' }, perm: 'sales:manage' },
  {
    label: '新建采购订单',
    icon: 'ShoppingCart',
    path: '/purchase-orders',
    query: { new: '1' },
    perm: 'purchase:manage',
  },
  {
    label: '提采购申请',
    icon: 'DocumentAdd',
    path: '/requisitions',
    query: { new: '1' },
    perm: 'requisition:manage',
  },
  { label: '新建入库单', icon: 'Download', path: '/inbounds', query: { new: '1' }, perm: 'inbound:manage' },
  { label: '新增客户', icon: 'User', path: '/customers', query: { new: '1' }, perm: 'customer:manage' },
  { label: '新增商品', icon: 'Goods', path: '/products', query: { new: '1' }, perm: 'product:manage' },
]

/** 当前账号可用的快捷操作 */
export function availableQuickActions(hasPerm) {
  return QUICK_ACTIONS.filter((action) => hasPerm(action.perm))
}
