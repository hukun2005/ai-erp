import { watch } from 'vue'
import { useRoute } from 'vue-router'

/**
 * 让列表页接受 ?keyword= 预填（命令面板跳转用）。
 * 同一路由内再次搜索时 query 变化也会刷新，而不是只认首次进入。
 */
export function useRouteKeyword(query, reload) {
  const route = useRoute()
  if (typeof route.query.keyword === 'string') {
    query.keyword = route.query.keyword
  }
  watch(
    () => route.query.keyword,
    (value) => {
      if (typeof value !== 'string') return
      query.keyword = value
      reload()
    }
  )
  return route
}
