<script setup>
/**
 * 全局命令面板：最近访问、快捷操作、菜单跳转、业务数据搜索四段式。
 * 空关键字时只展示最近访问与快捷操作，避免一打开就是长长的菜单列表。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { searchApi } from '../api'
import { useUserStore } from '../stores/user'
import { availableQuickActions, recentNavItems, toNavItem, visibleRoutes } from '../utils/nav'

const route = useRoute()
const router = useRouter()
const userStore = useUserStore()

const visible = ref(false)
const keyword = ref('')
const loading = ref(false)
const activeIndex = ref(0)
const remoteGroups = ref([])
const inputRef = ref()

let debounceTimer = null
let requestSeq = 0

const routes = computed(() => visibleRoutes(userStore.hasPerm, userStore.hasAnyPerm))
const navPool = computed(() => routes.value.map(toNavItem))
const quickPool = computed(() => availableQuickActions(userStore.hasPerm))
const recent = computed(() => recentNavItems(routes.value).filter((item) => item.path !== route.path))

function navEntry(item) {
  return {
    key: `nav-${item.name}`,
    title: item.title,
    subtitle: `${item.group} · ${item.desc}`,
    icon: item.icon,
    path: item.path,
    query: {},
    badge: '',
  }
}

function quickEntry(action) {
  return {
    key: `quick-${action.label}`,
    title: action.label,
    subtitle: '直接打开新建窗口',
    icon: action.icon,
    path: action.path,
    query: action.query,
    badge: '',
  }
}

const groups = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  const list = []

  if (!text) {
    if (recent.value.length) {
      list.push({ key: 'recent', title: '最近访问', items: recent.value.map(navEntry) })
    }
    if (quickPool.value.length) {
      list.push({ key: 'quick', title: '快捷操作', items: quickPool.value.map(quickEntry) })
    }
    return list
  }

  const navMatches = navPool.value
    .filter(
      (item) =>
        item.title.toLowerCase().includes(text) ||
        item.desc.toLowerCase().includes(text) ||
        item.group.toLowerCase().includes(text),
    )
    .slice(0, 6)
    .map(navEntry)
  if (navMatches.length) list.push({ key: 'nav', title: '菜单', items: navMatches })

  const quickMatches = quickPool.value
    .filter((action) => action.label.toLowerCase().includes(text))
    .map(quickEntry)
  if (quickMatches.length) list.push({ key: 'quick', title: '快捷操作', items: quickMatches })

  for (const group of remoteGroups.value) {
    list.push({
      key: group.key,
      title: group.title,
      items: group.items.map((item) => ({
        ...item,
        key: `${group.key}-${item.id}`,
        badge: group.title,
      })),
    })
  }
  return list
})

const flatItems = computed(() => groups.value.flatMap((group) => group.items))

function globalIndex(group, index) {
  let offset = 0
  for (const item of groups.value) {
    if (item.key === group.key) break
    offset += item.items.length
  }
  return offset + index
}

function open() {
  visible.value = true
  keyword.value = ''
  remoteGroups.value = []
  activeIndex.value = 0
  nextTick(() => inputRef.value?.focus())
}

function close() {
  visible.value = false
}

function choose(item) {
  if (!item) return
  close()
  router.push({ path: item.path, query: item.query || {} })
}

function move(step) {
  const total = flatItems.value.length
  if (!total) return
  activeIndex.value = (activeIndex.value + step + total) % total
}

function onKeydown(event) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    move(1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    move(-1)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    choose(flatItems.value[activeIndex.value])
  } else if (event.key === 'Escape') {
    event.preventDefault()
    close()
  }
}

// 输入即搜：本地导航与操作立刻过滤，业务数据防抖后再查
watch(keyword, (value) => {
  const text = value.trim()
  activeIndex.value = 0
  if (debounceTimer) clearTimeout(debounceTimer)
  if (!text) {
    remoteGroups.value = []
    loading.value = false
    return
  }
  loading.value = true
  debounceTimer = setTimeout(async () => {
    const seq = ++requestSeq
    try {
      const data = await searchApi.search(text)
      if (seq === requestSeq) remoteGroups.value = data.groups || []
    } catch (err) {
      if (seq === requestSeq) remoteGroups.value = []
    } finally {
      if (seq === requestSeq) loading.value = false
    }
  }, 180)
})

function isTypingTarget(target) {
  if (!target || !(target instanceof HTMLElement)) return false
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable
}

function onGlobalKey(event) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    if (visible.value) close()
    else open()
    return
  }
  // “/” 快速搜索：光标不在输入框里时才拦截，避免影响正常输入
  if (event.key === '/' && !visible.value && !isTypingTarget(event.target)) {
    event.preventDefault()
    open()
  }
}

onMounted(() => {
  window.addEventListener('keydown', onGlobalKey)
  window.addEventListener('erp-command-palette', open)
})

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onGlobalKey)
  window.removeEventListener('erp-command-palette', open)
  if (debounceTimer) clearTimeout(debounceTimer)
})
</script>

<template>
  <teleport to="body">
    <transition name="cmd-fade">
      <div v-if="visible" class="cmd-mask" @mousedown.self="close">
        <div class="cmd-panel" role="dialog" aria-label="全局搜索">
          <div class="cmd-input">
            <el-icon><Search /></el-icon>
            <input
              ref="inputRef"
              v-model="keyword"
              placeholder="搜索商品、客户、订单单号，或输入模块名…"
              aria-label="搜索内容"
              @keydown="onKeydown"
            />
            <span class="cmd-esc">ESC</span>
          </div>

          <div class="cmd-body">
            <div v-if="loading" class="cmd-hint">搜索中…</div>
            <template v-else-if="flatItems.length">
              <div v-for="group in groups" :key="group.key" class="cmd-group">
                <div class="cmd-group-title">{{ group.title }}</div>
                <div
                  v-for="(item, index) in group.items"
                  :key="item.key"
                  :class="['cmd-item', { active: globalIndex(group, index) === activeIndex }]"
                  @mouseenter="activeIndex = globalIndex(group, index)"
                  @click="choose(item)"
                >
                  <el-icon class="cmd-item-icon"><component :is="item.icon || 'Document'" /></el-icon>
                  <div class="cmd-item-text">
                    <div class="cmd-item-title">{{ item.title }}</div>
                    <div v-if="item.subtitle" class="cmd-item-sub">{{ item.subtitle }}</div>
                  </div>
                  <span v-if="item.badge" class="cmd-item-badge">{{ item.badge }}</span>
                  <span class="cmd-item-enter">↵</span>
                </div>
              </div>
            </template>
            <div v-else-if="keyword" class="cmd-empty">
              没有找到「{{ keyword }}」
              <p>可以试试商品名称、客户名称或订单号；也可以按关键字找菜单。</p>
            </div>
            <div v-else class="cmd-empty">
              输入关键字开始搜索
              <p>想跳过搜索？右侧「新建」可以直接开单。</p>
            </div>
          </div>

          <div class="cmd-footer">
            <span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
            <span><kbd>↵</kbd> 打开</span>
            <span><kbd>/</kbd> 或 <kbd>Ctrl K</kbd> 唤起</span>
          </div>
        </div>
      </div>
    </transition>
  </teleport>
</template>

<style scoped>
.cmd-mask {
  position: fixed;
  inset: 0;
  z-index: 3000;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 11vh;
}

.cmd-panel {
  width: 640px;
  max-width: calc(100vw - 32px);
  background: #fff;
  border-radius: 14px;
  box-shadow: 0 24px 60px rgba(15, 23, 42, 0.28);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  max-height: 74vh;
}

.cmd-input {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 14px 18px;
  border-bottom: 1px solid var(--border);
  color: var(--text-secondary);
}

.cmd-input input {
  flex: 1;
  border: none;
  outline: none;
  font-size: 15px;
  color: var(--text);
  font-family: inherit;
  background: transparent;
}

.cmd-esc {
  font-size: 11px;
  color: #9ca3af;
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 1px 5px;
}

.cmd-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 8px 0;
}

.cmd-group-title {
  padding: 8px 18px 4px;
  font-size: 11px;
  letter-spacing: 1px;
  color: #9ca3af;
}

.cmd-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 18px;
  cursor: pointer;
}

.cmd-item.active {
  background: #eef2ff;
}

.cmd-item-icon {
  color: var(--brand);
  font-size: 16px;
}

.cmd-item-text {
  flex: 1;
  min-width: 0;
}

.cmd-item-title {
  font-size: 14px;
  color: var(--text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cmd-item-sub {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.cmd-item-badge {
  flex-shrink: 0;
  font-size: 11px;
  color: var(--brand-dark);
  background: #eef2ff;
  border-radius: 999px;
  padding: 1px 8px;
}

.cmd-item-enter {
  font-size: 12px;
  color: #c0c4cc;
}

.cmd-empty {
  padding: 26px 22px 30px;
  color: var(--text-secondary);
  font-size: 13px;
}

.cmd-empty p {
  margin: 6px 0 0;
  font-size: 12px;
  color: var(--text-muted);
}

.cmd-hint {
  padding: 26px 18px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
}

.cmd-footer {
  display: flex;
  gap: 16px;
  padding: 10px 18px;
  border-top: 1px solid var(--border);
  background: #fafbfc;
  font-size: 12px;
  color: var(--text-secondary);
  flex-shrink: 0;
}

.cmd-footer kbd {
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0 4px;
  margin-right: 2px;
  font-family: inherit;
  background: #fff;
}

.cmd-fade-enter-active,
.cmd-fade-leave-active {
  transition: opacity 0.15s ease;
}

.cmd-fade-enter-from,
.cmd-fade-leave-to {
  opacity: 0;
}
</style>
