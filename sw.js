/* 自毁用的 Service Worker —— 这个文件**故意什么都不缓存**。

   历史：早期形态是「页面可以直接双击打开、后端可选」，所以用 SW 做了离线可用：
   预缓存 index.html / manifest，fetch 缓存优先，连不上时兜底返回缓存里的壳。
   后来部署形态收敛成「页面和数据都由 NAS 上同一个进程发出来」，离线可用就从
   优点变成了坏东西 —— 容器停了，反代明明返回 502，浏览器却照样从缓存里把页面
   端出来，看起来一切正常。于是「服务挂了」这件事被藏起来，直到你点一个需要
   联网的功能才发现。

   所以现在 index.html 末尾**不再注册** SW 了。但这个文件不能直接删：
   已经装过旧 SW 的浏览器，在服务端不可用时根本取不到新的 sw.js，
   会一直拿缓存里的旧壳继续跑，那个毛病会原样保留。
   留着这个自毁版，等下一次**在服务端正常时**访问，它会被装上去 →
   清空所有 cache → 注销自己。在那之后这个文件就再也用不到了。

   它不注册任何 fetch 监听，所以永远不会拦截请求 —— 就算还控制着某个页面，
   那个页面的请求也是直连网络的。 */

self.addEventListener("install", () => self.skipWaiting());

self.addEventListener("activate", e => {
  e.waitUntil((async () => {
    /* 先清缓存再注销。反过来的话，注销之后这个 worker 可能随时被终止，
       缓存就留在那儿了 —— 而留在那儿就等于「容器停了还能打开」。 */
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map(k => caches.delete(k)));
    } catch (_) {}
    try { await self.registration.unregister(); } catch (_) {}
  })());
});
