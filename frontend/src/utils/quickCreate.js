import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

/**
 * 让列表页支持从顶栏「新建」或看板快捷入口带 ?new=1 直达新建弹窗。
 * 打开后立即清掉该参数，避免刷新或从详情返回时再次弹窗。
 *
 * @param {() => void} openCreate 打开新建弹窗的方法
 */
export function useQuickCreate(openCreate) {
  const route = useRoute()
  const router = useRouter()
  onMounted(() => {
    if (route.query.new !== '1') return
    openCreate()
    const { new: created, ...rest } = route.query
    router.replace({ path: route.path, query: rest })
  })
}
