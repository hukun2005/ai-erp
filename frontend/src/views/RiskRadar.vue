<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { riskApi } from '../api'

const router = useRouter()

const loading = ref(false)
const data = ref({ summary: {}, groups: [], generated_at: '' })
const activeLevel = ref('')

const levelMap = {
  danger: { text: '紧急', color: '#ef4444', tag: 'danger' },
  warning: { text: '关注', color: '#f59e0b', tag: 'warning' },
  info: { text: '提示', color: '#0ea5e9', tag: 'info' },
}

const summaryCards = computed(() => [
  { key: '', label: '全部风险', value: data.value.summary.total || 0, color: '#6366f1' },
  { key: 'danger', label: '紧急', value: data.value.summary.danger || 0, color: '#ef4444' },
  { key: 'warning', label: '关注', value: data.value.summary.warning || 0, color: '#f59e0b' },
  { key: 'info', label: '提示', value: data.value.summary.info || 0, color: '#0ea5e9' },
])

const visibleGroups = computed(() =>
  data.value.groups
    .map((group) => ({
      ...group,
      items: activeLevel.value
        ? group.items.filter((item) => item.level === activeLevel.value)
        : group.items,
    }))
    .filter((group) => group.items.length)
)

async function load() {
  loading.value = true
  try {
    data.value = await riskApi.radar()
  } finally {
    loading.value = false
  }
}

function toggleLevel(key) {
  activeLevel.value = activeLevel.value === key ? '' : key
}

function go(item) {
  const action = item.action || {}
  if (!action.path) return
  router.push({ path: action.path, query: action.query || {} })
}

onMounted(load)
</script>

<template>
  <div class="page" v-loading="loading">
    <div class="page-header">
      <div>
        <h1 class="page-title">风险雷达</h1>
        <p class="page-subtitle">
          把信用、库存、应收、客户流失与待办汇总成一张清单，点一下直接跳去处理
          <span v-if="data.generated_at" class="generated">· 更新于 {{ data.generated_at }}</span>
        </p>
      </div>
      <el-button @click="load"><el-icon><Refresh /></el-icon>&nbsp;刷新</el-button>
    </div>

    <div class="summary">
      <div
        v-for="card in summaryCards"
        :key="card.key || 'all'"
        :class="['summary-card', { active: activeLevel === card.key }]"
        :style="{ borderColor: activeLevel === card.key ? card.color : 'var(--border)' }"
        @click="toggleLevel(card.key)"
      >
        <span class="summary-value" :style="{ color: card.color }">{{ card.value }}</span>
        <span class="summary-label">{{ card.label }}</span>
      </div>
    </div>

    <div v-if="!visibleGroups.length" class="card empty">
      <el-icon class="empty-icon"><CircleCheck /></el-icon>
      <p>当前没有需要处理的风险，账实与回款都在正常区间</p>
    </div>

    <div v-for="group in visibleGroups" :key="group.key" class="card group">
      <div class="group-head">
        <div class="group-title">
          <el-icon><component :is="group.icon || 'Warning'" /></el-icon>
          <span>{{ group.title }}</span>
        </div>
        <div class="group-meta">
          <el-tag v-if="group.danger" type="danger" effect="light" size="small">紧急 {{ group.danger }}</el-tag>
          <el-tag v-if="group.warning" type="warning" effect="light" size="small">关注 {{ group.warning }}</el-tag>
          <span class="group-count">共 {{ group.count }} 项</span>
        </div>
      </div>

      <div class="items">
        <div v-for="item in group.items" :key="item.key" class="item">
          <span class="item-bar" :style="{ background: levelMap[item.level]?.color || '#9ca3af' }"></span>
          <div class="item-main">
            <div class="item-title">{{ item.title }}</div>
            <div class="item-detail">{{ item.detail }}</div>
          </div>
          <div v-if="item.metric_label" class="item-metric">{{ item.metric_label }}</div>
          <el-button
            v-if="item.action && item.action.path"
            link
            type="primary"
            @click="go(item)"
          >
            {{ item.action.label || '去处理' }} →
          </el-button>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.generated {
  color: #9ca3af;
}

.summary {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}

.summary-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  cursor: pointer;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.summary-card:hover {
  box-shadow: 0 2px 8px rgba(16, 24, 40, 0.06);
}

.summary-value {
  font-size: 24px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.summary-label {
  font-size: 12px;
  color: var(--text-secondary);
}

.group {
  margin-bottom: 14px;
}

.group-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}

.group-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  font-size: 15px;
}

.group-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: var(--text-secondary);
}

.items {
  display: flex;
  flex-direction: column;
}

.item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-top: 1px solid #f1f5f9;
}

.item:first-child {
  border-top: none;
}

.item-bar {
  width: 3px;
  height: 30px;
  border-radius: 2px;
  flex-shrink: 0;
}

.item-main {
  flex: 1;
  min-width: 0;
}

.item-title {
  font-size: 14px;
}

.item-detail {
  font-size: 12px;
  color: var(--text-secondary);
  margin-top: 2px;
}

.item-metric {
  font-size: 13px;
  color: var(--text);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.empty {
  text-align: center;
  padding: 48px 0;
  color: var(--text-secondary);
}

.empty-icon {
  font-size: 36px;
  color: var(--success);
}

@media (max-width: 900px) {
  .summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
