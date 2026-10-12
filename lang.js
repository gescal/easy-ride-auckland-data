// 語言：?lang=en|hant|hans，否則跟瀏覽器語言（繁中／簡中／其他→英文）
(function(){
  var q=(location.search.match(/lang=(en|hant|hans)/)||[])[1];
  var n=(navigator.language||'en').toLowerCase();
  var auto=/^zh-(cn|sg|hans)/.test(n)?'hans':(/^zh/.test(n)?'hant':'en');
  var lang=q||auto;
  function set(l){document.documentElement.setAttribute('data-show',l);
    document.documentElement.lang={en:'en',hant:'zh-Hant',hans:'zh-Hans'}[l];
    var b=document.querySelectorAll('.langs button');
    for(var i=0;i<b.length;i++)b[i].setAttribute('aria-pressed',b[i].getAttribute('data-set')===l);}
  set(lang);
  document.addEventListener('click',function(e){var t=e.target.closest&&e.target.closest('[data-set]');if(t)set(t.getAttribute('data-set'));});
  // 信箱不直接寫在網頁原始碼裡，減少被垃圾信程式抓走
  var u='wellgescal',d='gmail.com',m=u+'@'+d;
  document.addEventListener('DOMContentLoaded',function(){
    set(document.documentElement.getAttribute('data-show')||lang);
    var s=document.querySelectorAll('.email');
    for(var i=0;i<s.length;i++){var a=document.createElement('a');a.href='mailto:'+m+'?subject=Easy%20Ride%20Auckland';a.textContent=m;s[i].textContent='';s[i].appendChild(a);}
  });
})();
