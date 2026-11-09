import { defineStore } from 'pinia'
import { authApi } from '../api'

export const useUserStore = defineStore('user', {
  state: () => ({
    token: localStorage.getItem('erp_token') || '',
    user: JSON.parse(localStorage.getItem('erp_user') || 'null'),
  }),
  getters: {
    isLoggedIn: (state) => !!state.token,
    displayName: (state) => state.user?.full_name || state.user?.username || '未登录',
    roleName: (state) => state.user?.role_name || '',
    dataScope: (state) => state.user?.data_scope || 'all',
    dataScopeName: (state) => state.user?.data_scope_name || '全部数据',
    permissions: (state) => state.user?.permissions || [],
    aiPrompts: (state) => state.user?.ai_prompts || [],
  },
  actions: {
    hasPerm(perm) {
      if (!perm) return true
      const perms = this.user?.permissions || []
      return perms.includes('*') || perms.includes(perm)
    },
    hasAnyPerm(list) {
      return (list || []).some((p) => this.hasPerm(p))
    },
    async login(username, password) {
      const data = await authApi.login({ username, password })
      this.setUser(data.user)
      this.token = data.access_token
      localStorage.setItem('erp_token', data.access_token)
      localStorage.setItem('erp_user', JSON.stringify(data.user))
    },
    async refresh() {
      const data = await authApi.me()
      this.setUser(data)
    },
    setUser(user) {
      this.user = user
      localStorage.setItem('erp_user', JSON.stringify(user))
    },
    logout() {
      this.token = ''
      this.user = null
      localStorage.removeItem('erp_token')
      localStorage.removeItem('erp_user')
    },
  },
})
