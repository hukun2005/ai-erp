import axios from 'axios'
import { ElMessage } from 'element-plus'

const http = axios.create({
  baseURL: '/api',
  timeout: 20000,
})

http.interceptors.request.use((config) => {
  const token = localStorage.getItem('erp_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

http.interceptors.response.use(
  (response) => response.data,
  async (error) => {
    const status = error.response?.status
    const data = error.response?.data
    let detail = data?.detail || '网络异常，请稍后重试'
    // 下载接口（responseType=blob）出错时，响应体是 Blob，需要先解析出后端提示
    if (data instanceof Blob) {
      detail = '文件下载失败，请稍后重试'
      try {
        const text = (await data.text()).trim()
        const json = text.startsWith('{') ? JSON.parse(text) : null
        if (json?.detail) detail = String(json.detail)
      } catch {
        // 解析失败时保留默认提示
      }
    }
    if (status === 401) {
      localStorage.removeItem('erp_token')
      localStorage.removeItem('erp_user')
      if (!location.hash.includes('/login')) {
        location.hash = '#/login'
      }
    }
    ElMessage.error(typeof detail === 'string' ? detail : '请求失败')
    return Promise.reject(error)
  }
)

export default http
