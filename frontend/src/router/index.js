import { createRouter, createWebHashHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import MainLayout from '../layouts/MainLayout.vue'
import { can, canAny } from '../utils/auth'
import { canSeeRoute } from '../utils/nav'
import { defaultPath, firstAccessiblePath } from './helper'
import { workspaceRoutes } from './routes'

const routes = [
  { path: '/login', name: 'login', component: () => import('../views/Login.vue'), meta: { public: true } },
  { path: '/register', name: 'register', component: () => import('../views/Register.vue'), meta: { public: true } },
  {
    path: '/',
    component: MainLayout,
    redirect: () => defaultPath(),
    children: workspaceRoutes,
  },
  { path: '/:pathMatch(.*)*', redirect: () => defaultPath() },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to) => {
  const token = localStorage.getItem('erp_token')
  if (to.meta.public) {
    return token && to.name === 'login' ? defaultPath() : true
  }
  if (!token) {
    return { name: 'login' }
  }
  if (!canSeeRoute(to, can, canAny)) {
    const path = firstAccessiblePath()
    if (!path) {
      ElMessage.warning('当前账号没有任何可访问的模块，请联系管理员')
      return { name: 'login' }
    }
    ElMessage.warning(`当前角色无权访问「${to.meta.title || '该页面'}」`)
    return path
  }
  return true
})

export default router
