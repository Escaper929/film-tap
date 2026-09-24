/* fetch 是「缓存优先」：命中就不再回源。所以**只要改了 SHELL 里的任何文件，
   就必须把这个版本号 +1** —— 浏览器是靠字节比对发现 sw.js 变了才装新 SW 的，
   新 SW 的 activate 会删掉旧 cache，页面才会拿到新版。
   忘了改的后果：手机上永远停在旧版界面，而且不报任何错。 */
const CACHE = "filmtap-v2";
const SHELL = ["./", "./index.html", "./manifest.webmanifest"];

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
