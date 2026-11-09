<script setup>
/**
 * 侧边导航：分组标题 + 缩进子项，最近访问置于顶部。
 * 分组展开状态按账号记在 localStorage；搜索时强制展开全部命中分组。
 */
import { computed, ref, watch } from 'vue'

const props = defineProps({
  groups: { type: Array, default: () => [] },
  recent: { type: Array, default: () => [] },
  activePath: { type: String, default: '' },
  collapsed: { type: Boolean, default: false },
})

const OPEN_KEY = 'erp.nav.openGroups'
const keyword = ref('')

function readOpenGroups() {
  try {
    const value = JSON.parse(localStorage.getItem(OPEN_KEY) || 'null')
    return Array.isArray(value) ? value.filter((item) => typeof item === 'string') : null
  } catch {
    return null
  }
}

const activeGroup = computed(
  () => props.groups.find((group) => group.items.some((item) => item.path === props.activePath))?.name,
)

const savedOpenGroups = readOpenGroups()
const openGroups = ref(new Set(savedOpenGroups || (activeGroup.value ? [activeGroup.value] : [])))

function persist() {
  localStorage.setItem(OPEN_KEY, JSON.stringify([...openGroups.value]))
}

function isOpen(name) {
  if (keyword.value.trim()) return true
  return openGroups.value.has(name)
}

function toggle(name) {
  const next = new Set(openGroups.value)
  if (next.has(name)) next.delete(name)
  else next.add(name)
  openGroups.value = next
  persist()
}

// 跳到别的分组时自动展开，避免“当前页在菜单里找不到”
watch(activeGroup, (name) => {
  if (!name || openGroups.value.has(name)) return
  const next = new Set(openGroups.value)
  next.add(name)
  openGroups.value = next
  persist()
})

const filteredGroups = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return props.groups
  return props.groups
    .map((group) => ({
      ...group,
      items: group.items.filter(
        (item) =>
          item.title.toLowerCase().includes(text) ||
          item.desc.toLowerCase().includes(text) ||
          group.name.toLowerCase().includes(text),
      ),
    }))
    .filter((group) => group.items.length)
})

// 当前页在分组里已经有高亮，最近访问里不再重复出现
const recentItems = computed(() => props.recent.filter((item) => item.path !== props.activePath))

function clearKeyword() {
  keyword.value = ''
}
</script>

<template>
  <nav class="sidenav" :class="{ collapsed }" aria-label="主导航">
    <div v-if="!collapsed" class="nav-filter">
      <el-icon class="filter-icon"><Search /></el-icon>
      <input v-model="keyword" placeholder="筛选菜单" aria-label="筛选菜单" />
      <button v-if="keyword" type="button" class="filter-clear" aria-label="清空筛选" @click="clearKeyword">
        <el-icon><Close /></el-icon>
      </button>
    </div>

    <div class="nav-body">
      <section v-if="!collapsed && !keyword.trim() && recentItems.length" class="nav-recent">
        <p class="section-label">最近访问</p>
        <router-link
          v-for="item in recentItems"
          :key="`recent-${item.name}`"
          :to="item.path"
          class="nav-item recent-item"
          :class="{ active: item.path === activePath }"
        >
          <el-icon class="item-icon"><component :is="item.icon" /></el-icon>
          <span class="item-label">{{ item.title }}</span>
        </router-link>
      </section>

      <section v-for="group in filteredGroups" :key="group.name" class="nav-group">
        <button
          v-if="!collapsed"
          type="button"
          class="group-head"
          :aria-expanded="isOpen(group.name)"
          @click="toggle(group.name)"
        >
          <span class="group-name">{{ group.name }}</span>
          <span class="group-count">{{ group.items.length }}</span>
          <el-icon class="group-chevron" :class="{ closed: !isOpen(group.name) }"><ArrowDown /></el-icon>
        </button>
        <div v-show="collapsed || isOpen(group.name)" class="group-items">
          <router-link
            v-for="item in group.items"
            :key="item.name"
            :to="item.path"
            class="nav-item"
            :class="{ active: item.path === activePath }"
            :title="collapsed ? `${item.title} · ${item.desc}` : item.desc"
          >
            <el-icon class="item-icon"><component :is="item.icon" /></el-icon>
            <span v-if="!collapsed" class="item-label">{{ item.title }}</span>
          </router-link>
        </div>
      </section>

      <p v-if="keyword.trim() && !filteredGroups.length" class="nav-empty">
        没有匹配「{{ keyword }}」的菜单
      </p>
    </div>
  </nav>
