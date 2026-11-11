/* 학교 공지 카드 (DESIGN §16.7) — 홍익대 대학원·기계공학과 공지를 서버(/api/notices)에서 받아 사이드바에 표시.
   app.js 와 독립(전역 의존 없음, reading.js 와 같은 즉시실행 방식). 날짜·보기(하루/주/월) 전환과 무관하게 항상 표시.
   백엔드가 아직 없거나(404) 오류(5xx)면 카드를 조용히 숨긴다 — 콘솔 에러를 내지 않는다.
   제목·태그는 textContent 로만 넣는다(innerHTML 금지, XSS 방지). */
(function () {
  var card = document.getElementById('card-notices');
  if (!card) return;
  var body = document.getElementById('notices-body');
  var badge = document.getElementById('notices-badge');
  var btnRefresh = document.getElementById('btn-notices-refresh');
  var footEl = document.getElementById('notices-foot');
  if (!body) return;

  var REFRESH_MS = 10 * 60 * 1000;   // 10분마다 갱신
  var expanded = false;              // '더 보기' 로 tier=all 까지 펼친 상태인지
  var ROW_CAP = 4;                   // §20: 접힌 상태에서 보여 주는 행 수
  var showAll = false;               // 'n건 더' 로 행 상한을 푼 상태인지 (새로고침하면 다시 접힘)
  var busy = false;                  // 수집/조회 진행 중
  var lastData = null;
  var reqSeq = 0;

  /* 본문 분석(§17): 펼침은 한 번에 하나만. 분석은 서버에 저장되므로 다시 누르면 즉시 표시된다. */
  var POLL_MS = 2500;                // 분석 진행 확인 주기
  var POLL_MAX = 80;                 // 2.5초 × 80 ≈ 200초에서 포기
  var openId = null;                 // 지금 펼쳐 둔 공지 id
  var pollTimer = null;              // 진행 확인 타이머
  var pollSeq = 0;                   // 오래된 폴링 무시용

  /* ── 유틸 ─────────────────────────────────────────────── */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }
  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); }
  function num(v) { return (typeof v === 'number' && isFinite(v)) ? v : null; }

  function fmtPosted(s) {
    // "YYYY-MM-DD" → "9.10" (올해가 아니면 "2025.9.10")
    if (!s || typeof s !== 'string') return '';
    var p = s.split('-');
    if (p.length !== 3) return s;
    var y = +p[0], m = +p[1], d = +p[2];
    if (!isFinite(y) || !isFinite(m) || !isFinite(d)) return s;
    var md = m + '.' + d;
    return (y === new Date().getFullYear()) ? md : (y + '.' + md);
  }
  function fmtFetch(ts) {
    var t = num(ts);
    if (!t) return '';
    var d = new Date(t * 1000), now = new Date();
    var hm = (d.getHours() < 10 ? '0' : '') + d.getHours() + ':' + (d.getMinutes() < 10 ? '0' : '') + d.getMinutes();
    var sameDay = d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
    return sameDay ? hm : ((d.getMonth() + 1) + '.' + d.getDate() + ' ' + hm);
  }
  function ddayText(n) {
    if (n === 0) return '오늘 마감';
    if (n > 0) return 'D-' + n;
    return '마감 지남';
  }

  /* 태그별 점 색 — 데이터 구분용. 빨강 금지(§12.5), 마감 임박만 --amber */
  var TAG_CLASS = {
    '연구·실적': 'nt-t-res',
    '장학': 'nt-t-sch',
    '근로·조교': 'nt-t-job'
  };

  /* ── 서버 통신 ───────────────────────────────────────── */
  function getJSON(url) {
    return fetch(url, { headers: { 'Accept': 'application/json' } }).then(function (r) {
      if (!r.ok) return null;                       // 404/500 → 조용히 없음 처리
      return r.json().catch(function () { return null; });
    }).catch(function () { return null; });
  }
  function postJSON(url, obj) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj || {})
    }).then(function (r) {
      if (!r.ok) return null;
      return r.json().catch(function () { return null; });
    }).catch(function () { return null; });
  }

  /* ── 정렬: 서버 순서를 유지하되 마감 지난 항목만 맨 아래로 ── */
  function ordered(items) {
    var live = [], past = [];
    for (var i = 0; i < items.length; i++) {
      var dd = num(items[i].d_day);
      if (dd !== null && dd < 0) past.push(items[i]); else live.push(items[i]);
    }
    return live.concat(past);
  }

  /* ── 렌더 ─────────────────────────────────────────────── */
  function markReadLocal(item, row) {
    if (item.read) return;
    item.read = true;
    if (row) row.className = 'nt-row nt-read';
    paintBadge();
    postJSON('/api/notices/read', { id: item.id });
  }

  /* 배지 숫자는 **지금 카드에 보이는 목록** 중 미열람 수. counts.unread 는 hide 를 뺀 전체(수백 건)라
     접힌 상태에서 쓰면 행을 다 읽어도 숫자가 안 줄어 보인다. */
  function unreadShown() {
    var items = (lastData && lastData.items) || [];
    var n = 0;
    for (var i = 0; i < items.length; i++) { if (!items[i].read) n++; }
    return n;
  }

  /* 새 글이 있으면 제목 옆에 n 배지, 없으면 '새 공지 없음'(.hint) */
  function paintBadge() {
    var zn = document.getElementById('zone-school-n');   // §29.5 구역 머리에 개수
    if (zn && lastData) { var unread = (lastData.items || []).filter(function (x) { return !x.read; }).length; zn.textContent = unread ? ('새 글 ' + unread) : ''; }
    if (!badge) return;
    var n = unreadShown();
    var any = !!(lastData && lastData.items && lastData.items.length);
    if (n) {
      badge.className = 'nt-badge';
      badge.title = '읽지 않은 공지';
      badge.textContent = String(n);
      badge.hidden = false;
    } else if (any) {
      badge.className = 'hint-inline nt-none';
      badge.title = '';
      badge.textContent = '새 공지 없음';
      badge.hidden = false;
    } else {
      badge.textContent = '';
      badge.hidden = true;
    }
  }

  /* ── 본문 분석 펼침 (§17) ─────────────────────────────── */
  var VERDICT_CLASS = {                       // 빨강 금지(§12.5) — 불가/해당없음도 회색
    '가능': 'nt-v-ok',
    '확인필요': 'nt-v-chk',
    '불가': 'nt-v-no',
    '해당없음': 'nt-v-no'
  };
  function verdictClass(v) { return VERDICT_CLASS[v] || 'nt-v-no'; }

  function stopPoll() { pollSeq++; if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; } }

  function closeExp() {
    stopPoll();
    openId = null;
    var old = body.querySelector('.nt-exp');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    var lit = body.querySelector('.nt-item.nt-open');
    if (lit) lit.className = lit.className.replace(/\s*\bnt-open\b/, '');
    var btns = body.querySelectorAll('.nt-analyze');
    for (var i = 0; i < btns.length; i++) btns[i].setAttribute('aria-expanded', 'false');
  }

  /* 라벨 + 값 한 줄. 값이 비어 있으면 아무것도 만들지 않는다. */
  function field(parent, label, value) {
    if (!value) return;
    var r = el('div', 'nt-f');
    r.appendChild(el('span', 'nt-f-k', label));
    r.appendChild(el('span', 'nt-f-v', String(value)));
    parent.appendChild(r);
  }

  function renderAnalysis(box, item, d) {
    clear(box);
    var a = d && d.analysis;

    if (!a) {
      if (d && d.available === false) {
        box.appendChild(el('div', 'hint', 'claude 명령을 찾지 못해 분석 기능을 쓸 수 없습니다.'));
      } else if (d && !d.body) {
        box.appendChild(el('div', 'hint', '본문을 가져오지 못했습니다. 원문을 열어 확인하세요.'));
      } else {
        box.appendChild(el('div', 'hint', '분석 결과를 받지 못했습니다. 다시 눌러 보세요.'));
      }
    } else {
      var head = el('div', 'nt-verdict');
      var vb = el('span', 'nt-v ' + verdictClass(a.verdict), a.verdict || '확인필요');
      head.appendChild(vb);
      if (a.summary) head.appendChild(el('span', 'nt-v-sum', a.summary));
      box.appendChild(head);

      if (a.why) box.appendChild(el('div', 'nt-why', a.why));

      var fields = el('div', 'nt-fields');
      field(fields, '대상', a.target);
      field(fields, '금액', a.amount);
      field(fields, '기간', a.period);
      field(fields, '제출처', a.how);
      if (a.docs && a.docs.length) field(fields, '서류', a.docs.join(' · '));
      if (fields.firstChild) box.appendChild(fields);

      if (a.actions && a.actions.length) {
        var t = el('div', 'nt-sub-h', '할 일');
        box.appendChild(t);
        var ul = el('ul', 'nt-todo');
        for (var i = 0; i < a.actions.length; i++) ul.appendChild(el('li', null, a.actions[i]));
        box.appendChild(ul);
      }
      if (a.caution) {
        var c = el('div', 'nt-caution');
        c.appendChild(el('span', 'nt-caution-k', '주의'));
        c.appendChild(el('span', null, a.caution));
        box.appendChild(c);
      }
    }

    var foot = el('div', 'nt-exp-foot');
    if (d && d.files && d.files.length) {
      foot.appendChild(el('span', 'nt-files', '첨부 ' + d.files.join(' · ')));
    }
    if (a && d && d.available !== false) {          // 프로필을 고쳤을 때 다시 판단시키는 길
      var redo = el('button', 'act nt-redo', '다시 분석');
      redo.type = 'button';
      redo.title = 'notices.json 의 profile 을 고쳤다면 다시 판단합니다';
      redo.addEventListener('click', function () { startAnalyze(box, item, d); });
      foot.appendChild(redo);
    }
    var link = el('a', 'act nt-open-src', '원문 열기');
    link.href = (d && d.url) || item.url || '#';
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    foot.appendChild(link);
    box.appendChild(foot);
  }

  function setBusyLabel(btn, on, item) {
    if (!btn) return;
    btn.disabled = !!on;
    btn.textContent = on ? '분석 중…' : (item.has_analysis ? '분석 결과' : '분석');
  }

  function analyzeBtn(item) {
    var w = body.querySelector('.nt-item[data-id="' + item.id + '"]');
    return w ? w.querySelector('.nt-analyze') : null;
  }

  /* 분석을 걸고 끝날 때까지 지켜본다. 이미 결과가 있는 글에서 부르면 새로 판단해 덮어쓴다. */
  function startAnalyze(box, item, prev) {
    var btn = analyzeBtn(item);
    var seq = pollSeq;
    clear(box);
    box.appendChild(el('div', 'hint', '공지를 읽고 판단하는 중… 30초쯤 걸립니다.'));
    setBusyLabel(btn, true, item);
    postJSON('/api/notices/analyze', { id: item.id }).then(function (r) {
      if (seq !== pollSeq || openId !== item.id) return;
      if (!r || (r.started === false && r.analyzing !== true)) {
        setBusyLabel(btn, false, item);
        renderAnalysis(box, item, (r && r.available === false) ? { available: false } : prev);
        return;
      }
      poll(box, item, btn, POLL_MAX, seq);
    });
  }

  /* 분석이 끝날 때까지 detail 을 되물어 본다. 끝나거나 포기하면 그리고 멈춘다. */
  function poll(box, item, btn, left, seq) {
    pollTimer = setTimeout(function () {
      if (seq !== pollSeq || openId !== item.id) return;
      getJSON('/api/notices/detail?id=' + item.id).then(function (d) {
        if (seq !== pollSeq || openId !== item.id) return;
        if (d && d.analysis) {
          item.has_analysis = true;
          item.verdict = d.analysis.verdict;
          setBusyLabel(btn, false, item);
          renderAnalysis(box, item, d);
          paintVerdictDot(item);
          return;
        }
        if (d && d.available === false) {
          setBusyLabel(btn, false, item);
          renderAnalysis(box, item, d);
          return;
        }
        if (left <= 0 || !d || (d.analyzing === false && d.analysis == null)) {
          setBusyLabel(btn, false, item);
          renderAnalysis(box, item, d);
          return;
        }
        poll(box, item, btn, left - 1, seq);
      });
    }, POLL_MS);
  }

  function paintVerdictDot(item) {
    var wrap = body.querySelector('.nt-item[data-id="' + item.id + '"]');
    if (!wrap) return;
    var old = wrap.querySelector('.nt-vdot');
    if (old && old.parentNode) old.parentNode.removeChild(old);
    if (!item.verdict) return;
    var line = wrap.querySelector('.nt-line');
    var a = wrap.querySelector('.nt-title');
    if (!line || !a) return;
    var dot = el('i', 'nt-vdot ' + verdictClass(item.verdict));
    dot.title = '분석: ' + item.verdict;
    line.insertBefore(dot, a);
  }

  function openExp(item, wrap, btn) {
    closeExp();
    openId = item.id;
    var seq = ++pollSeq;
    wrap.className = 'nt-item nt-open';
    btn.setAttribute('aria-expanded', 'true');
    var box = el('div', 'nt-exp');
    box.appendChild(el('div', 'hint', '본문을 읽는 중…'));
    wrap.appendChild(box);

    getJSON('/api/notices/detail?id=' + item.id).then(function (d) {
      if (seq !== pollSeq || openId !== item.id) return;
      if (d && d.analysis) {                       // 이미 분석돼 있으면 즉시 표시
        item.has_analysis = true;
        item.verdict = d.analysis.verdict;
        setBusyLabel(btn, false, item);
        renderAnalysis(box, item, d);
        paintVerdictDot(item);
        return;
      }
      if (!d || d.available === false) {
        setBusyLabel(btn, false, item);
        renderAnalysis(box, item, d);
        return;
      }
      startAnalyze(box, item, d);
    });
  }

  function makeRow(item) {
    var wrap = el('div', 'nt-item');
    wrap.setAttribute('data-id', String(item.id));
    var row = el('div', 'nt-row' + (item.read ? ' nt-read' : ''));

    var line = el('div', 'nt-line');
    if (item.tag) {
      var tag = el('span', 'nt-tag ' + (TAG_CLASS[item.tag] || 'nt-t-etc'));
      tag.appendChild(el('i', 'nt-dot'));
      tag.appendChild(el('span', null, item.tag));       // textContent
      line.appendChild(tag);
    }
    if (item.verdict) {                                   // 이미 분석된 글은 작은 판정 점
      var vdot = el('i', 'nt-vdot ' + verdictClass(item.verdict));
      vdot.title = '분석: ' + item.verdict;
      line.appendChild(vdot);
    }

    var a = el('a', 'nt-title');
    a.textContent = String(item.title == null ? '' : item.title);   // textContent (XSS 방지)
    a.href = item.url || '#';
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
    a.title = a.textContent;
    a.addEventListener('click', function (e) {
      if (e.button === 0 && !e.ctrlKey && !e.metaKey && !e.shiftKey && !e.altKey) {
        e.preventDefault();
        if (item.url) window.open(item.url, '_blank', 'noopener');
      }
      markReadLocal(item, row);
    });
    a.addEventListener('auxclick', function (e) {        // 가운데클릭: 기본 동작 그대로 두고 열람만 기록
      if (e.button === 1) markReadLocal(item, row);
    });
    line.appendChild(a);

    var an = el('button', 'act nt-analyze', item.has_analysis ? '분석 결과' : '분석');
    an.type = 'button';
    an.title = item.has_analysis
      ? '분석 결과를 펼칩니다'
      : '공지 본문을 읽어 내가 신청할 수 있는지 판단합니다';
    an.setAttribute('aria-expanded', 'false');
    an.addEventListener('click', function () {
      if (openId === item.id) { closeExp(); setBusyLabel(an, false, item); return; }
      openExp(item, wrap, an);
    });
    line.appendChild(an);

    var x = el('button', 'act nt-x', '×');
    x.type = 'button';
    x.title = '열람 처리(원문은 열지 않음)';
    x.setAttribute('aria-label', '열람 처리');
    x.addEventListener('click', function () { markReadLocal(item, row); });
    line.appendChild(x);
    row.appendChild(line);

    var meta = el('div', 'nt-meta');
    var bits = [];
    if (item.board_name) bits.push(String(item.board_name));
    var pd = fmtPosted(item.posted);
    if (pd) bits.push(pd);
    meta.appendChild(el('span', null, bits.join(' · ')));
    var dd = num(item.d_day);
    if (dd !== null && item.deadline) {
      var cls = 'nt-dday' + (dd < 0 ? ' nt-past' : (dd <= 3 ? ' nt-soon' : ''));
      var dspan = el('span', cls, ddayText(dd));
      dspan.title = '마감 ' + String(item.deadline);
      meta.appendChild(dspan);
    }
    row.appendChild(meta);
    wrap.appendChild(row);
    return wrap;
  }

  function render(d) {
    lastData = d;
    card.hidden = false;
    var items = ordered((d && d.items) ? d.items : []);

    stopPoll();
    openId = null;          // 목록을 새로 그리면 펼침도 사라진다(자동 갱신은 펼친 동안 멈춘다)
    clear(body);
    if (!items.length) {
      body.appendChild(el('div', 'hint', '공지 없음'));
    } else {
      var n = showAll ? items.length : Math.min(items.length, ROW_CAP);
      for (var i = 0; i < n; i++) body.appendChild(makeRow(items[i]));
    }
    paintBadge();
    paintFoot(d, items.length);
  }

  function paintFoot(d, shownTotal) {
    if (!footEl) return;
    clear(footEl);
    var counts = (d && d.counts) ? d.counts : {};
    var more = num(counts.info) || 0;

    // §20: 행 상한 — 접힌 상태면 'n건 더', 푼 상태면 '접기'
    var hidden = (shownTotal || 0) - ROW_CAP;
    if (!showAll && hidden > 0) {
      var cap = el('button', 'act nt-more', hidden + '건 더');
      cap.type = 'button';
      cap.addEventListener('click', function () { showAll = true; if (lastData) render(lastData); });
      footEl.appendChild(cap);
    } else if (showAll && (shownTotal || 0) > ROW_CAP) {
      var fold = el('button', 'act nt-more', '접기');
      fold.type = 'button';
      fold.addEventListener('click', function () { showAll = false; if (lastData) render(lastData); });
      footEl.appendChild(fold);
    }

    if (!expanded && more > 0) {
      var link = el('button', 'act nt-more', '학사·등록 공지 ' + more + '건 더 보기');
      link.type = 'button';
      link.addEventListener('click', function () { expanded = true; load(false); });
      footEl.appendChild(link);
    } else if (expanded) {
      var back = el('button', 'act nt-more', '접기');
      back.type = 'button';
      back.addEventListener('click', function () { expanded = false; load(false); });
      footEl.appendChild(back);
    }

    var notes = [];
    if (d && d.error) notes.push('수집 실패');
    var ft = fmtFetch(d && d.last_fetch);
    if (ft) notes.push(ft + ' 기준');
    if (notes.length) {
      var h = el('span', 'hint nt-foot-note', notes.join(' · '));
      if (d && d.error) h.title = String(d.error);
      footEl.appendChild(h);
    }
  }

  /* ── 조회 / 수집 ─────────────────────────────────────── */
  function load(quiet) {
    var seq = ++reqSeq;
    var url = '/api/notices?tier=' + (expanded ? 'all' : 'top') + '&limit=30';
    return getJSON(url).then(function (d) {
      if (seq !== reqSeq) return;
      if (!d || !d.items) {                 // 백엔드 없음/오류 → 조용히 숨김
        if (!lastData) card.hidden = true;
        return;
      }
      render(d);
    });
  }

  if (btnRefresh) {
    var REFRESH_LABEL = btnRefresh.textContent || '새로고침';
    var doneRefresh = function () {
      busy = false;
      btnRefresh.textContent = REFRESH_LABEL;
      btnRefresh.disabled = false;
    };
    btnRefresh.addEventListener('click', function () {
      if (busy) return;
      busy = true;
      btnRefresh.textContent = '가져오는 중…';
      btnRefresh.disabled = true;
      postJSON('/api/notices/refresh', {}).then(function () {
        return load(true);
      }).then(doneRefresh, doneRefresh);
    });
  }

  /* 펼쳐 둔 항목이 있으면 자동 갱신을 미룬다 — 읽는 중에 목록이 다시 그려지면 펼침이 닫힌다. */
  setInterval(function () { if (!busy && openId === null) load(true); }, REFRESH_MS);
  setTimeout(function () { load(true); }, 700);
})();
