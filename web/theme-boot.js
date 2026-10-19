/* 首屏前定明暗：app.js 要等 DOMContentLoaded 才跑，晚一步就是深色模式下
   每次进子目录都先闪一下白。这个文件必须同步加载、且排在 app.css 之前。 */
(function () {
  try {
    var d = document.documentElement, s = window.localStorage;
    var theme = s.getItem('lanshare.theme') || 'light';
    if (theme === 'auto') {
      theme = (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
        ? 'dark' : 'light';
    }
    d.setAttribute('data-theme', theme);
    d.setAttribute('data-palette', s.getItem('lanshare.palette') || 'mint');
    d.setAttribute('data-glass', s.getItem('lanshare.glass') || 'normal');
  } catch (e) { }
})();
