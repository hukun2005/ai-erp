<script setup>
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useUserStore } from '../stores/user'
import { defaultPath } from '../router/helper'

const router = useRouter()
const userStore = useUserStore()
const loading = ref(false)
const formRef = ref()

const form = reactive({ username: 'admin', password: 'admin123' })

const demoAccounts = [
  { role: '管理员', username: 'admin', password: 'admin123', color: '#6366f1' },
  { role: '销售员', username: 'sales01', password: '123456', color: '#0ea5e9' },
  { role: '销售员B', username: 'sales02', password: '123456', color: '#0284c7' },
  { role: '采购员', username: 'buyer01', password: '123456', color: '#f59e0b' },
  { role: '仓管员', username: 'wh01', password: '123456', color: '#10b981' },
  { role: '财务', username: 'fin01', password: '123456', color: '#8b5cf6' },
]

function fill(account) {
  form.username = account.username
  form.password = account.password
}

const rules = {
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
  password: [{ required: true, message: '请输入密码', trigger: 'blur' }],
}

const features = [
  { icon: 'DataAnalysis', title: '实时经营看板', desc: '销售、库存、客户数据一屏掌握' },
  { icon: 'MagicStick', title: 'AI 智能补货', desc: '按销量趋势自动计算补货量' },
  { icon: 'TrendCharts', title: '销售预测', desc: '基于历史数据预测未来走势' },
  { icon: 'ChatDotRound', title: '自然语言问答', desc: '用一句话查询经营数据' },
]

async function submit() {
  await formRef.value.validate()
  loading.value = true
  try {
    await userStore.login(form.username, form.password)
    ElMessage.success(`欢迎回来，${userStore.roleName}`)
    router.push(defaultPath())
  } catch (err) {
    // 错误提示由拦截器统一处理
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-left">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <span>AI-ERP 智能管理系统</span>
      </div>
      <h1>让 ERP 从"记账工具"<br />变成"经营大脑"</h1>
      <p class="desc">用 AI 替代人工统计与经验判断，降低人力成本，提升运营效率。</p>
      <div class="feature-grid">
        <div v-for="f in features" :key="f.title" class="feature">
          <el-icon><component :is="f.icon" /></el-icon>
          <div>
            <strong>{{ f.title }}</strong>
            <span>{{ f.desc }}</span>
          </div>
        </div>
      </div>
    </div>

    <div class="login-right">
      <div class="login-card">
        <h2>欢迎回来</h2>
        <p class="sub">请登录你的企业账号</p>
        <el-form ref="formRef" :model="form" :rules="rules" size="large" @submit.prevent="submit">
          <el-form-item prop="username">
            <el-input v-model="form.username" placeholder="用户名" :prefix-icon="'User'" />
          </el-form-item>
          <el-form-item prop="password">
            <el-input
              v-model="form.password"
              type="password"
              placeholder="密码"
              show-password
              :prefix-icon="'Lock'"
              @keyup.enter="submit"
            />
          </el-form-item>
          <el-button type="primary" class="login-btn" :loading="loading" @click="submit">登 录</el-button>
        </el-form>
        <div class="register-line">
          <span>还没有账号？</span>
          <router-link to="/register" class="register-link">手机号注册</router-link>
        </div>
        <div class="demo-tip">
          <p class="demo-title">演示账号（点击自动填充，体验不同角色权限）</p>
          <div class="demo-list">
            <button
              v-for="a in demoAccounts"
              :key="a.username"
              class="demo-item"
              :style="{ borderColor: a.color, color: a.color }"
              @click="fill(a)"
            >
              <strong>{{ a.role }}</strong>
              <span>{{ a.username }}</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  height: 100vh;
  display: flex;
}

.login-left {
  flex: 1.1;
  background: linear-gradient(150deg, #111827 0%, #1e1b4b 55%, #312e81 100%);
  color: #fff;
  padding: 56px 64px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  position: relative;
  overflow: hidden;
}

.login-left::after {
  content: '';
  position: absolute;
  width: 420px;
  height: 420px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(99, 102, 241, 0.35), transparent 70%);
  bottom: -120px;
  left: -80px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 48px;
  font-size: 15px;
  color: #c7d2fe;
}

.brand-mark {
  width: 36px;
  height: 36px;
  border-radius: 10px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 14px;
  color: #fff;
}

.login-left h1 {
  font-size: 34px;
  line-height: 1.35;
  margin: 0 0 16px;
  letter-spacing: 1px;
}

.desc {
  color: #9ca3af;
  font-size: 14px;
  margin-bottom: 42px;
  max-width: 460px;
  line-height: 1.7;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
  max-width: 560px;
}

.feature {
  display: flex;
  gap: 10px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  padding: 14px;
  border-radius: 12px;
}

.feature .el-icon {
  font-size: 20px;
  color: #a5b4fc;
  margin-top: 2px;
}

.feature strong {
  display: block;
  font-size: 13px;
  margin-bottom: 4px;
}

.feature span {
  font-size: 12px;
  color: #9ca3af;
  line-height: 1.5;
}

.login-right {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  background: #f8fafc;
}

.login-card {
  width: 380px;
}

.login-card h2 {
  font-size: 26px;
  margin: 0 0 8px;
  color: #111827;
}

.sub {
  color: #6b7280;
  margin-bottom: 30px;
}

.login-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
  background: linear-gradient(90deg, #6366f1, #7c3aed);
  border: none;
  margin-top: 8px;
}

.demo-tip {
  margin-top: 22px;
  padding: 14px;
  border-radius: 10px;
  background: #f8fafc;
  border: 1px solid var(--border);
}

.register-line {
  margin-top: 14px;
  text-align: center;
  font-size: 13px;
  color: var(--text-secondary);
}

.register-link {
  color: #6366f1;
  margin-left: 6px;
  text-decoration: none;
  font-weight: 500;
}

.register-link:hover {
  text-decoration: underline;
}

.demo-title {
  margin: 0 0 10px;
  font-size: 12px;
  color: var(--text-secondary);
}

.demo-list {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.demo-item {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid currentColor;
  background: #fff;
  cursor: pointer;
  font-size: 12px;
  line-height: 1.3;
  transition: all 0.2s;
}

.demo-item:hover {
  background: #f1f5f9;
}

.demo-item strong {
  font-size: 12px;
}

.demo-item span {
  font-size: 11px;
  color: var(--text-secondary);
}
</style>
