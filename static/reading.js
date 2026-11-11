/* 논문 읽기 카드 — Athenaeum(localhost:8770)의 읽기 기록을 서버 프록시(/api/reading)로 받아 사이드바에 표시.
   app.js 와 독립: 보기(하루/주/월) 버튼과 날짜 입력, 날짜 라벨의 변화를 감시해 그 기간의 기록을 다시 받는다.
   하루 보기 = 그 날, 주 보기 = 월~일 합계, 월 보기 = 그 달 합계. Athenaeum 이 꺼져 있으면 카드를 숨긴다. */
(function () {
  var card = document.getElementById('card-reading');
  if (!card) return;
  var body = document.getElementById('reading-body');
  var title = card.querySelector('.sec');
  var lastKey = null;

  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function dateStr(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
  function parseDate(s) { var p = s.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function shift(s, days) { var d = parseDate(s); d.setDate(d.getDate() + days); return dateStr(d); }
  function fmtDur(sec) {
    sec = sec || 0;
    if (sec < 60) return sec + '초';
    if (sec < 3600) return Math.round(sec / 60) + '분';
    return Math.floor(sec / 3600) + '시간 ' + Math.round(sec % 3600 / 60) + '분';
  }
  function esc(s) { return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }

  function currentView() {
    var b = document.querySelector('.seg-btn.active[data-view]');
    return b ? b.getAttribute('data-view') : 'day';
  }
  function currentDate() {
    var inp = document.getElementById('date-input');
    return inp && inp.value ? inp.value : null;
  }
  function period(view, date) {
    if (view === 'week') { var d = parseDate(date); d.setDate(d.getDate() - (d.getDay() + 6) % 7); var ws = dateStr(d); return { start: ws, end: shift(ws, 6) }; }
    if (view === 'month') { var m = parseDate(date); return { start: dateStr(new Date(m.getFullYear(), m.getMonth(), 1)), end: dateStr(new Date(m.getFullYear(), m.getMonth() + 1, 0)) }; }
    return { start: date, end: date };
  }
  function titleFor(view) {
    return view === 'week' ? '논문 읽기 · 이번 주' : view === 'month' ? '논문 읽기 · 이번 달' : '논문 읽기';
  }

  function render(d, view) {
    if (!d || d.available === false) { card.hidden = true; return; }
    card.hidden = false;
    if (title) title.innerHTML = esc(titleFor(view)) + ' <span class="hint-inline">Athenaeum 에서 읽은 시간</span>';
    var papers = d.papers || [];
    if (!papers.length) {
      body.innerHTML = '<div class="hint">' + (view === 'day' ? '이 날 읽은 논문 없음' : '이 기간에 읽은 논문 없음') + '</div>';
      return;
    }
    var html = '<div class="reading-total">' + papers.length + '편 · ' + fmtDur(d.sec) +
      (view !== 'day' && d.days_read ? ' · ' + d.days_read + '일' : '') + '</div>';
    papers.forEach(function (p) {
      html += '<div class="reading-row" title="' + esc(p.title) + '">' +
        '<span class="reading-title">' + esc(p.short) + '</span>' +
        '<span class="reading-sec">' + fmtDur(p.sec) + '</span></div>';
    });
    body.innerHTML = html;
  }

  function refresh(force) {
    var view = currentView(), date = currentDate();
    if (!date) { card.hidden = true; lastKey = null; return; }
    var p = period(view, date);
    var key = view + '|' + p.start + '|' + p.end;
    if (!force && key === lastKey) return;
    lastKey = key;
    var url = view === 'day' ? '/api/reading?date=' + encodeURIComponent(date)
                             : '/api/reading?start=' + encodeURIComponent(p.start) + '&end=' + encodeURIComponent(p.end);
    fetch(url)
      .then(function (r) { return r.json(); })
      .then(function (d) { if (key === lastKey) render(d, view); })
      .catch(function () { card.hidden = true; });
  }

  // 보기 전환·날짜 이동 감시: 날짜 라벨 텍스트와 보기 버튼의 active 클래스 변화 → 갱신. 60초마다 자동 갱신.
  var label = document.getElementById('date-label');
  if (label) new MutationObserver(function () { setTimeout(function () { refresh(false); }, 50); }).observe(label, { childList: true, characterData: true, subtree: true });
  var seg = document.getElementById('view-seg');
  if (seg) new MutationObserver(function () { setTimeout(function () { refresh(false); }, 50); }).observe(seg, { attributes: true, attributeFilter: ['class'], subtree: true });
  var inp = document.getElementById('date-input');
  if (inp) inp.addEventListener('change', function () { setTimeout(function () { refresh(false); }, 50); });
  setInterval(function () { refresh(true); }, 60000);
  setTimeout(function () { refresh(true); }, 800);
})();
