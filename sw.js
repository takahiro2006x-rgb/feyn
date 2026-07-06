// Feyn PWA用サービスワーカー
// 方針: API通信・ページ遷移は常にネットワークから取得し、キャッシュしない
//       （チャットは動的なやりとりなので、古い応答を返してしまうと事故になる）
//       静的アセット（CSS/JS）だけキャッシュファーストにする
const CACHE_NAME = 'feyn-static-v1';
const STATIC_ASSETS = ['/style.css', '/script.js'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // APIやページ遷移（HTML）は常に最新をネットワークから取る
  if (url.pathname.startsWith('/api/') || event.request.mode === 'navigate') {
    return;
  }

  // 静的アセットのみキャッシュファースト
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