</template>

<style scoped>
.sidenav {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  color: #cbd5e1;
}

.nav-filter {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 10px 12px 6px;
  padding: 0 8px;
  height: 32px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.07);
  color: #94a3b8;
  transition: border-color 0.18s, background 0.18s;
}

.nav-filter:focus-within {
  border-color: rgba(129, 140, 248, 0.65);
  background: rgba(255, 255, 255, 0.08);
}

.filter-icon {
  font-size: 13px;
}

.nav-filter input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  color: #e5e7eb;
  font-size: 12.5px;
  font-family: inherit;
}

.nav-filter input::placeholder {
  color: #7c8798;
}

.filter-clear {
  display: flex;
  align-items: center;
  border: none;
  background: transparent;
  color: #94a3b8;
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
}

.filter-clear:hover {
  color: #fff;
}

.nav-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 2px 8px 12px;
  scrollbar-width: thin;
}

.nav-body::-webkit-scrollbar {
  width: 6px;
}

.nav-body::-webkit-scrollbar-thumb {
  background: rgba(255, 255, 255, 0.14);
  border-radius: 3px;
}

.section-label {
  margin: 10px 8px 4px;
  font-size: 11px;
  letter-spacing: 0.6px;
  color: #7c8798;
}

.nav-recent {
  padding-bottom: 6px;
  margin-bottom: 4px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.recent-item .item-icon {
  color: #818cf8;
}

.nav-group + .nav-group {
  margin-top: 2px;
}

.group-head {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  margin: 8px 0 2px;
  padding: 5px 8px;
  border: none;
  border-radius: 7px;
  background: transparent;
  color: #8b95a7;
  font-size: 11.5px;
  font-weight: 600;
  letter-spacing: 0.5px;
  text-align: left;
  cursor: pointer;
  transition: background 0.16s, color 0.16s;
}

.group-head:hover {
  background: rgba(255, 255, 255, 0.05);
  color: #e2e8f0;
}

.group-name {
  flex: 1;
}

.group-count {
  font-size: 10.5px;
  font-weight: 500;
  color: #64748b;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 999px;
  padding: 0 6px;
  line-height: 16px;
}

.group-chevron {
  font-size: 10px;
  color: #64748b;
  transition: transform 0.18s ease;
}

.group-chevron.closed {
  transform: rotate(-90deg);
}

.group-items {
  display: flex;
  flex-direction: column;
  gap: 1px;
  margin-left: 7px;
  padding-left: 7px;
  border-left: 1px solid rgba(255, 255, 255, 0.07);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  height: 34px;
  padding: 0 9px;
  border-radius: 8px;
  color: #b6bfcd;
  text-decoration: none;
  font-size: 13px;
  line-height: 1;
  transition: background 0.16s, color 0.16s;
}

.nav-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
}

.nav-item.active {
  background: rgba(99, 102, 241, 0.2);
  color: #fff;
  font-weight: 500;
}

.nav-item.active .item-icon {
  color: #a5b4fc;
}

.item-icon {
  font-size: 15px;
  color: #8b95a7;
  flex-shrink: 0;
}

.item-label {
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}

/* 收起态：仅图标，靠分隔线区分分组 */
.sidenav.collapsed .nav-body {
  padding: 6px 6px 12px;
}

.sidenav.collapsed .nav-group + .nav-group {
  margin-top: 6px;
  padding-top: 6px;
  border-top: 1px solid rgba(255, 255, 255, 0.07);
}

.sidenav.collapsed .group-items {
  margin-left: 0;
  padding-left: 0;
  border-left: none;
  align-items: center;
}

.sidenav.collapsed .nav-item {
  justify-content: center;
  width: 44px;
  height: 38px;
  padding: 0;
}

.sidenav.collapsed .nav-item.active {
  position: relative;
}

.sidenav.collapsed .nav-item.active::before {
  content: '';
  position: absolute;
  left: -3px;
  top: 50%;
  transform: translateY(-50%);
  width: 3px;
  height: 16px;
  border-radius: 2px;
  background: #818cf8;
}

.nav-empty {
  padding: 18px 10px;
  font-size: 12px;
  line-height: 1.6;
  color: #7c8798;
}
</style>
