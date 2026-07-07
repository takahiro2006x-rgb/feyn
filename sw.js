// Feyn PWA用サービスワーカー
// 方針: API通信・ページ遷移は常にネットワークから取得し、キャッシュしない
//       （チャットは動的なやりとりなので、古い応答を返してしまうと事故になる）
//       静的アセット（CSS/JS）は「ネットワース優先・失敗時のみキャッシュ」にする
//       （開発中に頻繁に更新されるため、キャッシュファーストだと古い版が残り続けてしまう）
const CACHE_NAME = 'feyn-static-v2';
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

  // 静的アセットはネットワーク優先。オフライン時だけキャッシュにフォールバックする
  event.respondWith(
    fetch(event.request)
      .then((res) => {
        const clone = res.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        return res;
      })
      .catch(() => caches.match(event.request))
  );
});
