<script setup>
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { authApi } from '../api'

const router = useRouter()
const formRef = ref()
const sending = ref(false)
const submitting = ref(false)
const countdown = ref(0)
const devCode = ref('')
let timer = null

const form = reactive({
  phone: '',
  code: '',
  username: '',
  full_name: '',
  password: '',
  confirm: '',
})

const rules = {
  phone: [
    { required: true, message: '请输入手机号', trigger: 'blur' },
    { pattern: /^1[3-9]\d{9}$/, message: '请输入正确的 11 位手机号', trigger: 'blur' },
  ],
  code: [
    { required: true, message: '请输入验证码', trigger: 'blur' },
    { pattern: /^\d{6}$/, message: '验证码为 6 位数字', trigger: 'blur' },
  ],
  password: [
    { required: true, message: '请设置登录密码', trigger: 'blur' },
    { min: 6, max: 64, message: '密码长度需为 6~64 位', trigger: 'blur' },
  ],
  confirm: [
    {
      validator: (rule, value, callback) =>
        value === form.password ? callback() : callback(new Error('两次输入的密码不一致')),
      trigger: 'blur',
    },
  ],
}

function startCountdown() {
  countdown.value = 60
  clearInterval(timer)
  timer = setInterval(() => {
    countdown.value -= 1
    if (countdown.value <= 0) clearInterval(timer)
  }, 1000)
}

async function sendCode() {
  try {
    await formRef.value.validateField('phone')
  } catch {
    return
  }
  sending.value = true
  try {
    const data = await authApi.sendRegisterCode(form.phone)
    ElMessage.success(data.message)
    if (data.dev_code) {
      devCode.value = data.dev_code
      form.code = data.dev_code
    }
    startCountdown()
  } catch {
    // 错误提示由拦截器统一处理
  } finally {
    sending.value = false
  }
}

async function submit() {
  try {
    await formRef.value.validate()
  } catch {
    return
  }
  submitting.value = true
  try {
    const data = await authApi.register({
      phone: form.phone,
      code: form.code,
      username: form.username,
      full_name: form.full_name,
      password: form.password,
    })
    ElMessage.success(data.message)
    router.push({ name: 'login' })
  } catch {
    // 错误提示由拦截器统一处理
  } finally {
    submitting.value = false
  }
}

onBeforeUnmount(() => clearInterval(timer))
</script>

<template>
  <div class="register-page">
    <div class="register-card">
      <div class="brand">
        <div class="brand-mark">AI</div>
        <span>AI-ERP 智能管理系统</span>
      </div>
      <h1>手机号注册</h1>
      <p class="sub">验证手机号后提交注册，管理员审核通过即可登录</p>

      <el-form ref="formRef" :model="form" :rules="rules" label-position="top" size="large">
        <el-form-item label="手机号" prop="phone">
          <el-input v-model="form.phone" placeholder="11 位手机号" maxlength="11" :prefix-icon="'Iphone'" />
        </el-form-item>

        <el-form-item label="验证码" prop="code">
          <div class="code-row">
            <el-input v-model="form.code" placeholder="6 位验证码" maxlength="6" :prefix-icon="'Message'" />
            <el-button :disabled="countdown > 0" :loading="sending" class="code-btn" @click="sendCode">
              {{ countdown > 0 ? `${countdown}s 后重发` : '获取验证码' }}
            </el-button>
          </div>
        </el-form-item>

        <el-alert
          v-if="devCode"
          type="warning"
          :closable="false"
          show-icon
          :title="`演练模式：验证码 ${devCode} 已自动填入（后端日志同样可查）`"
          class="dev-tip"
        />

        <el-form-item label="登录账号" prop="username">
          <el-input v-model="form.username" placeholder="留空则使用手机号登录" :prefix-icon="'User'" />
        </el-form-item>

        <el-form-item label="姓名" prop="full_name">
          <el-input v-model="form.full_name" placeholder="选填，便于管理员识别" :prefix-icon="'Avatar'" />
        </el-form-item>

        <el-form-item label="登录密码" prop="password">
          <el-input v-model="form.password" type="password" show-password placeholder="6~64 位" :prefix-icon="'Lock'" />
        </el-form-item>

        <el-form-item label="确认密码" prop="confirm">
          <el-input
            v-model="form.confirm"
            type="password"
            show-password
            placeholder="再次输入密码"
            :prefix-icon="'Lock'"
            @keyup.enter="submit"
          />
        </el-form-item>

        <el-button type="primary" class="submit-btn" :loading="submitting" @click="submit">提交注册</el-button>
      </el-form>

      <div class="foot">
        <span>已有账号？</span>
        <a class="link" @click="router.push({ name: 'login' })">返回登录</a>
      </div>
    </div>
  </div>
</template>

<style scoped>
.register-page {
  min-height: 100vh;
  padding: 32px 16px;
  box-sizing: border-box;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(150deg, #111827 0%, #1e1b4b 55%, #312e81 100%);
}

.register-card {
  width: 440px;
  background: #fff;
  border-radius: 16px;
  padding: 28px 32px 24px;
  box-shadow: 0 20px 50px rgba(15, 23, 42, 0.35);
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 22px;
  font-size: 14px;
  color: #4b5563;
}

.brand-mark {
  width: 34px;
  height: 34px;
  border-radius: 10px;
  background: linear-gradient(135deg, #6366f1, #8b5cf6);
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
  font-size: 13px;
  color: #fff;
}

.register-card h1 {
  font-size: 24px;
  margin: 0 0 6px;
  color: #111827;
}

.sub {
  color: #6b7280;
  font-size: 13px;
  margin-bottom: 18px;
}

.code-row {
  display: flex;
  gap: 10px;
  width: 100%;
}

.code-btn {
  width: 128px;
  flex-shrink: 0;
}

.dev-tip {
  margin: -4px 0 14px;
}

.submit-btn {
  width: 100%;
  height: 44px;
  font-size: 15px;
  background: linear-gradient(90deg, #6366f1, #7c3aed);
  border: none;
  margin-top: 6px;
}

.foot {
  margin-top: 18px;
  text-align: center;
  font-size: 13px;
  color: #6b7280;
}

.link {
  color: #6366f1;
  cursor: pointer;
  margin-left: 6px;
}
</style>
