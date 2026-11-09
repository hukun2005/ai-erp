<script setup>
/**
 * 应用外壳：桌面侧栏 / 窄屏抽屉 + 顶栏（面包屑、全局搜索、快捷新建、AI、用户）。
 * 导航数据统一来自 utils/nav，权限过滤与路由守卫保持同一口径。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { useUserStore } from '../stores/user'
import { inboxApi } from '../api'
import { availableQuickActions, buildNavGroups, recentNavItems, rememberVisit, visibleRoutes } from '../utils/nav'
import CommandPalette from '../components/CommandPalette.vue'
import SideNav from '../components/SideNav.vue'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const NARROW_BREAKPOINT = 1200
const COLLAPSED_KEY = 'erp.nav.collapsed'

const isNarrow = ref(window.innerWidth < NARROW_BREAKPOINT)
const drawerOpen = ref(false)
const collapsed = ref(localStorage.getItem(COLLAPSED_KEY) === '1')
// 最近访问存在 localStorage，用版本号触发重新计算
const recentVersion = ref(0)

const unreadCount = ref(0)
let unreadTimer = null

async function loadUnread() {
  if (!userStore.token) return
  try {
    const data = await inboxApi.unreadCount()
    unreadCount.value = data.unread || 0
  } catch {
    // 未读数读取失败不阻断外壳渲染
  }
}

const routes = computed(() => visibleRoutes(userStore.hasPerm, userStore.hasAnyPerm))
const groups = computed(() => buildNavGroups(routes.value))
const recent = computed(() => {
  recentVersion.value
  return recentNavItems(routes.value)
})
const currentRoute = computed(() => routes.value.find((item) => `/${item.path}` === route.path))
const currentGroupName = computed(() => currentRoute.value?.meta.group || '工作台')
const currentTitle = computed(() => route.meta.title || currentRoute.value?.meta.title || '经营看板')

// 快捷新建：从任何页面一步进入开单，权限不足的入口直接不显示
const quickActions = computed(() => availableQuickActions(userStore.hasPerm))

const navToggleLabel = computed(() => {
  if (isNarrow.value) return drawerOpen.value ? '关闭导航' : '打开导航'
  return collapsed.value ? '展开侧栏' : '收起侧栏'
})

function toggleNav() {
  if (isNarrow.value) {
    drawerOpen.value = !drawerOpen.value
    return
  }
  collapsed.value = !collapsed.value
  localStorage.setItem(COLLAPSED_KEY, collapsed.value ? '1' : '0')
}

function onResize() {
  const narrow = window.innerWidth < NARROW_BREAKPOINT
  isNarrow.value = narrow
  if (!narrow) drawerOpen.value = false
}

function onKeydown(event) {
  if (event.key === 'Escape' && drawerOpen.value) drawerOpen.value = false
}

function openPalette() {
  window.dispatchEvent(new CustomEvent('erp-command-palette'))
}

function goQuick(action) {
  router.push({ path: action.path, query: action.query })
}

watch(
  () => route.name,
  (name) => {
    if (!name) return
    rememberVisit(name)
    recentVersion.value += 1
    if (isNarrow.value) drawerOpen.value = false
  },
  { immediate: true },
)

onMounted(() => {
  window.addEventListener('resize', onResize)
  window.addEventListener('keydown', onKeydown)
  if (userStore.token) userStore.refresh().catch(() => {})
  loadUnread()
  unreadTimer = setInterval(loadUnread, 30000)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  window.removeEventListener('keydown', onKeydown)
  if (unreadTimer) clearInterval(unreadTimer)
})

async function handleLogout() {
  await ElMessageBox.confirm('确定要退出登录吗？', '提示', { type: 'warning' })
  userStore.logout()
  router.push('/login')
}
</script>

<template>
  <div class="layout" :class="{ narrow: isNarrow }">
    <aside v-if="!isNarrow" class="sidebar" :style="{ width: collapsed ? '64px' : '216px' }">
      <div class="brand" :class="{ compact: collapsed }">
        <div class="brand-mark">AI</div>
        <div v-if="!collapsed" class="brand-text">
          <strong>AI-ERP</strong>
          <span>智能管理系统</span>
        </div>
      </div>
      <SideNav
        :groups="groups"
        :recent="recent"
        :active-path="route.path"
        :collapsed="collapsed"
      />
    </aside>

    <template v-if="isNarrow">
      <div class="drawer-mask" :class="{ open: drawerOpen }" @click.self="drawerOpen = false">
        <aside class="drawer" role="dialog" aria-label="主导航">
          <div class="brand">
            <div class="brand-mark">AI</div>
            <div class="brand-text">
              <strong>AI-ERP</strong>
              <span>智能管理系统</span>
            </div>
            <button type="button" class="drawer-close" aria-label="关闭导航" @click="drawerOpen = false">
              <el-icon><Close /></el-icon>
            </button>
          </div>
          <SideNav :groups="groups" :recent="recent" :active-path="route.path" />
        </aside>
      </div>
    </template>

    <div class="workspace">
      <header class="topbar">
        <div class="topbar-left">
          <button type="button" class="icon-btn" :aria-label="navToggleLabel" :title="navToggleLabel" @click="toggleNav">
            <el-icon v-if="isNarrow">
              <Close v-if="drawerOpen" />
              <Operation v-else />
            </el-icon>
            <el-icon v-else>
              <Expand v-if="collapsed" />
              <Fold v-else />
            </el-icon>
          </button>
          <nav class="crumbs" aria-label="当前位置">
            <span class="crumb-group">{{ currentGroupName }}</span>
            <el-icon class="crumb-sep"><ArrowRight /></el-icon>
            <span class="crumb-current">{{ currentTitle }}</span>
          </nav>
        </div>

        <div class="topbar-right">
          <router-link
            to="/inbox"
            class="bell-btn"
            :aria-label="`消息中心（${unreadCount} 条未读）`"
            :title="`消息中心（${unreadCount} 条未读）`"
          >
            <el-badge :value="unreadCount" :hidden="unreadCount === 0" :max="99">
              <el-icon><Bell /></el-icon>
            </el-badge>
          </router-link>

          <button type="button" class="search-btn" aria-label="全局搜索（Ctrl+K）" @click="openPalette">
            <el-icon><Search /></el-icon>
            <span class="search-text">搜索</span>
            <kbd>Ctrl K</kbd>
          </button>

          <el-dropdown v-if="quickActions.length" trigger="click" placement="bottom-end" @command="goQuick">
            <button type="button" class="create-btn">
              <el-icon><Plus /></el-icon>
              <span>新建</span>
            </button>
            <template #dropdown>
              <el-dropdown-menu class="quick-menu">
                <el-dropdown-item v-for="action in quickActions" :key="action.label" :command="action">
                  <el-icon><component :is="action.icon" /></el-icon>
                  <span>{{ action.label }}</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>

          <router-link v-if="userStore.hasPerm('ai:assist')" to="/ai" class="ai-chip">
            <el-icon><MagicStick /></el-icon>
            <span>AI 助手</span>
          </router-link>

          <el-dropdown trigger="click" placement="bottom-end">
            <button type="button" class="user-chip">
              <el-avatar :size="28">{{ (userStore.displayName || '未').slice(0, 1) }}</el-avatar>
              <span class="user-name">{{ userStore.displayName }}</span>
              <el-icon class="user-caret"><ArrowDown /></el-icon>
            </button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item disabled>
                  {{ userStore.roleName || '未分配角色' }} · {{ userStore.dataScopeName }}
                </el-dropdown-item>
                <el-dropdown-item divided @click="handleLogout">退出登录</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </header>

      <main class="content">
        <router-view v-slot="{ Component }">
          <transition name="page-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </main>
    </div>

    <CommandPalette />
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  height: 100vh;
  overflow: hidden;
  background: var(--bg);
}

.sidebar {
  background: #111827;
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  transition: width 0.22s ease;
  overflow: hidden;
  border-right: 1px solid rgba(255, 255, 255, 0.05);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  height: 58px;
  padding: 0 14px;
  flex-shrink: 0;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.brand.compact {
  padding: 0;
  justify-content: center;
}

.brand-mark {
  width: 30px;
  height: 30px;
  border-radius: 9px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  color: #fff;
  font-weight: 700;
  font-size: 13px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.brand-text strong {
  display: block;
  color: #fff;
  font-size: 15px;
  line-height: 1.2;
}

.brand-text span {
  color: #8b95a7;
  font-size: 10.5px;
}

.drawer-mask {
  position: fixed;
  inset: 0;
  z-index: 2100;
  background: rgba(15, 23, 42, 0.45);
  opacity: 0;
  visibility: hidden;
  transition: opacity 0.2s ease, visibility 0.2s ease;
}

.drawer-mask.open {
  opacity: 1;
  visibility: visible;
}

.drawer {
  width: 268px;
  max-width: 82vw;
  height: 100%;
  background: #111827;
  display: flex;
  flex-direction: column;
  transform: translateX(-100%);
  transition: transform 0.24s ease;
  box-shadow: 12px 0 32px rgba(15, 23, 42, 0.3);
}

.drawer-mask.open .drawer {
  transform: translateX(0);
}

.drawer-close {
  margin-left: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.06);
  color: #cbd5e1;
  cursor: pointer;
}

.drawer-close:hover {
  background: rgba(255, 255, 255, 0.12);
  color: #fff;
}

.workspace {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  height: 100%;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  height: 58px;
  padding: 0 18px;
  background: #fff;
  border-bottom: 1px solid var(--border);
  flex-shrink: 0;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.icon-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #475569;
  cursor: pointer;
  font-size: 17px;
  transition: background 0.16s, color 0.16s;
}

.icon-btn:hover {
  background: var(--bg);
  color: var(--brand);
}

.icon-btn:focus-visible {
  outline: 2px solid var(--brand);
  outline-offset: 1px;
}

.crumbs {
  display: flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
  font-size: 12.5px;
  color: var(--text-muted);
}

.crumb-group {
  flex-shrink: 0;
}

.crumb-sep {
  font-size: 10px;
  color: #cbd5e1;
}

.crumb-current {
  color: var(--text);
  font-weight: 600;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.bell-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  color: #475569;
  font-size: 18px;
  text-decoration: none;
  transition: background 0.16s, color 0.16s;
}

.bell-btn:hover {
  background: var(--bg);
  color: var(--brand);
}

.search-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  height: 32px;
  padding: 0 9px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #fff;
  color: var(--text-secondary);
  font-size: 12.5px;
  cursor: pointer;
  transition: border-color 0.16s, color 0.16s, background 0.16s;
}

.search-btn:hover {
  border-color: #c7cbf5;
  color: var(--brand-dark);
  background: #f8f9ff;
}

.search-btn kbd {
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0 4px;
  font-size: 10.5px;
  font-family: inherit;
  color: #94a3b8;
  background: #f8fafc;
}

.create-btn {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 32px;
  padding: 0 12px;
  border: none;
  border-radius: 8px;
  background: linear-gradient(135deg, #6366f1, #7c3aed);
  color: #fff;
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(99, 102, 241, 0.28);
  transition: box-shadow 0.16s, transform 0.16s;
}

.create-btn:hover {
  box-shadow: 0 4px 14px rgba(99, 102, 241, 0.36);
  transform: translateY(-1px);
}

.ai-chip {
  display: flex;
  align-items: center;
  gap: 5px;
  height: 32px;
  padding: 0 11px;
  border-radius: 999px;
  background: #eef2ff;
  color: #4f46e5;
  font-size: 12.5px;
  font-weight: 500;
  text-decoration: none;
  transition: background 0.16s;
}

.ai-chip:hover {
  background: #e0e7ff;
}

.user-chip {
  display: flex;
  align-items: center;
  gap: 7px;
  height: 34px;
  padding: 0 8px 0 3px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: #374151;
  font-size: 12.5px;
  cursor: pointer;
  transition: background 0.16s;
}

.user-chip:hover {
  background: var(--bg);
}

.user-name {
  max-width: 110px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

.user-caret {
  font-size: 11px;
  color: var(--text-muted);
}

.content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  background: var(--bg);
}

.page-fade-enter-active,
.page-fade-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
}

.page-fade-enter-from {
  opacity: 0;
  transform: translateY(5px);
}

.page-fade-leave-to {
  opacity: 0;
}

@media (max-width: 1180px) {
  .search-text,
  .create-btn span,
  .ai-chip span {
    display: none;
  }

  .create-btn,
  .ai-chip {
    width: 34px;
    justify-content: center;
    padding: 0;
  }

  .ai-chip {
    border-radius: 9px;
  }
}
</style>
