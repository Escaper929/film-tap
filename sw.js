/* fetch 是「缓存优先」：命中就不再回源。所以**只要改了 SHELL 里的任何文件，
   就必须把这个版本号 +1** —— 浏览器是靠字节比对发现 sw.js 变了才装新 SW 的，
   新 SW 的 activate 会删掉旧 cache，页面才会拿到新版。
   忘了改的后果：手机上永远停在旧版界面，而且不报任何错。

   光靠"记得改"是不够的（这个坑真踩过：改了 index.html 却没动 sw.js，
   NAS 上的镜像明明是新版，手机上还是旧版）。所以下面再放一个 SHELL_FP ——
   SHELL 里那些文件的内容指纹，`_selftest.js` 会算一遍比对。
   对不上就自检失败，逼你改这一行；改了这一行 sw.js 的字节就变了，
   浏览器才会去装新 SW。版本号和指纹两个都要动。

   ⚠️ 现在镜像由 watchtower 自动更新，这一条比以前**更要紧**：
      更新是无人值守发生的，没人盯着“手机上是不是还是旧版”。 */
const CACHE = "filmtap-v20";
const SHELL = ["./", "./index.html", "./manifest.webmanifest"];
/* SHA-256(index.html + "\n" + manifest.webmanifest)，按换行归一化后算。
   算错不要紧：跑 `node _selftest.js` 会把正确的值打出来，照抄即可。 */
const SHELL_FP = "f16655c4cfc80aefbe687e5fcf6db09aae03a90f1fcd9900461a20a4a0cf7cf0";

self.addEventListener("install", e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()).catch(() => {})
  );
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  if (new URL(req.url).origin !== location.origin) return;
  /* ⚠️ /api/ 一律进不了这一层。它是**数据**，不是壳 ——
     被缓存住的话下次打开会拿到一份过期的数据；
     更糟的是离线时下面那个 .catch 会把 index.html 当成接口的响应返回，
     于是 fetch 拿到 200 + 一坨 HTML，页面以为「读成功了」。
     两种都很难查。这个 SW 现在**只服务于自建后端这一种形态**
     （页面和 /api/ 同一个进程发出），这一条比什么时候都关键。 */
  if (new URL(req.url).pathname.indexOf("/api/") === 0) return;

  e.respondWith(
    caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res && res.status === 200 && res.type === "basic") {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
      }
      return res;
    }).catch(() => caches.match("./index.html")))
  );
});
