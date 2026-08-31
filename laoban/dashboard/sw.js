/* 老板驾驶舱 Service Worker：应用壳缓存——离线能打开壳（页面骨架 + 图标），
   数据一律走网络（台账永远现拉，绝不给离线旧数据当真账看）。 */
const SHELL = 'laoban-shell-v1';
const SHELL_ASSETS = ['/', '/manifest.json', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(SHELL).then((c) => c.addAll(SHELL_ASSETS))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  // API 不缓存；非同源 / 非 GET 原样放行
  if (url.pathname.startsWith('/api/') || e.request.method !== 'GET'
      || url.origin !== self.location.origin) {
    return;
  }
  // 网络优先：在线拿到最新壳并顺手更新缓存；离线才回退缓存（最后兜底 /）
  e.respondWith(
    fetch(e.request).then((resp) => {
      if (resp.ok && (e.request.mode === 'navigate'
                      || SHELL_ASSETS.includes(url.pathname))) {
        const copy = resp.clone();
        caches.open(SHELL).then((c) => c.put(e.request, copy));
      }
      return resp;
    }).catch(() =>
      caches.match(e.request).then((hit) => hit || caches.match('/')))
  );
});
