/* Orbit(옛 Horarium, 출근기록) — 프론트엔드 (바닐라 JS, ES2017, 외부 리소스 없음) */
(function () {
  'use strict';

  // ------------------------------------------------------------ 상수
  // 시간축 배율은 상수가 아니라 state.pxPerMin (DESIGN §14). 범위 0.2~6, 저장 키 worklog.pxPerMin
  var PX_MIN = 0.2, PX_MAX = 6;
  var ZOOM_KEY = 'worklog.pxPerMin';
  var ZOOM_STEP_WHEEL = 1.15, ZOOM_STEP_BTN = 1.3;
  var GUTTER = 48;        // 좌측 눈금 열 폭
  var PAD_TOP = 14;
  var PAD_BOTTOM = 14;
  var RECT_PAD_RIGHT = 12;
  var DAY_REFRESH_MS = 30000;
  var SIDE_REFRESH_MS = 60000;   // §27 옆 판(할 일·메모·밤 일정) 다시 받기 — 포커스 없는 창만
  var STATUS_REFRESH_MS = 10000;
  var NOTE_DEBOUNCE_MS = 1000;

  var MOCK = /[?&]mock=1/.test(location.search);

  // 서버 응답이 오기 전 기본값 (서버 categories 가 오면 덮어씀)
  var DEFAULT_CATEGORIES = [
    { id: 'focus', name: '집중', color: '#3B82F6', kind: 'active', manual: true },
    { id: 'experiment', name: '실험', color: '#22C55E', kind: 'active', manual: true },
    { id: 'meeting', name: '회의', color: '#14B8A6', kind: 'active', manual: true },
    { id: 'break', name: '휴식', color: '#F59E0B', kind: 'active', manual: true },
    { id: 'distraction', name: '딴짓', color: '#EF4444', kind: 'active', manual: true },
    { id: 'neutral', name: '중립', color: '#CBD5E1', kind: 'active', manual: true },
    { id: 'unknown', name: '미분류', color: '#9CA3AF', kind: 'active', manual: false },
    { id: 'away', name: '자리비움', color: '#E5E7EB', kind: 'system', manual: false },
    { id: 'absent', name: '부재', color: '#F8FAFC', kind: 'system', manual: false },
    { id: 'remote', name: '원격', color: '#A855F7', kind: 'system', manual: false },
    { id: 'paused', name: '일시정지', color: '#F3F4F6', kind: 'system', manual: false },
    { id: 'off', name: '꺼짐', color: '#FFFFFF', kind: 'system', manual: false }
  ];

  // §19 밤 일정 종류. 서버가 /api/night 의 kinds 로 내려주지만 첫 렌더 전 기본값이 필요하다.
  var DEFAULT_NIGHT_KINDS = [
    { id: 'appointment', name: '약속', ask: true }, { id: 'english', name: '영어공부', ask: true },
    { id: 'workout', name: '운동', ask: true }, { id: 'study', name: '공부', ask: true }, { id: 'research', name: '연구', ask: false },
    { id: 'rest', name: '쉼', ask: false }
  ];

  // ------------------------------------------------------------ 전역 상태
  var state = {
    date: null,        // 'YYYY-MM-DD' (표시 중인 날짜)
    day: null,         // /api/day 응답
    status: null,      // /api/status 응답
    drawer: null,      // §35 서랍 목록
    drawerEdit: null,  // §35 고치는 중인 항목 id
    drawerFile: null,  // §35 저장 대기 중인 파일
    devices: null,     // §36 다른 기기 설정 보기
    remote: false,     // §36 이 화면을 이 PC 밖(테일넷)에서 열었나
    categories: themedCats(DEFAULT_CATEGORIES),
    config: { display_start_hour: 6, display_end_hour: 24, day_start_hour: 4, night_start_hour: 20, night_prompt_hour: 16, budget_card: false, anki_card: false },
    todos: null,
    budget: null,
    period: null,
    schedule: null,
    events: null,
    eventsPoll: null,
    anki: null,
    scheduleAll: false,
    night: null,      // /api/night 응답 (§19)
    popover: null,     // 열려 있는 팝오버 컨텍스트
    dayPending: false, // 팝오버 때문에 미뤄진 새로고침 여부
    scrolledOnce: false,
    renderedAsToday: false, // 마지막 헤더 렌더 시점에 '오늘' 이었는지 (04:00 경계 감지용)
    sideLoaded: null,       // 투두·메모를 마지막으로 불러온 기준 날짜(sideDate) — 주·월 보기 04:00 경계 감지용
    // --- 2차: 보기 전환 (§11.3)
    view: 'day',          // 'day' | 'week' | 'month'
    range: null,          // /api/range 응답 (주·월 보기)
    rangePending: false,  // 팝오버 때문에 미뤄진 range 새로고침 여부
    weekScrolledOnce: false,
    // --- §14: 시간축 배율 (하루·주 보기 공용). zoomFit=true 면 렌더마다 컨테이너 높이에 맞춰 다시 계산
    pxPerMin: 1,
    zoomFit: true
  };

  // ------------------------------------------------------------ DOM 헬퍼
  function $(id) { return document.getElementById(id); }
  function h(tag, attrs, children) {
    var el = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        if (k === 'text') el.textContent = attrs[k];
        else if (k === 'class') el.className = attrs[k];
        else if (k.indexOf('on') === 0 && typeof attrs[k] === 'function') el.addEventListener(k.slice(2), attrs[k]);
        else if (attrs[k] === false || attrs[k] === null || attrs[k] === undefined) { /* skip */ }
        else el.setAttribute(k, attrs[k]);
      });
    }
    (children || []).forEach(function (c) {
      if (c === null || c === undefined) return;
      el.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return el;
  }
  var SVG_NS = 'http://www.w3.org/2000/svg';
  function s(tag, attrs, children) {
    var el = document.createElementNS(SVG_NS, tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      if (k === 'text') el.textContent = attrs[k];
      else if (attrs[k] !== null && attrs[k] !== undefined) el.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) el.appendChild(c); });
    return el;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  // ------------------------------------------------------------ 시간 유틸
  function pad2(n) { return (n < 10 ? '0' : '') + n; }
  function hm(ts) {
    if (ts === null || ts === undefined) return '—';
    var d = new Date(ts * 1000);
    return pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }
  function hms(d) { return pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds()); }
  function mins(sec) { return Math.round((sec || 0) / 60); }
  function fmtHM(sec) {
    var m = mins(sec);
    var hh = Math.floor(m / 60), mm = m % 60;
    return hh > 0 ? hh + '시간 ' + mm + '분' : mm + '분';
  }
  function dateStr(d) { return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate()); }
  function parseDate(str) {
    var p = str.split('-');
    return new Date(+p[0], +p[1] - 1, +p[2]);
  }
  function shiftDate(str, days) {
    var d = parseDate(str);
    d.setDate(d.getDate() + days);
    return dateStr(d);
  }
  function todayStr() {
    var h0 = state.config.day_start_hour || 4;
    return dateStr(new Date(Date.now() - h0 * 3600 * 1000));
  }
  function isToday() { return state.date === todayStr(); }
  var WEEK = ['일', '월', '화', '수', '목', '금', '토'];
  function koreanDate(str) {
    var d = parseDate(str);
    return d.getFullYear() + '년 ' + (d.getMonth() + 1) + '월 ' + d.getDate() + '일 (' + WEEK[d.getDay()] + ')';
  }
  function nowTs() { return (state.status && state.status.now && !MOCK) ? Math.max(state.status.now, Date.now() / 1000) : Date.now() / 1000; }

  // §39.1 테마: 기본은 어둡게. 어두운 화면에서는 밝은 회색 계열 카테고리 여섯만 어두운 값으로 그린다(서버·DB 값은 그대로)
  var THEME = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  function themedCats(list) {
    if (document.documentElement.getAttribute('data-theme') === 'light') return list;   // state 초기화가 THEME 대입보다 먼저 돈다 — 속성을 직접 본다
    var dark = { neutral: '#4b556b', unknown: '#6b6f76', away: '#222327', absent: '#0d0e10', paused: '#17181b', off: '#08090a' };
    return (list || []).map(function (c) { return dark[c.id] ? Object.assign({}, c, { color: dark[c.id] }) : c; });
  }
  function toggleTheme() {
    try { localStorage.setItem('orbit.theme', THEME === 'dark' ? 'light' : 'dark'); } catch (e) { /* 저장 못 하면 그대로 */ }
    location.reload();
  }
  function catOf(id) {
    for (var i = 0; i < state.categories.length; i++) if (state.categories[i].id === id) return state.categories[i];
    return { id: id, name: id, color: '#9CA3AF', kind: 'active', manual: false };
  }
  function manualCategories() { return state.categories.filter(function (c) { return c.manual; }); }
  function baseName(p) {
    if (!p) return '';
    var i = Math.max(p.lastIndexOf('\\'), p.lastIndexOf('/'));
    return i >= 0 ? p.slice(i + 1) : p;
  }
  // §15 표시 이름: 서버가 span.label / apps[i].name / unknown_top[i].name 을 주면 그것, 없으면 exe 로 폴백
  // exe 가 없는 구간(부재·자리비움 등 창 없음)은 종전처럼 카테고리명만 — '(창 없음)' 을 막대마다 반복하지 않음
  function spanLabel(sp) { if (!sp || !sp.exe) return ''; return sp.label || baseName(sp.exe) || ''; }
  function appName(a) { return (a && a.name) || baseName(a && a.exe) || ''; }
  // 브라우저 접미사 · 카운터 제거 (제목 규칙 기본값)
  function cleanTitleKeyword(t) {
    var x = (t || '').trim();
    x = x.replace(/^\(\d+\)\s*/, '');
    var re = /\s+[-–—]\s+(Google Chrome|Chrome|Microsoft\s?Edge|Personal|Work|Profile \d+|Mozilla Firefox|Firefox|Whale|Brave|Opera)\s*$/i;
    var prev;
    do { prev = x; x = x.replace(re, ''); } while (x !== prev);
    return x.trim();
  }
  function textColorFor(hex) {
    var c = (hex || '#000000').replace('#', '');
    if (c.length !== 6) return '#fff';
    var r = parseInt(c.slice(0, 2), 16), g = parseInt(c.slice(2, 4), 16), b = parseInt(c.slice(4, 6), 16);
    var lum = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
    return lum > 0.68 ? '#1c1c1a' : '#FFFFFF';
  }

  // ------------------------------------------------------------ 토스트
  // §48 같은 문구가 이미 떠 있으면 새로 쌓지 않고 그 토스트의 시간만 늘린다. 한꺼번에 TOAST_MAX 개까지(오래된 것부터 뺀다).
  var TOAST_MAX = 3;
  function toast(msg, ms) {
    var wrap = $('toast-wrap');
    for (var i = 0; i < wrap.children.length; i++) {
      var k = wrap.children[i];
      if (k._hide && k.textContent === msg) { clearTimeout(k._hide); armToast(k, ms); return; }
    }
    while (wrap.children.length >= TOAST_MAX) wrap.removeChild(wrap.firstChild);
    var el = h('div', { class: 'toast', text: msg });
    wrap.appendChild(el);
    requestAnimationFrame(function () { el.classList.add('show'); });
    armToast(el, ms);
  }
  function armToast(el, ms) {
    el._hide = setTimeout(function () {
      el._hide = null;
      el.classList.remove('show');
      setTimeout(function () { if (el.parentNode) el.parentNode.removeChild(el); }, 250);
    }, ms || 3000);
  }

  // ------------------------------------------------------------ API
  // §48 GET 은 연결이 잠깐 끊겼을 때(서버 재시작 틈, 테일넷 프록시의 502·503·504) 조용히 다시 묻는다 — 0.4·1·2.5초 뒤.
  // POST 는 다시 보내지 않는다(두 번 적힐 수 있다).
  var GET_RETRY_MS = [400, 1000, 2500];
  var RETRY_STATUS = { 502: true, 503: true, 504: true };
  function fetchGet(path, opt, n) {
    function again() {
      return new Promise(function (res) { setTimeout(res, GET_RETRY_MS[n]); })
        .then(function () { return fetchGet(path, opt, n + 1); });
    }
    return fetch(path, opt).then(function (r) {
      return (RETRY_STATUS[r.status] && n < GET_RETRY_MS.length) ? again() : r;
    }, function (e) {
      if (n < GET_RETRY_MS.length) return again();
      throw e;
    });
  }
  var api = function (path, body) {
    if (MOCK) return mockApi(path, body);
    var opt = { method: 'GET', headers: {} };
    if (body !== undefined) {
      opt.method = 'POST';
      opt.headers['Content-Type'] = 'application/json; charset=utf-8';
      opt.body = JSON.stringify(body);
    }
    return (opt.method === 'GET' ? fetchGet(path, opt, 0) : fetch(path, opt)).then(function (r) {
      return r.json().catch(function () { return { error: 'HTTP ' + r.status }; }).then(function (j) {
        if (r.status === 401 && j && j.login) {          // §36 밖에서 연 화면의 로그인이 풀렸다 → 로그인 화면으로
          location.href = '/login?next=' + encodeURIComponent(location.pathname + location.search);
          throw new Error('로그인이 필요합니다');
        }
        if (j && j.error) throw new Error(j.error);
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return j;
      });
    });
  };
  // §48 연결 문제(502·503·504, 브라우저의 네트워크 오류)는 불러오는 것마다 따로 띄우지 않고 한 줄로 — toast 가 같은 문구를 묶는다
  var NET_ERR = /^HTTP 50[234]$|Failed to fetch|Load failed|NetworkError|network connection/i;
  function fail(prefix) {
    return function (e) {
      var msg = (e && e.message) ? e.message : String(e);
      toast(NET_ERR.test(msg) ? '서버에 잠깐 닿지 못했습니다 — 안 되면 새로 고침' : (prefix ? prefix + ': ' : '') + msg);
      console.warn(prefix, e);
    };
  }

  // ------------------------------------------------------------ 데이터 로드
  function loadDay(dateOpt) {
    var d = dateOpt !== undefined ? dateOpt : state.date;
    var path = '/api/day' + (d ? '?date=' + encodeURIComponent(d) : '');
    return api(path).then(function (day) {
      // 요청 당시 날짜와 지금 표시 중인 날짜가 다르면(◀ 연타로 응답 순서가 뒤바뀜) 늦게 온 응답은 버린다.
      // d 가 null 인 초기 로드는 state.date 가 아직 없을 때만 day.date 로 채운다.
      if (d !== null && d !== undefined && state.date !== null && d !== state.date) return;
      if ((d === null || d === undefined) && state.date !== null && day.date !== state.date) return;
      // 요청 뒤 주·월 보기로 넘어갔으면 늦게 온 하루 응답을 버린다(기간 헤더·요약을 덮어쓰지 않도록).
      // setView('day') 는 state.view 를 먼저 바꾼 뒤 loadDay 를 부르므로 정상 경로에는 영향 없음.
      if (state.view !== 'day') return;
      if (state.popover && d === state.date) {
        // 팝오버가 열린 채로 온 응답은 반영을 미룬다
        state.dayPending = true;
        return;
      }
      var dateChanged = state.date !== day.date;
      state.date = day.date;
      state.day = day;
      if (day.categories && day.categories.length) state.categories = themedCats(day.categories);
      if (day.config) {
        state.config = Object.assign({}, state.config, day.config);
        if (!state.configSeen) { state.configSeen = true; loadAnki(); }   // §31.6 하루 보기로 시작할 때는 여기서 설정이 처음 온다
      }
      if (day.status) { state.status = day.status; renderStatus(); }
      state.dayPending = false;
      renderDateHeader();
      renderTimeline(dateChanged);
      renderSummary();
      renderUnknownTop();
      renderLegend();
      return day;
    }).catch(fail('하루 기록 불러오기 실패'));
  }

  function loadStatus() {
    return api('/api/status').then(function (st) {
      state.status = st;
      renderStatus();
      if (state.view === 'week') { if (!state.popover) updateWeekNowLine(); return; }
      if (state.view !== 'day') return;
      if (isToday() && state.day && !state.popover) updateNowLine();
    }).catch(fail('상태 조회 실패'));
  }

  function loadTodos() {
    if (!state.date) return Promise.resolve();
    var d = sideDate();
    state.sideLoaded = d; // 투두·메모는 항상 함께 다시 불러오므로 여기서 '마지막으로 불러온 기준 날짜' 를 기록
    return api('/api/todos?date=' + encodeURIComponent(d)).then(function (t) {
      if (d !== sideDate()) return; // 늦게 온 이전 날짜 응답은 버림
      state.todos = t;
      renderTodos();
    }).catch(fail('할 일 불러오기 실패'));
  }

  function loadNight() {
    if (!state.date) return Promise.resolve();
    var d = sideDate();
    return api('/api/night?date=' + encodeURIComponent(d)).then(function (n) {
      if (d !== sideDate()) return; // 늦게 온 이전 날짜 응답은 버림
      state.night = n;
      renderNight();
    }).catch(fail('밤 일정 불러오기 실패'));
  }

  function budgetOn() { return !!(state.config && state.config.budget_card); }
  function loadBudget() {
    $('card-budget').hidden = !budgetOn();          // §21.8: 설정으로 켜고 끈다. 꺼져 있으면 조회도 안 한다
    if (!state.date || !budgetOn()) return Promise.resolve();
    var d = sideDate();
    return api('/api/budget?date=' + encodeURIComponent(d)).then(function (b) {
      if (d !== sideDate()) return; // 늦게 온 이전 날짜 응답은 버림
      state.budget = b;
      renderBudget();
    }).catch(fail('예산 불러오기 실패'));
  }

  // ------------------------------------------------------------ 서랍 (§35): 행을 누르면 클립보드로
  function loadDrawer() {
    return api('/api/drawer').then(function (r) {
      var items = (r && r.items) || [];
      var key = JSON.stringify(items);
      if (key === state.drawerKey) return;             // 창에 돌아올 때마다 받지만, 안 바뀌었으면 다시 그리지 않는다(복사 표시가 지워지지 않게)
      state.drawerKey = key;
      state.drawer = items;
      renderDrawer();
    }).catch(fail('서랍 불러오기 실패'));
  }
  function copyTextLegacy(t) {
    return new Promise(function (res, rej) {           // 테일넷 IP(보안 컨텍스트 아님)나 권한이 막힌 곳 → 옛 방식. 사용자 클릭 안에서만 통한다
      var ta = h('textarea', { style: 'position:fixed;top:0;left:0;opacity:0' });
      ta.value = t; document.body.appendChild(ta); ta.focus(); ta.select();
      var ok = false; try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      if (ok) res(); else rej(new Error('이 브라우저에서는 복사가 안 됩니다'));
    });
  }
  function copyText(t) {
    if (!(navigator.clipboard && window.isSecureContext)) return copyTextLegacy(t);
    return navigator.clipboard.writeText(t).catch(function (e) {
      return copyTextLegacy(t).catch(function () { throw e; });   // 둘 다 안 되면 원래 이유를 보여 준다
    });
  }
  function copyImage(url) {
    if (!(navigator.clipboard && window.ClipboardItem && window.isSecureContext)) {
      return Promise.reject(new Error('이 주소에서는 그림 복사가 안 됩니다 — 그림을 우클릭해 "이미지 복사"'));
    }
    return new Promise(function (res, rej) {           // canvas 로 PNG 로 바꿔 넣는다 (투명 배경 유지, jpg 도 됨)
      var img = new Image();
      img.onload = function () {
        var c = document.createElement('canvas');
        c.width = img.naturalWidth || img.width; c.height = img.naturalHeight || img.height;
        c.getContext('2d').drawImage(img, 0, 0);
        c.toBlob(function (b) {
          if (!b) { rej(new Error('그림을 PNG 로 못 바꿨습니다')); return; }
          navigator.clipboard.write([new ClipboardItem({ 'image/png': b })]).then(res, rej);
        }, 'image/png');
      };
      img.onerror = function () { rej(new Error('그림을 못 읽었습니다')); };
      img.src = url;
    });
  }
  function drawerCopy(it, li) {
    var p;
    if (it.kind === 'text') p = copyText(it.value);
    else if (it.is_image) p = copyImage(it.url);
    else { window.open(it.url, '_blank'); return; }
    p.then(function () {
      toast('복사했어요 — ' + it.label, 1800);
      li.classList.add('copied'); setTimeout(function () { li.classList.remove('copied'); }, 1200);
    }).catch(function (e) {
      if (it.kind === 'text') { fail('복사 실패')(e); return; }
      // 그림 복사를 브라우저가 막으면(권한·보안 컨텍스트) 돌아갈 길을 알려 준다 — '열기' 로 띄운 그림을 우클릭하면 된다
      toast('그림 복사가 막혔어요 — "열기" 로 띄운 뒤 그림을 우클릭 → 이미지 복사 (' + ((e && e.message) || e) + ')', 5000);
    });
  }
  function renderDrawer() {
    var ul = $('drawer-list');
    clear(ul);
    var items = state.drawer || [];
    if (!items.length) {
      ul.appendChild(h('li', { class: 'empty hint', text: '아직 비었어요 — "넣기"로 사업자번호·주소·사인을 넣어 두면 눌러서 복사합니다.' }));
      return;
    }
    items.forEach(function (it) {
      var v = it.kind === 'file'
        ? h('span', { class: 'drawer-v' }, [it.is_image ? h('img', { src: it.url, alt: it.label }) : null, h('span', { text: it.value || '파일' })])
        : h('span', { class: 'drawer-v' }, [h('span', { text: it.value })]);
      var kids = [
        h('button', { class: 'drawer-k', type: 'button', title: '이름·내용 고치기', text: it.label, onclick: function (e) { e.stopPropagation(); openDrawerAdd(it); } }),
        v
      ];
      if (it.kind === 'file') {
        kids.push(h('button', { class: 'act', type: 'button', text: '열기', title: '새 탭에서 엽니다', onclick: function (e) { e.stopPropagation(); window.open(it.url, '_blank'); } }));
        kids.push(h('button', { class: 'act drawer-reveal', type: 'button', text: '탐색기', title: '탐색기에서 이 파일을 선택해 엽니다 (업로드 창에 끌어 넣을 때)', onclick: function (e) {
          e.stopPropagation(); api('/api/drawer/reveal', { id: it.id }).catch(fail('탐색기 열기 실패'));
        } }));
      }
      kids.push(h('button', { class: 'act btn-del', type: 'button', title: '삭제', text: '×', onclick: function (e) {
        e.stopPropagation(); api('/api/drawer/delete', { id: it.id }).then(loadDrawer).catch(fail('삭제 실패'));
      } }));
      var li = h('li', { class: 'list-row drawer-row', title: it.kind === 'text' ? '누르면 복사' : (it.is_image ? '누르면 그림 복사' : '누르면 열기') }, kids);
      li.addEventListener('click', function () { drawerCopy(it, li); });
      ul.appendChild(li);
    });
  }
  var DRAWER_PH = '내용 — 글을 적거나, 파일… 을 고르거나, 그림을 여기 붙여넣기(Ctrl+V)';
  function openDrawerAdd(it) {
    state.drawerEdit = it ? it.id : null;
    state.drawerFile = null;
    var isFile = !!(it && it.kind === 'file');
    $('drawer-label').value = it ? it.label : '';
    $('drawer-value').value = (it && !isFile) ? it.value : '';
    $('drawer-value').disabled = isFile;
    $('drawer-value').placeholder = isFile ? '파일 항목은 이름만 고칠 수 있어요' : DRAWER_PH;
    $('btn-drawer-file').hidden = !!it;
    $('drawer-file-name').textContent = '';
    $('drawer-add').hidden = false;
    $('drawer-label').focus();
  }
  function closeDrawerAdd() {
    $('drawer-add').hidden = true;
    state.drawerEdit = null; state.drawerFile = null;
    $('drawer-file').value = '';
  }
  function setDrawerFile(f) {
    state.drawerFile = f || null;
    $('drawer-file-name').textContent = f ? ('파일: ' + (f.name || '붙여넣은 그림')) : '';
  }
  function saveDrawer() {
    var label = $('drawer-label').value.trim();
    var value = $('drawer-value').value.trim();
    var done = function () { closeDrawerAdd(); return loadDrawer(); };
    if (state.drawerEdit !== null) {
      var body = { id: state.drawerEdit, label: label };
      if (!$('drawer-value').disabled) body.value = value;
      api('/api/drawer/update', body).then(done).catch(fail('고치기 실패'));
      return;
    }
    var f = state.drawerFile;
    if (f) {
      var rd = new FileReader();
      rd.onload = function () {
        var s = String(rd.result || ''); var i = s.indexOf(',');
        var name = f.name || ('붙여넣기_' + Date.now() + '.png');
        api('/api/drawer', { label: label, name: name, data: i >= 0 ? s.slice(i + 1) : s }).then(done).catch(fail('저장 실패'));
      };
      rd.onerror = function () { toast('파일을 못 읽었습니다', 3000); };
      rd.readAsDataURL(f);
      return;
    }
    if (!value) { toast('내용을 적거나 파일을 고르세요', 2500); return; }
    api('/api/drawer', { label: label, value: value }).then(done).catch(fail('저장 실패'));
  }

  // ------------------------------------------------------------ 폰 화면 (§39.2 → §41): 한 장을 길게 + 오른쪽 세로 바
  // 타임라인만 따로 화면이고, 나머지는 한 장에 차례로 놓인다. 주소의 #/<이름> 은 '어디를 보고 있나' 다 — 세로 바를 누르면 그 칸으로 간다.
  // 배치는 CSS(≤559px)가 #main 의 pg-home / pg-timeline 클래스로 한다.
  var PHONE_PAGES = ['today', 'timeline', 'todos', 'events', 'more', 'notes', 'night', 'drawer', 'notices', 'tools'];
  var PHONE_ANCHOR = { today: 'phone-head', todos: 'card-todos', events: 'card-events', notes: 'card-notes', night: 'card-night',
                       drawer: 'card-drawer', notices: 'card-notices', tools: 'card-unknown', more: 'phone-more' };   // 칸의 맨 위가 되는 요소
  var RAIL_ORDER = ['today', 'todos', 'events', 'notes', 'night', 'drawer', 'notices', 'tools', 'more'];             // 한 장 안에서 놓이는 차례
  var phoneMQ = window.matchMedia ? window.matchMedia('(max-width: 559px)') : null;
  function isPhone() { return !!(phoneMQ && phoneMQ.matches); }
  function phonePage() {
    var p = (location.hash || '').replace(/^#\/?/, '');
    return PHONE_PAGES.indexOf(p) >= 0 ? p : 'today';
  }
  function goPhonePage(p) {
    if (location.hash === '#/' + p) applyPhonePage(); else location.hash = '/' + p;
  }
  function syncTimelineTop() {
    var w = $('timeline-wrap');
    if (w && !w.hidden) document.documentElement.style.setProperty('--tl-top', Math.round(w.getBoundingClientRect().top + window.scrollY) + 'px');
  }
  function phoneShown(id) { var el = $(id); return el && !el.hidden && el.getClientRects().length ? el : null; }
  // 세로 바: 지금 칸 표시(흰 날이 그 자리로 미끄러진다)
  function markRail(p) {
    state.railCur = p;
    var on = null;
    Array.prototype.forEach.call(document.querySelectorAll('#phone-rail a'), function (a) {
      var hit = a.getAttribute('data-sec') === p;
      a.classList.toggle('on', hit);
      if (hit && !a.hidden) on = a;
    });
    var mk = $('rail-mark');
    if (!mk) return;
    mk.style.opacity = on ? '1' : '0';
    if (on) { mk.style.height = on.offsetHeight + 'px'; mk.style.transform = 'translateY(' + on.offsetTop + 'px)'; }
  }
  // 숨은 칸(공지가 없을 때 등)은 세로 바에서도 뺀다
  function syncRail() {
    Array.prototype.forEach.call(document.querySelectorAll('#phone-rail a'), function (a) {
      var sec = a.getAttribute('data-sec'), id = PHONE_ANCHOR[sec];
      if (id && sec !== 'today' && sec !== 'more' && $(id)) a.hidden = !!$(id).hidden;
    });
    if (state.railCur) markRail(state.railCur);
  }
  function phoneScrollTo(p, smooth) {
    var el = phoneShown(PHONE_ANCHOR[p]);
    var y = (!el || p === 'today') ? 0 : Math.max(0, Math.round(el.getBoundingClientRect().top + window.scrollY - 62));   // 52 = 위 막대, 10 = 숨 쉴 틈
    // 화면이 짧으면 칸이 맨 위까지 못 올라온다 — 그래도 누른 칸을 '지금 칸'으로 붙잡아 둔다(손으로 더 움직일 때까지)
    state.railPin = { sec: p, y: Math.min(y, Math.max(0, document.documentElement.scrollHeight - window.innerHeight)) };
    state.railLock = Date.now() + 900;               // 가는 동안에는 누른 칸이 켜져 있다
    try { window.scrollTo({ top: y, behavior: smooth ? 'smooth' : 'auto' }); } catch (e) { window.scrollTo(0, y); }
  }
  // 내려 보는 동안: 위 막대 바로 아래에 걸린 칸이 '지금 칸'
  function phoneSpy() {
    if (!isPhone() || phonePage() === 'timeline') return;
    if (state.railLock && Date.now() < state.railLock) return;
    if (state.railPin) {
      if (Math.abs(window.scrollY - state.railPin.y) < 48) { if (state.railCur !== state.railPin.sec) markRail(state.railPin.sec); return; }
      state.railPin = null;
    }
    var cur = 'today', last = 'today', curTop = -1e9;
    RAIL_ORDER.forEach(function (k) {
      var el = phoneShown(PHONE_ANCHOR[k]);
      if (!el) return;
      last = k;
      var top = el.getBoundingClientRect().top;
      if (top <= 120 && top > curTop + 1) { cur = k; curTop = top; }      // 한 줄에 나란한 두 칸이면 앞의 것
    });
    if (window.scrollY > 0 && window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) cur = last;   // 바닥에 닿으면 마지막 칸
    if (cur === state.railCur) return;
    markRail(cur);
    try { history.replaceState(null, '', '#/' + cur); } catch (e) { /* 주소를 못 바꿔도 표시는 한다 */ }
  }
  function applyPhonePage() {
    var p = phonePage(), m = $('main'), tl = p === 'timeline';
    // 주·월 보기는 타임라인 화면 안에서만 — 다른 곳으로 가면 하루로 돌아온다
    if (isPhone() && state.view !== 'day' && !tl && state.date) setView('day', state.date);
    var wasHome = m.classList.contains('pg-home');
    m.classList.add('phone-pages');
    m.classList.toggle('pg-timeline', tl);
    m.classList.toggle('pg-home', !tl);
    document.body.setAttribute('data-page', tl ? 'timeline' : 'home');
    syncRail();
    markRail(p);
    renderPhoneHead();
    if (!isPhone()) return;
    if (tl) {
      window.scrollTo(0, 0);
      requestAnimationFrame(function () {
        syncTimelineTop();
        // 숨어 있던 동안 폭이 0 이었으므로 다시 재 그리고 지금 시각으로
        if (state.view === 'day' && state.day) { state.scrolledOnce = false; renderTimeline(false); }
      });
    } else {
      if (state.tileWant === p && PHONE_ANCHOR[p]) openTile(PHONE_ANCHOR[p], true);    // 세로 바로 타임라인에서 넘어온 칸은 펼쳐서 보여 준다
      state.tileWant = null;
      phoneScrollTo(p, wasHome);
    }
  }
  function longDate(str) { var d = parseDate(str); return (d.getMonth() + 1) + '월 ' + d.getDate() + '일 ' + WEEK[d.getDay()] + '요일'; }
  function renderPhoneHead() {
    if (!$('ph-title')) return;
    var tl = phonePage() === 'timeline', title = tl ? '타임라인' : '오늘', sub = '';
    if (!tl && state.date && !isToday()) {
      var t = todayStr();
      title = state.date === shiftDate(t, -1) ? '어제' : state.date === shiftDate(t, 1) ? '내일' : (parseDate(state.date).getMonth() + 1) + '월 ' + parseDate(state.date).getDate() + '일';
    }
    if (state.date) sub = state.view === 'day' ? longDate(state.date) : ($('date-label').textContent || '');
    $('ph-title').textContent = title;
    $('ph-sub').textContent = sub;
    $('ph-sub').parentNode.hidden = !sub;          // 설명 줄이 없으면 그 높이도 없앤다
    var pick = state.view === 'day';
    $('ph-date').hidden = !pick;
    if (pick && state.date) $('ph-date').value = state.date;
    $('ph-ctl').hidden = !tl;
    Array.prototype.forEach.call(document.querySelectorAll('#ph-seg button'), function (b) { b.classList.toggle('on', b.getAttribute('data-view') === state.view); });
  }
  // 히어로: 큰 숫자 하나(활동 시간). 나머지 숫자는 '자세히' 를 눌러야 나온다(§23 요약 띠가 펼쳐진다)
  function renderPhoneHero() {
    var num = $('hero-num');
    if (!num) return;
    var sm = (state.day && state.day.summary) || {};
    var has = function (v) { return v !== null && v !== undefined; };
    clear(num);
    if (!has(sm.active_sec)) num.textContent = '—';
    else {
      var m = mins(sm.active_sec), hh = Math.floor(m / 60), mm = m % 60;
      if (hh) { num.appendChild(document.createTextNode(String(hh))); num.appendChild(h('small', { text: '시간' })); }
      num.appendChild(document.createTextNode(String(mm))); num.appendChild(h('small', { text: '분' }));
    }
    $('hero-attend').textContent = has(sm.first_active)
      ? hm(sm.first_active) + ' 출근 · ' + (sm.in_progress ? '진행 중' : (has(sm.last_active) ? hm(sm.last_active) + ' 퇴근' : '—'))
      : '기록 없음';
    var parts = [];
    if (has(sm.distraction_sec)) parts.push('딴짓 ' + Math.round((sm.distraction_ratio || 0) * 100) + '%');
    if (has(sm.focus_longest_sec)) parts.push('최장 집중 ' + mins(sm.focus_longest_sec) + '분');
    var tp = todoProgress();
    if (tp.total) parts.push('할 일 ' + tp.done + '/' + tp.total);
    $('hero-foot').textContent = parts.join(' · ');
  }
  // 하루 궤도(§42, 옛 하루 띠 §38.4): 하루를 24시간 원으로 — 맨 위가 0시, 시계 방향.
  // 바깥 고리 = 타임라인(카테고리 색), 안쪽 가는 고리 = 오늘 할 일 진행률, 작은 위성 = 지금. 로고(가운데 원 + 고리 + 위성)와 같은 그림이다.
  function todoProgress() {
    var t = state.todos || {}, today = t.today || [];
    var done = today.filter(function (x) { return x.done; }).length;
    return { done: done, total: today.length + (t.carried || []).length };
  }
  function orbitPt(r, deg) { var a = (deg - 90) * Math.PI / 180; return [120 + r * Math.cos(a), 120 + r * Math.sin(a)]; }
  function orbitArc(r, d0, d1) {
    var p0 = orbitPt(r, d0), p1 = orbitPt(r, d1);
    return 'M' + p0[0].toFixed(2) + ' ' + p0[1].toFixed(2) + 'A' + r + ' ' + r + ' 0 ' + (d1 - d0 > 180 ? 1 : 0) + ' 1 ' + p1[0].toFixed(2) + ' ' + p1[1].toFixed(2);
  }
  function renderPhoneStrip(spans, startHour, endHour, today, now) {
    var svg = $('orbit-svg');
    if (!svg) return;
    tl.strip = { spans: spans, startHour: startHour, endHour: endHour, today: today, now: now };
    clear(svg);
    var R = 96, R2 = 80;
    var deg = function (ts) { return (((hourOfTs(ts) % 24) + 24) % 24) / 24 * 360; };
    svg.appendChild(s('circle', { cx: 120, cy: 120, r: R, class: 'ob-track' }));
    svg.appendChild(s('circle', { cx: 120, cy: 120, r: R2, class: 'ob-track2' }));
    (spans || []).forEach(function (sp) {
      if (sp.category === 'absent') return;                       // 부재(오기 전)는 빈칸으로 둔다
      var a = deg(sp.start), b = deg(sp.end);
      if (b < a) b += 360;                                        // 자정을 넘는 구간
      if (b - a < 0.6) b = a + 0.6;
      if (b - a > 359.9) b = a + 359.9;
      svg.appendChild(s('path', { d: orbitArc(R, a, b), class: 'ob-arc', stroke: catOf(sp.category).color }));
    });
    var tp = todoProgress();
    if (tp.total && tp.done) {
      svg.appendChild(tp.done >= tp.total ? s('circle', { cx: 120, cy: 120, r: R2, class: 'ob-prog' })
                                          : s('path', { d: orbitArc(R2, 0, tp.done / tp.total * 360), class: 'ob-prog' }));
    }
    [0, 6, 12, 18].forEach(function (hh) {
      var p = orbitPt(113, hh * 15);
      svg.appendChild(s('text', { x: p[0].toFixed(1), y: (p[1] + 3.2).toFixed(1), class: 'ob-hr', 'text-anchor': 'middle', text: String(hh) }));
    });
    if (today && now) {
      var q = orbitPt(R, deg(now));
      svg.appendChild(s('circle', { cx: q[0].toFixed(2), cy: q[1].toFixed(2), r: 5.5, class: 'ob-now' }));
    }
  }
  // 할 일 줄: 동그라미(완료) + 제목 + 한 줄. 누르면 펼쳐져 설명과 동작이 나온다. 데이터·API 는 넓은 화면의 renderTasks 와 같다
  function phRow(key, title, meta, done, onCheck, fill) {
    var tag = meta && meta[2];                                    // §40 오른쪽 열: D-3 · 오늘 · 2일 지남 — 색은 여기에만
    var li = h('li', { class: 'ph-row' + (done ? ' done' : '') + (meta && meta[1] === 'doing' ? ' doing' : '') + (state.phOpen === key ? ' open' : '') });
    li.appendChild(h('button', { class: 'ph-ck', type: 'button', 'aria-label': done ? '되돌리기' : '끝냄', onclick: function (e) { e.stopPropagation(); onCheck(); } }));
    var b = h('div', { class: 'ph-b' }, [h('div', { class: 'ph-t', text: title })]);
    if (meta && meta[0]) b.appendChild(h('div', { class: 'ph-m ' + (tag ? '' : (meta[1] || '')), text: meta[0] }));
    var x = h('div', { class: 'ph-x' });
    fill(x);
    b.appendChild(x);
    var toggle = function (e) {
      if (e.target.closest && e.target.closest('.ph-x')) return;
      state.phOpen = state.phOpen === key ? null : key;
      renderTasks();
    };
    b.addEventListener('click', toggle);
    li.appendChild(b);
    if (tag) li.appendChild(h('span', { class: 'ph-tag ' + (meta[1] || ''), text: tag, onclick: toggle }));
    return li;
  }
  function phMetaEvt(x) {
    if (x.status === 'parsing') return [x.note || '날짜를 찾는 중…', ''];
    if (!x.occ) return [x.status === 'failed' ? '처리 실패 — 날짜를 적어 주세요' : '날짜를 적어 주세요', 'soon'];
    var w = evtWhen(x);
    if (x.done || x.d === null || x.d === undefined) return [w, ''];
    if (x.d < 0) return [w, 'late', (-x.d) + '일 지남'];
    if (x.d === 0) return [w, 'late', '오늘'];
    return [w, x.d <= 3 ? 'soon' : '', 'D-' + x.d];      // 주황은 사흘 안일 때만 — 다 주황이면 급한 게 안 보인다
  }
  function phEvt(x) {
    var weekly = x.repeat === 'weekly' && !x.done;
    return phRow('e' + x.id, x.title, phMetaEvt(x), !!x.done, function () {
      if (weekly) evtUpdate(x.id, { skip_until: shiftDate(x.occ || todayStr(), 1) });   // 매주 일정: 이번 주 것만 끝
      else evtUpdate(x.id, { done: x.done ? 0 : 1 });
    }, function (box) {
      if (x.note && x.status !== 'parsing') box.appendChild(h('div', { text: x.note }));
      var pills = [], inp = null;
      if (x.status !== 'parsing' && !x.done) {
        inp = evtDateInput(x);
        if (x.occ) { inp.hidden = true; pills.push(h('button', { class: 'ph-pill', type: 'button', text: '날짜 바꾸기', onclick: function () { inp.hidden = !inp.hidden; } })); }
        else pills.push(h('button', { class: 'ph-pill', type: 'button', text: '다시 찾기', onclick: function () {
          api('/api/events/retry', { id: x.id }).then(loadEvents).catch(fail('다시 찾기 실패')); } }));
      }
      if (x.source_url) pills.push(h('a', { class: 'ph-pill', href: x.source_url, target: '_blank', rel: 'noopener', text: '근거' }));
      pills.push(h('button', { class: 'ph-pill del', type: 'button', text: '삭제', onclick: function () {
        api('/api/events/delete', { id: x.id }).then(loadEvents).catch(fail('삭제 실패')); } }));
      box.appendChild(h('div', { class: 'ph-pills' }, pills));
      if (inp) box.appendChild(inp);
    });
  }
  function phTodo(it, carried) {
    var meta = it.done ? null : (it.doing ? ['하는 중', 'doing'] : (carried ? [mdStr(it.day) + ' 에 적음', ''] : null));
    return phRow('t' + it.id, it.text, meta, !!it.done, function () {
      var body = { id: it.id, done: it.done ? 0 : 1 };
      if (carried && !it.done) body.day = sideDate();              // §20: 전에 적은 할 일을 끝내면 '그 날 한 일' 로
      api('/api/todos/update', body).then(loadTodos).catch(fail('할 일 갱신 실패'));
    }, function (box) {
      var pills = [];
      if (!it.done) pills.push(h('button', { class: 'ph-pill' + (it.doing ? ' on' : ''), type: 'button', text: it.doing ? '하는 중 — 대기로' : '하는 중으로', onclick: function () {
        api('/api/todos/update', { id: it.id, doing: it.doing ? 0 : 1 }).then(loadTodos).catch(fail('할 일 갱신 실패')); } }));
      pills.push(h('button', { class: 'ph-pill del', type: 'button', text: '삭제', onclick: function () {
        api('/api/todos/delete', { id: it.id }).then(loadTodos).catch(fail('삭제 실패')); } }));
      box.appendChild(h('div', { class: 'ph-pills' }, pills));
    });
  }
  function renderTasksPhone() {
    var box = $('ph-todo-list');
    if (!box) return;
    clear(box);
    var t = state.todos || { today: [], carried: [] };
    var plans = ((state.events && state.events.items) || []).filter(function (x) { return x.kind !== 'event'; });
    var open = plans.filter(function (x) { return !x.done; });
    var late = open.filter(function (x) { return x.occ && x.d <= 0; }), ask = open.filter(function (x) { return !x.occ; });
    var week = open.filter(function (x) { return x.occ && x.d > 0 && x.d <= 7; }), later = open.filter(function (x) { return x.occ && x.d > 7; });
    var und = (t.today || []).filter(function (x) { return !x.done; }).map(function (x) { return [x, false]; })
      .concat((t.carried || []).map(function (x) { return [x, true]; }));
    var undRow = function (a) { return phTodo(a[0], a[1]); };
    var doneT = (t.today || []).filter(function (x) { return x.done; }), doneP = plans.filter(function (x) { return x.done; });
    var grp = function (title, rows) {
      if (!rows.length) return;
      box.appendChild(h('div', { class: 'ph-gh', text: title }));
      box.appendChild(h('ul', { class: 'ph-grp' }, rows));
    };
    grp('지남 · 오늘', late.map(phEvt));
    grp('날짜 확인', ask.map(phEvt));
    grp('이번 주', week.map(phEvt));
    grp('날짜 없음', und.map(undRow));
    grp('나중에', later.map(phEvt));
    var total = open.length + und.length, nDone = doneT.length + doneP.length;
    if (!total) box.appendChild(h('div', { class: 'ph-empty', text: '할 일이 없습니다. 위에 적어 보세요 — 날짜가 있으면 알아서 가릅니다.' }));
    if (nDone) {
      var rows = [h('li', { class: 'ph-row chev' + (state.tasksDoneOpen ? ' up' : ''), text: '끝낸 것 ' + nDone, onclick: function () { state.tasksDoneOpen = !state.tasksDoneOpen; renderTasks(); } })];
      if (state.tasksDoneOpen) rows = rows.concat(doneT.map(function (x) { return phTodo(x, false); }), doneP.map(phEvt));
      box.appendChild(h('div', { class: 'ph-gh' }));
      box.appendChild(h('ul', { class: 'ph-grp' }, rows));
    }
    if ($('ph-todos-n')) $('ph-todos-n').textContent = total + '개' + (nDone ? ' · 끝낸 것 ' + nDone : '');
    renderPhoneHero();                                              // 궤도의 안쪽 고리(할 일 진행률)와 아래 한 줄도 따라 바뀐다
    if (tl.strip) renderPhoneStrip(tl.strip.spans, tl.strip.startHour, tl.strip.endHour, tl.strip.today, tl.strip.now);
  }
  // ------------------------------------------------------------ 두 칸 타일 (§42): 폰에서 칸을 접어 한눈에 — 제목 + 큰 값 하나 + 한 줄. 누르면 그 자리에서 펼쳐진다.
  // 미리 보기는 이미 그려진 칸의 화면(DOM)에서 읽는다 — 칸마다의 데이터 모양을 여기서 다시 알 필요가 없고, 칸이 다시 그려지면 따라 바뀐다.
  var TILE_IDS = ['card-events', 'card-notes', 'card-night', 'card-drawer', 'card-notices', 'card-unknown', 'card-pause', 'card-reading', 'card-devices'];
  function tx(el) { return el ? el.textContent.replace(/\s+/g, ' ').trim() : ''; }
  var TILE_PV = {
    'card-events': function (c) {
      var row = c.querySelector('.tl-row');
      if (!row) return ['', '다가오는 일정 없음'];
      var day = row.previousElementSibling;
      while (day && !day.classList.contains('tl-day')) day = day.previousElementSibling;
      var d = tx(day), m = /D-\d+/.exec(d);
      return [m ? m[0] : d, tx(row.querySelector('.tl-title'))];
    },
    'card-notes': function () { var v = $('note-today') ? $('note-today').value.trim() : ''; return ['', v ? v.split('\n')[0] : '비어 있음']; },
    'card-night': function (c) {
      var on = c.querySelector('#night-pick .tonight-btn.on');
      return on ? [tx(on), tx($('night-sub')) || '오늘 밤'] : ['', '오늘 밤을 아직 정하지 않았습니다'];
    },
    'card-drawer': function (c) { var k = c.querySelectorAll('.drawer-k'); return [String(k.length), Array.prototype.map.call(k, tx).join(' · ') || '비어 있음']; },
    'card-notices': function (c) { var b = $('notices-badge'); return [b && !b.hidden ? tx(b) : '', tx(c.querySelector('.nt-title')) || '새 공지 없음']; },
    'card-unknown': function (c) {
      var r = c.querySelectorAll('.unknown-item');
      return [String(r.length), r.length ? Array.prototype.map.call(c.querySelectorAll('.unknown-item .mins'), tx).join(' · ') : '모두 분류됨'];
    },
    'card-pause': function () { return ['', tx($('pause-hint'))]; },
    'card-reading': function () { return ['', tx($('reading-body'))]; },
    'card-devices': function (c) { return ['', tx(c.querySelector('.dev-line .grow'))]; }
  };
  function renderTiles() {
    TILE_IDS.forEach(function (id) {
      var c = $(id), pv = c && c.querySelector('.tile-pv');
      if (!pv) return;
      var v = TILE_PV[id](c) || ['', ''];
      if (pv.firstChild.textContent !== v[0]) pv.firstChild.textContent = v[0];      // 바뀐 것만 쓴다 — 지켜보는 쪽이 다시 부르지 않게
      if (pv.lastChild.textContent !== v[1]) pv.lastChild.textContent = v[1];
    });
  }
  function openTile(id, open) {
    var c = $(id);
    if (!c || !c.classList.contains('tile')) return;
    c.classList.toggle('open', !!open);
    if (state.railCur) markRail(state.railCur);
  }
  function initTiles() {
    TILE_IDS.forEach(function (id) {
      var c = $(id);
      if (!c) return;
      c.classList.add('tile');
      var sec = c.querySelector('.sec');
      var pv = h('div', { class: 'tile-pv' }, [h('div', { class: 'pv-big' }), h('div', { class: 'pv-cap' })]);
      if (sec && sec.nextSibling) c.insertBefore(pv, sec.nextSibling); else c.appendChild(pv);
      c.addEventListener('click', function (e) {
        if (!isPhone()) return;
        if (!c.classList.contains('open')) { openTile(id, true); return; }
        // 펼친 뒤에는 제목 줄(그 안의 단추·입력은 빼고)을 눌러야 접힌다
        var inSec = e.target.closest ? e.target.closest('.sec') : null;
        if (inSec && inSec.parentNode === c && !e.target.closest('button, a, input, textarea, select')) openTile(id, false);
      });
    });
    var wait = null;
    var again = function () { if (wait) return; wait = setTimeout(function () { wait = null; renderTiles(); }, 150); };
    if (window.MutationObserver) new MutationObserver(again).observe($('sidebar'), { childList: true, subtree: true, characterData: true });
    $('sidebar').addEventListener('input', again);
    renderTiles();
  }
  function initPhone() {
    window.addEventListener('hashchange', applyPhonePage);
    $('phone-rail').addEventListener('click', function (e) {
      var a = e.target.closest ? e.target.closest('a[data-sec]') : null;
      if (!a) return;
      e.preventDefault();
      var sec = a.getAttribute('data-sec');
      // 타임라인으로 가거나 타임라인에서 나올 때만 화면이 바뀐다(뒤로 가기에 남는다). 한 장 안에서는 그 칸으로 내려갈 뿐
      if (sec === 'timeline' || phonePage() === 'timeline') { state.tileWant = sec; goPhonePage(sec); return; }
      try { history.replaceState(null, '', '#/' + sec); } catch (err) { /* 주소를 못 바꿔도 간다 */ }
      if (PHONE_ANCHOR[sec]) openTile(PHONE_ANCHOR[sec], true);     // 접힌 칸이면 펼치고 간다
      markRail(sec);
      phoneScrollTo(sec, true);
    });
    window.addEventListener('scroll', function () {
      if (state.spyWait) return;
      state.spyWait = true;
      setTimeout(function () { state.spyWait = false; phoneSpy(); }, 60);      // 마지막 위치는 늘 한 번 더 본다
    }, { passive: true });
    $('ph-prev').addEventListener('click', function () { navigatePeriod(-1); });
    $('ph-next').addEventListener('click', function () { navigatePeriod(1); });
    $('ph-date').addEventListener('change', function () { if (this.value && state.view === 'day') changeDate(this.value); });
    $('ph-seg').addEventListener('click', function (e) {
      var b = e.target.closest ? e.target.closest('button[data-view]') : null;
      if (b) setView(b.getAttribute('data-view'), state.date || todayStr());
    });
    $('hero-more').addEventListener('click', function () {
      var open = $('main').classList.toggle('hero-open');
      this.textContent = open ? '접기' : '자세히 ›';
    });
    var tap = $('ps-tap');
    tap.addEventListener('click', function () { goPhonePage('timeline'); });
    tap.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); goPhonePage('timeline'); } });
    $('phone-more').addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('[data-act]') : null;
      if (!t) return;
      var act = t.getAttribute('data-act');
      if (act === 'vocab') $('btn-vocab').click();
      else if (act === 'theme') toggleTheme();
      else if (act === 'logout') $('link-logout').click();
    });
    $('ph-theme').firstChild.nodeValue = THEME === 'dark' ? '밝은 화면으로' : '어두운 화면으로';
    window.addEventListener('resize', function () { syncTimelineTop(); if (state.railCur) markRail(state.railCur); });
    if (phoneMQ && phoneMQ.addEventListener) phoneMQ.addEventListener('change', function () {
      applyPhonePage();
      if (state.view === 'day' && state.day) renderTimeline(false);
    });
    // 칸이 생기거나 사라지면(공지 · 오늘 밤 · 다른 기기) 세로 바도 맞춘다
    if (window.MutationObserver) new MutationObserver(function () { syncRail(); }).observe($('sidebar'), { attributes: true, attributeFilter: ['hidden'], subtree: true });
    initTiles();
    applyPhonePage();
    setTimeout(function () { var sp = $('splash'); if (sp && sp.parentNode) sp.parentNode.removeChild(sp); }, 1800);   // 시작 화면: 움직임이 멈춘 창(가려진 창)에서도 걷어 낸다
    // 처음 열 때는 내용이 아직 덜 그려져 자리가 어긋난다 — 그려진 뒤 한 번 더 맞춘다
    setTimeout(function () {
      syncRail();
      if (isPhone() && phonePage() !== 'timeline' && phonePage() !== 'today') phoneScrollTo(phonePage(), false);
    }, 700);
  }

  // ------------------------------------------------------------ 로고의 위성 = 분침 (§37.9)
  // 헤더의 마크 그림을 같은 모양의 SVG(위성이 12시)로 바꿔 끼우고, 위성과 고리의 틈을 지금 '분' 만큼 돌린다 — 한 시간에 한 바퀴.
  function initOrbitMark() {
    var img = document.querySelector('#header img.brand-mark');
    if (!img || !window.fetch || !window.DOMParser) return;
    fetch('/static/logo-mark-clock.svg').then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.text();
    }).then(function (txt) {
      var src = new DOMParser().parseFromString(txt, 'image/svg+xml').documentElement;
      if (!src || src.nodeName.toLowerCase() !== 'svg' || !src.querySelector('.om-moon')) return;
      var svg = document.importNode(src, true);
      svg.setAttribute('class', 'brand-mark');
      svg.setAttribute('aria-hidden', 'true');
      img.parentNode.replaceChild(svg, img);
      startMinuteHand(svg);
    }).catch(function () { /* 못 받으면 멈춘 그림 그대로 */ });
  }
  function startMinuteHand(svg) {
    var vb = (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
    var c = (vb[2] || 0) / 2;
    var parts = svg.querySelectorAll('.om-gap, .om-moon');
    function tick() {
      var d = new Date();
      var a = (d.getMinutes() + d.getSeconds() / 60) * 6;          // 분침: 1분에 6°, 시계 방향(SVG 는 y 가 아래라 양수가 시계 방향)
      for (var i = 0; i < parts.length; i++) parts[i].setAttribute('transform', 'rotate(' + a.toFixed(2) + ' ' + c + ' ' + c + ')');
      svg.setAttribute('data-minute', String(d.getMinutes()));
    }
    tick();
    setInterval(tick, 10000);                                        // 10초에 1° — 눈에는 이어져 보인다
    document.addEventListener('visibilitychange', function () { if (!document.hidden) tick(); });
  }

  // ------------------------------------------------------------ 다른 기기 (§36): 이 PC 의 설정 카드
  function loadDevices(check) {
    return api('/api/devices' + (check ? '?check=1' : '')).then(function (v) {
      state.devices = v;
      state.remote = !!(v && v.remote);
      document.body.classList.toggle('is-remote', state.remote);
      $('link-logout').hidden = !state.remote;
      if ($('pm-logout')) $('pm-logout').hidden = !state.remote;
      renderDevices();
    }).catch(function () { /* 설정 카드는 못 받아도 판은 쓴다 */ });
  }
  function devicesPost(body, okMsg) {
    return api('/api/devices', body).then(function (v) {
      state.devices = v; renderDevices();
      var r = (v && v.result) || {};
      if (r.error) toast(r.error, 5000); else if (okMsg) toast(okMsg, 2200);
      return r;
    }).catch(fail('설정 실패'));
  }
  function fmtWhen(t) {
    if (!t) return '';
    var d = new Date(t * 1000);
    return (d.getMonth() + 1) + '/' + d.getDate() + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
  }
  function renderDevices() {
    var v = state.devices, card = $('card-devices');
    if (!v || v.remote) { card.hidden = true; return; }
    card.hidden = false;
    var body = $('devices-body');
    clear(body);
    // 줄 1: 상태 + 켜기/끄기
    body.appendChild(h('div', { class: 'dev-line' }, [
      h('span', { class: 'st-dot' + (v.on ? ' on' : '') }),
      h('span', { class: 'grow', text: v.on ? '켜짐 — 비밀번호로 로그인한 기기에서 열립니다' : '꺼짐 — 이 PC 에서만 열립니다' }),
      h('button', { class: 'act', type: 'button', text: v.on ? '끄기' : '켜기', onclick: function () {
        devicesPost({ op: 'on', on: !v.on }, v.on ? '다른 기기에서 여는 것을 껐습니다' : '켰습니다 — 아래 주소를 휴대폰에서 여세요');
      } })
    ]));
    // 줄 2: 비밀번호
    var pw = h('input', { type: 'password', autocomplete: 'new-password', placeholder: v.has_password ? '새 비밀번호 (8자 이상)' : '비밀번호 (8자 이상)', maxlength: '200' });
    var setPw = function () {
      var val = pw.value;
      if (val.length < 8) { toast('비밀번호는 8자 이상이어야 합니다', 2500); pw.focus(); return; }
      pw.value = '';
      devicesPost({ op: 'password', password: val }, v.has_password ? '바꿨습니다 — 모든 기기가 다시 로그인합니다' : '정했습니다');
    };
    pw.addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); setPw(); } });
    body.appendChild(h('div', { class: 'dev-line' }, [pw, h('button', { class: 'ghostbtn', type: 'button', text: '정하기', onclick: setPw })]));
    if (v.has_password) body.appendChild(h('div', { class: 'hint', text: '정해 둠 — 바꾸면 모든 기기가 다시 로그인합니다' }));
    // 줄 3: 주소
    var ts = v.tailscale || {};
    var line3;
    if (!ts.found) line3 = h('span', { class: 'grow hint', text: '이 PC 에 Tailscale 이 없습니다 — 설치하고 로그인하면 주소가 생깁니다' });
    else if (!ts.running) line3 = h('span', { class: 'grow hint', text: 'Tailscale 이 로그인되어 있지 않습니다 (' + (ts.state || '?') + ')' });
    else if (ts.serving && ts.urls && ts.urls.length) {
      line3 = h('span', { class: 'grow wrap' }, ts.urls.slice(0, 1).map(function (u) {
        return h('span', { class: 'dev-url', title: '누르면 복사 — 휴대폰·노트북 브라우저에서 엽니다', text: u, onclick: function () {
          copyText(u).then(function () { toast('주소를 복사했어요', 1800); }).catch(fail('복사 실패'));
        } });
      }));
    } else line3 = h('span', { class: 'grow hint', text: '테일넷으로 아직 넘겨 주지 않습니다' });
    var l3kids = [h('span', { class: 'hint', text: '주소' }), line3];
    if (ts.found && ts.running && !ts.serving) l3kids.push(h('button', { class: 'ghostbtn', type: 'button', text: '연결 설정', onclick: function (e) {
      e.currentTarget.disabled = true; e.currentTarget.textContent = '설정 중…';
      devicesPost({ op: 'serve', on: true }, '테일넷으로 넘겨 주기를 켰습니다');
    } }));
    else l3kids.push(h('button', { class: 'act', type: 'button', text: '다시 보기', title: 'Tailscale 상태를 다시 읽습니다', onclick: function () { loadDevices(true); } }));
    body.appendChild(h('div', { class: 'dev-line' }, l3kids));
    // 줄 4: 로그인한 기기
    var ss = v.sessions || [];
    if (ss.length) {
      body.appendChild(h('div', { class: 'dev-line' }, [
        h('span', { class: 'grow' }, ss.slice(0, 6).map(function (s) {
          return h('span', { class: 'dev-sess', text: s.dev + '  ' + fmtWhen(s.seen || s.t) });
        })),
        h('button', { class: 'act', type: 'button', text: '모두 로그아웃', onclick: function () { devicesPost({ op: 'logout_all' }, '모든 기기를 로그아웃했습니다'); } })
      ]));
    }
  }

  // ------------------------------------------------------------ 학사 일정 (§24)
  var SCHED_ROWS = 6;
  function loadSchedule() {
    if (!state.date) return Promise.resolve();
    var d = sideDate();
    return api('/api/schedule?date=' + encodeURIComponent(d)).then(function (r) {
      if (d !== sideDate()) return;
      state.schedule = r;
      renderSchedule();
    }).catch(fail('학사 일정 불러오기 실패'));
  }
  function schedDay(it) {
    if (it.ongoing) return it.d_end === 0 ? '오늘까지' : '진행 중';
    if (it.d === 0) return 'D-day';
    return 'D-' + it.d;
  }
  function schedRange(it) {
    var a = mdStr(it.start), b = mdStr(it.end);
    return a === b ? a : a + ' ~ ' + b;
  }
  // §29 일정 타임라인 = 내 일정(수업·회의·실습) + 학사 일정, 날짜순. 14일 안은 다 보이고 나머지는 'n건 더'
  function renderSchedule() { renderAgenda(); }
  var TL_DAYS = 14;
  function renderAgenda() {
    var ul = $('event-list'), foot = $('events-foot');
    if (!ul) return;
    clear(ul); clear(foot);
    var today = sideDate();
    var items = [];
    ((state.events && state.events.items) || []).forEach(function (x) {
      if (x.kind !== 'event' || x.done || !x.occ) return;
      items.push({ date: x.occ, d: x.d, time: x.time || '', title: x.title, tag: x.repeat === 'weekly' ? '매주' : '', src: 'mine', it: x });
    });
    var sc = state.schedule || {};
    (sc.items || []).forEach(function (s) {
      if (s.ongoing) items.push({ date: today, d: 0, time: '', title: s.title, tag: '학사', src: 'school', holiday: s.holiday, until: s.end, d_end: s.d_end });
      else items.push({ date: s.start, d: s.d, time: '', title: s.title, tag: '학사', src: 'school', holiday: s.holiday, until: s.end !== s.start ? s.end : '' });
    });
    items.sort(function (a, b) { return a.date < b.date ? -1 : a.date > b.date ? 1 : (a.src === b.src ? (a.time < b.time ? -1 : 1) : (a.src === 'mine' ? -1 : 1)); });
    var near = items.filter(function (x) { return x.d <= TL_DAYS; }), far = items.filter(function (x) { return x.d > TL_DAYS; });
    var show = state.scheduleAll ? items : near;
    setZoneCount('zone-coming-n', near.length + '건 · 14일');
    if (!show.length) ul.appendChild(h('li', { class: 'empty', text: '앞으로 ' + TL_DAYS + '일 안에 일정이 없습니다' }));
    var lastDate = null;
    show.forEach(function (x) {
      if (x.date !== lastDate) {
        lastDate = x.date;
        var dt = parseDate(x.date);
        var label = x.d === 0 ? '오늘' : x.d === 1 ? '내일' : (WEEK[dt.getDay()] + ' ' + mdStr(x.date));
        if (x.d > 1 && x.d <= 7) label += ' · D-' + x.d;
        ul.appendChild(h('li', { class: 'tl-day' + (x.d === 0 ? ' today' : ''), text: label }));
      }
      var kids = [h('span', { class: 'tl-time', text: x.time || (x.until ? '~ ' + mdStr(x.until) : '·') }),
                  h('span', { class: 'tl-title', text: x.title })];
      if (x.tag) kids.push(h('span', { class: 'tl-tag', text: x.tag }));
      if (x.src === 'mine') {
        if (x.it.repeat === 'weekly') kids.push(h('button', { class: 'act', type: 'button', text: '이번 주 끝', title: '이번 주 것은 끝났다고 표시하고 다음 주로',
          onclick: function () { evtUpdate(x.it.id, { skip_until: shiftDate(x.it.occ, 1) }); } }));
        else kids.push(h('button', { class: 'act', type: 'button', text: '끝', title: '지난 일정으로 표시', onclick: function () { evtUpdate(x.it.id, { done: 1 }); } }));
        kids.push(h('button', { class: 'act', type: 'button', text: '할 일로', title: '일정이 아니라 할 일이면', onclick: function () { evtUpdate(x.it.id, { kind: 'task' }); } }));
        kids.push(h('button', { class: 'act btn-del', type: 'button', text: '×', title: '지우기', onclick: function () { api('/api/events/delete', { id: x.it.id }).then(loadEvents).catch(fail('삭제 실패')); } }));
      }
      var li = h('li', { class: 'list-row tl-row ' + x.src + (x.holiday ? ' holiday' : '') }, kids);
      if (x.src === 'school') li.title = x.title + (x.until ? ' (' + x.date + ' ~ ' + x.until + ')' : ' (' + x.date + ')') + ' · 대학원 학사일정';
      ul.appendChild(li);
    });
    if (far.length) foot.appendChild(h('button', { class: 'act nt-more', type: 'button', text: state.scheduleAll ? '접기' : far.length + '건 더 (' + TL_DAYS + '일 뒤)',
      onclick: function () { state.scheduleAll = !state.scheduleAll; renderAgenda(); } }));
    if (sc.error) { var e = h('span', { class: 'hint nt-foot-note', text: '학사 일정 수집 실패' }); e.title = String(sc.error); foot.appendChild(e); }
    else if (sc.fetched) { var f = new Date(sc.fetched * 1000); foot.appendChild(h('span', { class: 'hint nt-foot-note', text: '학사 ' + (f.getMonth() + 1) + '/' + f.getDate() + ' ' + pad2(f.getHours()) + ':' + pad2(f.getMinutes()) + ' 기준' })); }
  }

  // ------------------------------------------------------------ Anki 복습 모니터 (§31)
  var ANKI_REFRESH_MS = 10 * 60000;
  function ankiOn() { return !!(state.config && state.config.anki_card); }
  function loadAnki(force) {
    $('card-anki').hidden = !ankiOn();          // §31.6: 설정으로 켜고 끈다. 꺼져 있으면 조회도, 동기화 트리거도 안 한다
    if (!ankiOn()) return Promise.resolve();
    return api('/api/anki' + (force ? '?force=1' : '')).then(function (r) {
      state.anki = r; renderAnki();
      // AnkiConnect 로 sync 를 눌러 둔 상태면 끝날 때까지 20초마다 다시 본다 (끝나면 서버 캐시가 비워져 새 숫자가 온다)
      if (r && r.connect && r.connect.busy) setTimeout(function () { loadAnki(false); }, 20000);
    }).catch(fail('Anki 읽기 실패'));
  }
  function renderAnki() {
    var r = state.anki, box = $('anki-body'), sub = $('anki-sub');
    if (!r || !box) return;
    clear(box);
    var cn = r.connect || {};
    function when(dt) { return (dt.getMonth() + 1) + '/' + dt.getDate() + ' ' + pad2(dt.getHours()) + ':' + pad2(dt.getMinutes()); }
    if (!r.available || !r.data) {
      sub.textContent = '';
      box.appendChild(h('div', { class: 'anki-none' }, [
        document.createTextNode('이 PC 에 Anki 데스크톱이 없습니다. 설치하고 AnkiWeb 에 로그인해 두면 폰에서 한 복습이 여기 보입니다.' + (r.error ? ' (' + r.error + ')' : ''))
      ]));
      return;
    }
    var d = r.data, t = d.today, due = d.due;
    // col.ls = 마지막으로 새 기록을 주고받은 동기화. 바뀐 게 없던 동기화는 이 값을 안 바꾼다
    var ls = d.last_sync ? new Date(d.last_sync * 1000) : null;
    sub.textContent = ls ? ('새 기록 받음 ' + when(ls)) : (r.mtime ? '파일 ' + when(new Date(r.mtime * 1000)) : '');
    box.appendChild(h('div', { class: 'anki-main' }, [
      h('span', { class: 'big' }, [document.createTextNode(String(t.reviews)), h('small', { text: '장 오늘' })]),
      h('span', { class: 'big' }, [document.createTextNode(String(Math.round(t.minutes))), h('small', { text: '분' })]),
      h('span', { class: 'big' }, [document.createTextNode(t.accuracy === null || t.accuracy === undefined ? '—' : t.accuracy + '%'), h('small', { text: '정답' })])
    ]));
    var row = h('div', { class: 'anki-row' }, [
      h('span', {}, [document.createTextNode('남은 것 '), h('b', { class: due.review + due.learn > 0 ? 'warn' : '', text: String(due.review + due.learn) }),
        document.createTextNode(due.learn ? ' (학습 중 ' + due.learn + ')' : '')]),
      h('span', {}, [document.createTextNode('연속 '), h('b', { text: d.streak + '일' })]),
      h('span', {}, [document.createTextNode('30일 정답률 '), h('b', { text: d.retention30 === null || d.retention30 === undefined ? '—' : d.retention30 + '%' })])
    ]);
    box.appendChild(row);
    var days = h('div', { class: 'anki-days', title: '최근 7일 복습 장수' });
    (d.days || []).forEach(function (x, i, arr) {
      var dt = parseDate(x.day);
      days.appendChild(h('span', { class: (x.n > 0 ? 'on' : '') + (i === arr.length - 1 ? ' today' : '') }, [document.createTextNode(String(x.n)), h('i', { text: WEEK[dt.getDay()] })]));
    });
    box.appendChild(days);
    if (d.decks && d.decks.length) {
      box.appendChild(h('div', { class: 'anki-row' }, d.decks.map(function (k) { return h('span', {}, [document.createTextNode(k.name + ' '), h('b', { text: String(k.n) })]); })));
    }
    if (d.last_review) {
      var l = new Date(d.last_review * 1000), ago = Math.round((Date.now() - l.getTime()) / 3600000);
      box.appendChild(h('div', { class: 'hint', text: '마지막 복습 ' + when(l) + (ago >= 24 ? ' · ' + Math.floor(ago / 24) + '일 전' : '') }));
    }
    // 동기화 상태 — Anki 데스크톱은 켜 둔 채로는 스스로 동기화하지 않는다(열고 닫을 때만). AnkiConnect 가 있으면 호라리움이 눌러 준다
    var staleH = ls ? (Date.now() - ls.getTime()) / 3600000 : null;
    if (cn.busy) box.appendChild(h('div', { class: 'hint', text: 'AnkiWeb 과 동기화 중…' }));
    else if (cn.present) box.appendChild(h('div', { class: 'hint', text: '자동 동기화 켜짐 (AnkiConnect, 30분마다)' + (cn.error ? ' · 오류: ' + cn.error : '') }));
    else if (staleH === null || staleH >= 3) box.appendChild(h('div', { class: 'hint anki-stale', text: '폰 기록이 안 보이면: 폰 Anki 에서 동기화 → 이 PC 의 Anki 창에서 Y (켜 둔 Anki 는 스스로 동기화하지 않음)' }));
  }

  // ------------------------------------------------------------ 내 일정·마감 (§28)
  function loadEvents() {
    if (!state.date) return Promise.resolve();
    var d = sideDate();
    return api('/api/events?date=' + encodeURIComponent(d)).then(function (r) {
      if (d !== sideDate()) return;
      state.events = r;
      renderEvents();
      // 날짜를 가르는 중인 행이 있으면 4초마다 다시 본다 (끝나면 멈춤)
      if (state.eventsPoll) { clearTimeout(state.eventsPoll); state.eventsPoll = null; }
      if ((r.items || []).some(function (x) { return x.status === 'parsing'; })) state.eventsPoll = setTimeout(loadEvents, 4000);
    }).catch(fail('일정 불러오기 실패'));
  }
  function evtDay(it) {
    if (it.d === null || it.d === undefined) return '';
    if (it.d < 0) return 'D+' + (-it.d);
    if (it.d === 0) return 'D-day';
    return 'D-' + it.d;
  }
  function evtDayCls(it) {
    if (it.d === null || it.d === undefined) return 'evt-d wait';
    if (it.d <= 0) return 'evt-d now';
    if (it.d <= 7) return 'evt-d soon';
    return 'evt-d';
  }
  function evtWhen(it) {
    if (!it.occ) return '';
    var dt = parseDate(it.occ);
    return mdStr(it.occ) + '(' + WEEK[dt.getDay()] + ')' + (it.time ? ' ' + it.time : '') + (it.repeat === 'weekly' ? ' · 매주' : '');
  }
  function evtUpdate(id, body) {
    body.id = id;
    api('/api/events/update', body).then(loadEvents).catch(fail('일정 갱신 실패'));
  }
  function evtDateInput(it) {
    var inp = h('input', { type: 'date', title: '날짜를 적으면 그걸로 확정됩니다' });
    if (it.day) inp.value = it.day;
    inp.addEventListener('change', function () { if (inp.value) evtUpdate(it.id, { day: inp.value }); });
    return inp;
  }
  function evtRow(it, compact) {
    var kids = [];
    var l1 = [h('span', { class: evtDayCls(it), text: it.status === 'parsing' ? '…' : (evtDay(it) || (it.status === 'failed' ? '실패' : '날짜?')) }),
              h('span', { class: 'evt-t', text: it.title })];
    if (it.status !== 'parsing') {
      if (it.repeat === 'weekly' && !it.done) {
        l1.push(h('button', { class: 'act', type: 'button', text: '이번 주 끝', title: '이번 주 것은 끝났다고 표시하고 다음 주로 넘깁니다',
          onclick: function () { evtUpdate(it.id, { skip_until: shiftDate(it.occ || todayStr(), 1) }); } }));
      } else {
        l1.push(h('button', { class: 'act', type: 'button', text: it.done ? '되돌리기' : '완료', title: it.done ? '다시 안 끝난 것으로' : '끝났다고 표시',
          onclick: function () { evtUpdate(it.id, { done: it.done ? 0 : 1 }); } }));
      }
    }
    l1.push(h('button', { class: 'act btn-del', type: 'button', text: '×', title: '지우기',
      onclick: function () { api('/api/events/delete', { id: it.id }).then(loadEvents).catch(fail('삭제 실패')); } }));
    kids.push(h('div', { class: 'evt-l1' }, l1));
    if (!compact) {
      var meta = [];
      if (it.status === 'parsing') meta.push(h('span', { text: it.note || '제목·날짜를 가르는 중…' }));
      else {
        meta.push(h('span', { text: it.kind_name + (it.occ ? ' · ' + evtWhen(it) : '') }));
        if (!it.day) {
          meta.push(h('span', { class: 'need', text: it.status === 'failed' ? '처리 실패 — 날짜를 적어 주세요' : '날짜를 못 찾았어요 — 적어 주세요' }));
          meta.push(evtDateInput(it));
          meta.push(h('button', { class: 'act', type: 'button', text: '다시 찾기', onclick: function () {
            api('/api/events/retry', { id: it.id }).then(loadEvents).catch(fail('다시 찾기 실패')); } }));
        } else if (it.confidence === 'guess') {
          meta.push(h('span', { class: 'need', text: '확인 필요(추정)' }));
          meta.push(evtDateInput(it));
        } else if (!it.done) {
          var chg = h('button', { class: 'act', type: 'button', text: '날짜', title: '날짜 바꾸기' });
          var inp = evtDateInput(it); inp.hidden = true;
          chg.onclick = function () { inp.hidden = !inp.hidden; };
          meta.push(chg); meta.push(inp);
        }
        if (it.source_url) { var a = h('a', { href: it.source_url, target: '_blank', rel: 'noopener', text: '근거' }); if (it.note) a.title = it.note; meta.push(a); }
        else if (it.note && it.status !== 'parsing') meta.push(h('span', { text: it.note, title: it.note, style: 'max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap' }));
      }
      kids.push(h('div', { class: 'evt-meta' }, meta));
    }
    return h('li', { class: 'list-row evt-row' + (it.done ? ' done' : '') }, kids);
  }
  function renderEvents() {
    var r = state.events;
    if (!r) return;
    var items = r.items || [];
    renderTasks();
    renderAgenda();
    // 곧: 이틀 안이거나 지난 것(할 일·마감·일정 모두). 끝낼 때까지 맨 위에 남는다
    var due = items.filter(function (x) { return !x.done && x.d !== null && x.d !== undefined && x.d <= 2; });
    var dl = $('due-list'); clear(dl);
    due.forEach(function (it) { dl.appendChild(evtRow(it, true)); });
    $('card-due').hidden = !due.length || !isToday();
    var base = document.title.replace(/^\(\d+\) /, '');
    document.title = (due.length ? '(' + due.length + ') ' : '') + base;
  }
  function loadNotes(includePinned) {
    if (!state.date) return Promise.resolve();
    var d = sideDate();
    renderNoteLabel();
    var p = [api('/api/note?name=' + encodeURIComponent(d)).then(function (n) {
      if (d !== sideDate()) return;
      setNoteText('today', n.text || '', n.name, n.ver);
    }).catch(function (e) {
      if (d !== sideDate()) return;
      // 불러오기 실패: 입력을 막아 이전 날짜 파일에 저장되는 일을 방지
      lockNote('today', '불러오기 실패 — 저장 안 됨');
      throw e;
    })];
    if (includePinned) p.push(api('/api/note?name=pinned').then(function (n) {
      setNoteText('pinned', n.text || '', 'pinned', n.ver);
    }));
    return Promise.all(p).catch(fail('메모 불러오기 실패'));
  }

  function changeDate(newDate) {
    if (newDate === state.date) return;
    closePopover();
    // 1초 디바운스 대기 중인 메모가 있으면 날짜를 바꾸기 전에 즉시 저장 (안 그러면 사라짐)
    flushNote('today');
    state.date = newDate;
    state.scrolledOnce = false;
    lockNote('today', '불러오는 중…');
    loadDay(newDate);
    loadTodos();
    loadNight();
    loadBudget();
    loadSchedule();
    loadEvents();
    loadNotes(false);
  }

  // ------------------------------------------------------------ 헤더
  function renderDateHeader() {
    if (!state.date) return;
    var today = isToday();
    state.renderedAsToday = today;
    $('date-label').textContent = koreanDate(state.date) + (today ? ' · 오늘' : '');
    $('date-input').value = state.date;
    renderPhoneHead();
  }

  function renderStatus() {
    var st = state.status;
    var el = $('status-banner');
    var now = Date.now() / 1000;
    el.className = 'status-banner';
    var paused = st && st.paused_until && st.paused_until > (st.now || now);
    var stText;
    if (!st) {
      el.classList.add('st-none'); stText = '상태 확인 중…';
    } else if (!st.alive) {
      el.classList.add('st-dead');
      stText = '끊김 · 바탕화면 아이콘으로 다시 실행';
    } else if (paused) {
      el.classList.add('st-paused');
      stText = '일시정지 · ' + hm(st.paused_until) + '까지';
    } else {
      el.classList.add('st-alive');
      var n = (st.seconds_since === null || st.seconds_since === undefined) ? '?' : st.seconds_since;
      stText = '기록 중 · ' + n + '초 전';
    }
    clear(el);
    el.appendChild(h('span', { class: 'st-dot' }));
    el.appendChild(h('span', { class: 'st-text', text: stText }));
    if (st && st.current && st.current.exe) {
      // §15: 표시 이름(name) 우선, 없으면 exe. 원문 제목·exe 는 뒤에 그대로.
      var curName = st.current.name || baseName(st.current.exe);
      el.title = '현재: ' + curName + ' · ' + (st.current.title || '') +
        (curName !== baseName(st.current.exe) ? ' · ' + baseName(st.current.exe) : '') +
        ' · ' + catOf(st.current.category).name;
      el.title = stText + ' — ' + el.title;   // §36.5 폰에서는 글자를 숨기고 점만 — 길게 누르면 보인다
    } else el.title = stText;
    $('btn-resume').disabled = !paused;
    $('btn-pause-1h').disabled = !!paused;
    $('btn-pause-day').disabled = !!paused;
    $('pause-hint').textContent = paused ? hm(st.paused_until) + '까지 기록하지 않습니다.' : '일시정지 중에는 전경 창을 기록하지 않습니다.';
  }

  // ------------------------------------------------------------ 타임라인
  var tl = { startHour: 0, endHour: 24, windowStart: 0, width: 0 };

  function hourOfTs(ts) {
    // window.start 기준 상대 시각을 '시' 로 (day_start_hour + 경과시간)
    return (state.config.day_start_hour || 4) + (ts - tl.windowStart) / 3600;
  }
  function yOf(ts) {
    var h0 = state.config.day_start_hour || 4;
    return PAD_TOP + ((ts - tl.windowStart) / 60 - (tl.startHour - h0) * 60) * state.pxPerMin;
  }

  function renderTimeline(scrollToStart) {
    var day = state.day;
    var svg = $('timeline');
    var wrap = $('timeline-scroll'); // 스크롤 컨테이너 (#timeline-wrap 은 툴바 행 + 스크롤 영역의 세로 flex)
    var emptyEl = $('timeline-empty');
    clear(svg);
    if (!day || !day.window) return;
    if (isPhone() && phonePage() === 'timeline') syncTimelineTop();   // §39.2 폰 타임라인 화면의 높이 기준 — 폭·맞춤 배율을 재기 전에

    var h0 = state.config.day_start_hour || 4;
    tl.windowStart = day.window.start;
    var spans = (day.spans || []).filter(function (sp) { return sp.category !== 'off' && sp.end > sp.start; });

    var startHour = state.config.display_start_hour;
    var endHour = state.config.display_end_hour;
    if (startHour === undefined || startHour === null) startHour = 6;
    if (endHour === undefined || endHour === null) endHour = 24;
    if (spans.length) {
      startHour = Math.min(startHour, Math.floor(hourOfTs(spans[0].start) + 1e-9));
      endHour = Math.max(endHour, Math.ceil(hourOfTs(spans[spans.length - 1].end) - 1e-9));
    }
    var today = isToday();
    var now = nowTs();
    if (today && now >= day.window.start && now <= day.window.end) {
      endHour = Math.max(endHour, Math.ceil(hourOfTs(now) - 1e-9));
    }
    if (day.night) endHour = Math.max(endHour, h0 + 20);   // §19 밤 밴드가 자정까지 보이게
    startHour = Math.max(h0, startHour);
    endHour = Math.min(h0 + 24, Math.max(endHour, startHour + 1));
    tl.startHour = startHour; tl.endHour = endHour;

    var width = wrap.clientWidth || 600;
    tl.width = width;
    if (state.zoomFit) state.pxPerMin = fitPxFor(wrap, (endHour - startHour) * 60); // 맞춤: 한 화면에 들어오게
    var px = state.pxPerMin;
    var height = PAD_TOP + (endHour - startHour) * 60 * px + PAD_BOTTOM;
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);
    svg.style.height = height + 'px';

    var x0 = GUTTER + 4;
    var rectW = Math.max(40, width - x0 - RECT_PAD_RIGHT);

    // 배경 격자 + 눈금 (밀도는 배율에 따라 1/2/3시간, 격자선은 라벨 있는 시각만 + 범위 양끝)
    svg.appendChild(drawHourGrid(startHour, endHour, px, width));

    // spans
    var gSpans = s('g', { class: 'spans' });
    (day.spans || []).forEach(function (sp, idx) {
      if (sp.category === 'off' || !(sp.end > sp.start)) return;
      var yA = yOf(sp.start), yB = yOf(sp.end);
      var hgt = Math.max(1, yB - yA);
      var cat = catOf(sp.category);
      var attrs = {
        class: 'span cat-' + sp.category + (sp.source === 'override' ? ' override' : ''),
        'data-idx': idx, x: x0, y: yA, width: rectW, height: hgt, fill: cat.color, rx: 2
      };
      if (sp.category === 'paused') {
        attrs.stroke = '#9CA3AF'; attrs['stroke-width'] = 1.5; attrs['stroke-dasharray'] = '4 3';
        attrs.x = x0 + 1; attrs.width = rectW - 2; // 테두리 잘림 방지
      } else if (sp.source === 'override') {
        attrs.stroke = '#1f4fd1'; attrs['stroke-width'] = 1; attrs['stroke-opacity'] = 0.6;
      }
      var rect = s('rect', attrs);
      rect.appendChild(s('title', { text: spanTooltip(sp) }));
      gSpans.appendChild(rect);
      if (sp.category === 'distraction' || sp.category === 'away') {
        gSpans.appendChild(s('rect', {
          class: 'span-overlay', x: x0, y: yA, width: rectW, height: hgt, rx: 2,
          fill: sp.category === 'distraction' ? 'url(#hatch-white)' : 'url(#hatch-gray)'
        }));
      }
      if (hgt >= 14) {
        var lbl = spanLabel(sp);
        var label = cat.name + (lbl ? ' · ' + lbl : '');
        if (!sp.label && sp.title) label += ' · ' + sp.title; // 표시 이름이 없을 때(구 서버)만 원문 제목 병기
        label = truncateToWidth(label, rectW - 12, 11);
        gSpans.appendChild(s('text', {
          class: 'span-text', x: x0 + 6, y: yA + Math.min(hgt / 2 + 4, 12), fill: textColorFor(cat.color), text: label
        }));
      }
    });
    svg.appendChild(gSpans);
    if (day.night) svg.appendChild(nightBand(day.night, x0, rectW));

    // 현재 시각선
    if (today && now >= day.window.start && now <= day.window.end) {
      var yn = yOf(now);
      svg.appendChild(s('line', { id: 'now-line', class: 'now-line', x1: GUTTER, y1: yn, x2: width, y2: yn, stroke: '#1f4fd1', 'stroke-width': 1.5 }));
    }

    emptyEl.hidden = spans.length > 0 || !!day.night;
    renderPhoneStrip(spans, startHour, endHour, today, now);   // §38.4 폰 '오늘' 화면의 하루 띠

    if (scrollToStart || !state.scrolledOnce) {
      state.scrolledOnce = true;
      var targetY = null;
      if (today && now >= day.window.start && now <= day.window.end) targetY = yOf(now);
      else if (spans.length) targetY = yOf(spans[0].start);
      if (targetY !== null) {
        var top = Math.max(0, targetY - wrap.clientHeight * 0.55);
        wrap.scrollTop = top;
      }
    }
  }

  function updateNowLine() {
    var line = $('now-line');
    if (!line || !state.day) return;
    var now = nowTs();
    if (now > state.day.window.end) return;
    if (hourOfTs(now) > tl.endHour) { renderTimeline(false); return; }
    if (tl.strip) renderPhoneStrip(tl.strip.spans, tl.strip.startHour, tl.strip.endHour, tl.strip.today, now);   // §38.4 하루 띠의 지금 선
    var y = yOf(now);
    line.setAttribute('y1', y); line.setAttribute('y2', y);
  }

  // §19 밤 밴드: 채우지 않는 점선 괄호 + accent 라벨. day.spans 에 넣지 않으므로 정정·주 보기·요약에
  // 새어 나가지 않는다. 밤늦게 연구한 날은 fill:none 이라 아래 블록이 그대로 다 보인다.
  // §25: 보이는 밤 구간은 night_start_hour ~ night_band_end_hour(기본 20~23시). 연구 측정 구간과 별개
  function nightShowEndTs(windowStart) {
    var h0 = state.config.day_start_hour || 4;
    var he = cfgHour('night_band_end_hour', 23);
    if (he <= h0) he += 24;
    return windowStart + (he - h0) * 3600;
  }
  function nightShowStartTs(windowStart) {
    var h0 = state.config.day_start_hour || 4;
    var hs = cfgHour('night_start_hour', 20);
    if (hs <= h0) hs += 24;
    return windowStart + (hs - h0) * 3600;
  }
  function nightText(n) {
    var txt = '밤 · ' + (n.kind_name || nightName(n.kind));
    if (n.kind === 'research') txt += n.research_sec ? ' ' + fmtHM(n.research_sec) : '';
    else if (n.checked_ts || n.checked) txt += n.done ? ' ✓' : ' ×';
    return txt;
  }
  function nightBand(n, x0, rectW) {
    var axisEnd = tl.windowStart + (tl.endHour - (state.config.day_start_hour || 4)) * 3600;
    var a = Math.max(n.start, tl.windowStart), b = Math.min(n.end, axisEnd, nightShowEndTs(tl.windowStart));
    var g = s('g', { class: 'night-band' });
    if (!(b > a)) return g;
    var yA = yOf(a), yB = yOf(b);
    g.appendChild(s('rect', {
      x: x0 + 0.5, y: yA, width: rectW - 1, height: Math.max(1, yB - yA), rx: 2,
      fill: 'none', stroke: '#1f4fd1', 'stroke-opacity': 0.45, 'stroke-width': 1, 'stroke-dasharray': '3 3'
    }));
    var txt = nightText(n);
    // span 라벨은 왼쪽 정렬이라 rectW 를 반씩 나눠 최악 겹침을 막는다
    g.appendChild(s('text', {
      class: 'night-label', x: x0 + rectW - 6, y: yA + 13, 'text-anchor': 'end',
      text: truncateToWidth(txt, rectW * 0.5, 11)
    }));
    return g;
  }

  function spanTooltip(sp) {
    var cat = catOf(sp.category);
    var head = hm(sp.start) + '–' + hm(sp.end) + ' (' + mins(sp.end - sp.start) + '분) · ' + cat.name;
    if (sp.category === 'absent') head += ' (출근 전·퇴근 후, PC 켜짐)';
    var lbl = spanLabel(sp);
    if (lbl) head += ' · ' + lbl;
    if (sp.source === 'override') head += ' · [정정됨]';
    // §15: 1줄째 label, 2줄째 원문 제목, 3줄째 exe, 그 아래 apps 상위 3
    var lines = [head];
    if (sp.title) lines.push(sp.title);
    if (sp.exe) lines.push(baseName(sp.exe));
    (sp.apps || []).slice(0, 3).forEach(function (a) {
      var nm = appName(a) || '?';
      lines.push('  ' + nm + (a.title ? ' — ' + a.title : '') + ' · ' + mins(a.sec) + '분');
    });
    return lines.join('\n');
  }

  var _measureCtx = null;
  function textWidth(str, fontPx) {
    if (!_measureCtx) {
      var c = document.createElement('canvas');
      _measureCtx = c.getContext('2d');
    }
    _measureCtx.font = fontPx + 'px "Pretendard Variable", Pretendard, Inter, "Malgun Gothic", "Segoe UI", system-ui, sans-serif';
    return _measureCtx.measureText(str).width;
  }
  function truncateToWidth(str, maxW, fontPx) {
    if (textWidth(str, fontPx) <= maxW) return str;
    var lo = 0, hi = str.length;
    while (lo < hi) {
      var mid = (lo + hi + 1) >> 1;
      if (textWidth(str.slice(0, mid) + '…', fontPx) <= maxW) lo = mid; else hi = mid - 1;
    }
    return lo <= 0 ? '' : str.slice(0, lo) + '…';
  }

  // ------------------------------------------------------------ 요약 · 사이드바
  function renderSummary() {
    var sm = (state.day && state.day.summary) || {};
    var has = function (v) { return v !== null && v !== undefined; };
    var attend = '—';
    if (has(sm.first_active)) {
      attend = hm(sm.first_active) + ' → ' + (sm.in_progress ? '진행 중' : (has(sm.last_active) ? hm(sm.last_active) : '—'));
    }
    $('sum-attend').textContent = attend;
    $('sum-active').textContent = has(sm.active_sec) ? fmtHM(sm.active_sec) : '—';
    if (has(sm.distraction_sec)) {
      var pct = Math.round((sm.distraction_ratio || 0) * 100);
      $('sum-distraction').textContent = mins(sm.distraction_sec) + '분 · ' + pct + '%';
      $('sum-distraction-sub').textContent = '진입 ' + (sm.distraction_entries || 0) + '회';
    } else {
      $('sum-distraction').textContent = '—';
      $('sum-distraction-sub').textContent = '';
    }
    $('sum-away').textContent = (has(sm.away_sec) || has(sm.remote_sec))
      ? mins(sm.away_sec) + '분 / ' + mins(sm.remote_sec) + '분' : '—';
    $('sum-focus').textContent = has(sm.focus_longest_sec) ? mins(sm.focus_longest_sec) + '분' : '—';
    renderPhoneHero();   // §39.2
  }

  function renderUnknownTop() {
    var ul = $('unknown-list');
    clear(ul);
    var items = (state.day && state.day.summary && state.day.summary.unknown_top) || [];
    renderUnknownItems(items);
  }
  function renderUnknownItems(items) {
    var ul = $('unknown-list');
    if (!items.length) {
      ul.appendChild(h('li', { class: 'empty', text: '미분류 항목이 없습니다.' }));
      return;
    }
    items.slice(0, 5).forEach(function (it) {
      var li = h('li', { class: 'list-row unknown-item', title: '누르면 이 프로그램/제목을 분류할 수 있습니다' }, [
        h('span', { class: 'swatch', style: 'background:' + catOf('unknown').color }),
        h('div', { class: 'grow' }, [
          h('div', { class: 'name', text: appName(it) || '(알 수 없음)' }),
          it.title ? h('div', { class: 'sub', text: it.title, title: it.title }) : null,
          // exe 는 작게 — 표시 이름이 exe 폴백과 같으면 중복이므로 생략
          (it.exe && appName(it) !== baseName(it.exe)) ? h('div', { class: 'exe', text: baseName(it.exe) }) : null
        ]),
        h('span', { class: 'mins', text: mins(it.sec) + '분' })
      ]);
      li.addEventListener('click', function (ev) { openPopover({ kind: 'unknown', item: it }, ev); });
      ul.appendChild(li);
    });
  }

  function renderLegend() {
    // §30 타임라인 아래(하루)와 주 보기 아래, 두 곳에 같은 범례
    ['legend', 'legend-week'].forEach(function (id) { var box = $(id); if (box) renderLegendInto(box); });
  }
  function renderLegendInto(box) {
    clear(box);
    state.categories.forEach(function (c) {
      if (c.id === 'off') return;
      var dot = h('span', { class: 'swatch', style: 'background:' + c.color });
      if (c.id === 'distraction') dot.style.backgroundImage = 'repeating-linear-gradient(45deg, rgba(255,255,255,.7) 0 2px, transparent 2px 5px)';
      if (c.id === 'away') dot.style.backgroundImage = 'repeating-linear-gradient(45deg, #9CA3AF 0 1.5px, transparent 1.5px 4px)';
      if (c.id === 'paused') dot.style.border = '1px dashed #9CA3AF';
      var item = h('span', { class: 'legend-item' }, [dot, c.name]);
      if (c.id === 'absent') item.title = '출근 전·퇴근 후에 PC 만 켜져 있던 시간 (활동·자리비움 어디에도 안 들어감)';
      box.appendChild(item);
    });
  }

  // ------------------------------------------------------------ 투두
  // ---------------------------------------------------------------- 밤 일정 (§19)
  function nightKinds() { return (state.night && state.night.kinds) || DEFAULT_NIGHT_KINDS; }

  function nightName(id) {
    var k = nightKinds().filter(function (x) { return x.id === id; })[0];
    return k ? k.name : id;
  }

  function renderNight() {
    renderNightAsk();
    renderNightPick();
    renderTonight();
    var n = state.night || {};
    var w = n.workout || {};
    // 제목 옆 회색 한 줄: 판단(accent 글자)이 아니라 사실만
    var sub;
    if (w.last) sub = '마지막 운동 ' + mdStr(w.last) + (w.gap === 0 ? ' · 오늘' : ' · ' + w.gap + '일 전');
    else sub = '운동 기록 없음';
    $('night-sub').textContent = sub;
    $('night-sub').title = w.last
      ? ('마지막 운동 ' + w.last + (w.due ? ' — 오늘 차례' : ''))
      : '운동 기록이 아직 없습니다 — 오늘 차례';
    var t = n.today;
    $('night-note').textContent = t ? ('오늘 밤 — ' + (t.kind_name || nightName(t.kind))) : '아직 안 정함';
  }

  // §22: 저녁이 되고 오늘 밤을 아직 안 정했으면 사이드바 맨 위에 큰 버튼으로. 판정은 시각에 달렸으므로
  // updateTonight 는 30초 루프에서도 부른다(조회·재그리기 없이 hidden 만).
  function tonightHour() {
    var c = state.day && state.day.config;
    return (c && typeof c.night_prompt_hour === 'number') ? c.night_prompt_hour : 16;
  }
  function updateTonight() {
    var n = state.night;
    var show = !!n && isToday() && !n.today && new Date().getHours() >= tonightHour();
    $('card-tonight').hidden = !show;
  }
  function renderTonight() {
    var box = $('tonight-pick');
    clear(box);
    var due = !!(state.night && state.night.workout && state.night.workout.due);
    nightKinds().forEach(function (k) {
      box.appendChild(h('button', {
        class: 'ghostbtn tonight-btn' + ((k.id === 'workout' && due) ? ' due' : ''), type: 'button', text: k.name,
        title: '오늘 밤을 ' + k.name + '(으)로 정합니다' + ((k.id === 'workout' && due) ? ' — 운동 차례' : ''),
        onclick: function () { setNightPlan(k.id); }
      }));
    });
    updateTonight();
  }

  function renderNightPick() {
    var n = state.night || {};
    var t = n.today, due = !!(n.workout && n.workout.due);
    var box = $('night-pick');
    clear(box);
    nightKinds().forEach(function (k) {
      var on = !!(t && t.kind === k.id);
      var cls = 'ghostbtn tonight-btn' + (on ? ' on' : '') + ((k.id === 'workout' && due && !on) ? ' due' : '');
      box.appendChild(h('button', {
        class: cls, type: 'button', text: k.name,
        title: on ? '선택 해제' : (k.name + '(으)로 정하기'),
        onclick: function () { if (on) { clearNightPlan(t.id); } else { setNightPlan(k.id); } }
      }));
    });
    // §23.1 결과 줄: 물어보는 종류면 어느 날짜든 했음/안 했음을 바로 고칠 수 있다
    var res = $('night-result');
    clear(res);
    res.hidden = !(t && t.ask);
    if (t && t.ask) {
      var checked = t.checked_ts !== null && t.checked_ts !== undefined;
      res.appendChild(h('span', { text: '결과' }));
      [[1, '했음'], [0, '안 했음']].forEach(function (o) {
        var on = checked && t.done === o[0];
        res.appendChild(h('button', { class: 'act' + (on ? ' on' : ''), type: 'button', text: o[1],
          title: on ? '지금 기록' : (o[1] + '(으)로 기록'), onclick: function () { if (!on) confirmNight(t.id, o[0]); } }));
      });
      if (!checked) res.appendChild(h('span', { class: 'hint', text: '아직 기록 없음' }));
    }
  }

  // 다음날 출근해서 확인한다(§19.8). '출근' 은 summary.first_active 를 그대로 쓴다 — 원격은 active 가
  // 아니므로 집에서 밤늦게 원격으로 앱을 열어도 이 블록은 뜨지 않는다.
  function renderNightAsk() {
    var box = $('night-ask');
    var pend = (state.night && state.night.pending) || [];
    clear(box);
    var sm = (state.day && state.day.summary) || null;
    var arrived = !!(sm && sm.first_active !== null && sm.first_active !== undefined);
    var dead = !!(state.status && state.status.alive === false); // 트래커가 끊겨 판정 자체가 불가능
    box.hidden = !pend.length || !isToday() || !(arrived || dead);
    if (box.hidden) return;
    var y = shiftDate(todayStr(), -1);
    pend.forEach(function (p) {
      var when = (p.day === y ? '어제 밤' : mdStr(p.day) + ' 밤');
      box.appendChild(h('div', { class: 'night-ask-row' }, [
        h('span', { class: 'night-ask-txt', text: when + ' ' + (p.kind_name || nightName(p.kind)) }),
        h('button', { class: 'act', type: 'button', text: '했음', title: '했다고 기록합니다',
                      onclick: function () { confirmNight(p.id, 1); } }),
        h('button', { class: 'act', type: 'button', text: '안 했음', title: '안 했다고 기록합니다',
                      onclick: function () { confirmNight(p.id, 0); } }),
        h('button', { class: 'act', type: 'button', text: '다른 걸 했음', title: '계획과 다른 것을 했으면 그것으로 바꿔 기록합니다',
                      onclick: function (e) { toggleNightAlt(e.currentTarget.parentNode, p); } })
      ]));
    });
  }
  // §23.1 '다른 걸 했음': 종류 다섯을 펼쳐 고르면 그 날 기록을 그 종류·이행으로 한 번에 바꾼다
  function toggleNightAlt(row, p) {
    var old = row.nextSibling;
    if (old && old.className === 'night-ask-alt') { old.remove(); return; }
    var alt = h('div', { class: 'night-ask-alt' });
    nightKinds().forEach(function (k) {
      if (k.id === p.kind) return;
      alt.appendChild(h('button', { class: 'act', type: 'button', text: k.name, title: '그 날 밤을 ' + k.name + '(으)로 기록',
        onclick: function () {
          api('/api/night', { day: p.day, kind: k.id, done: 1 }).then(afterNight).catch(fail('밤 일정 기록 실패'));
        } }));
    });
    row.parentNode.insertBefore(alt, row.nextSibling);
  }

  // 변경은 전부 POST 1회 + 재조회. DOM 을 미리 고치지 않는다(투두 규약).
  // 타임라인 밤 밴드도 같은 표를 보므로 loadDay 를 함께 부른다.
  function afterNight() { loadDay(); return loadNight(); }
  function setNightPlan(kind) {
    api('/api/night', { day: sideDate(), kind: kind }).then(afterNight).catch(fail('밤 일정 저장 실패'));
  }
  function clearNightPlan(id) {
    api('/api/night/delete', { id: id }).then(afterNight).catch(fail('밤 일정 삭제 실패'));
  }
  function confirmNight(id, done) {
    api('/api/night/confirm', { id: id, done: done }).then(afterNight).catch(fail('밤 일정 기록 실패'));
  }

  // ------------------------------------------------------------ 예산 (§21)
  function fmtWon(n) { return (n < 0 ? '−' : '') + Math.abs(n).toLocaleString('ko-KR'); }
  var SPEND_CAT_KEY = 'horarium.spendCat';

  function renderBudget() {
    var b = state.budget;
    if (!b) return;
    var m = parseInt(b.month.slice(5), 10);
    var hasPlan = b.plan.some(function (r) { return !r.extra; });
    $('budget-sub').textContent = hasPlan
      ? m + '월 · 남음 ' + fmtWon(b.total.left) + ' / ' + fmtWon(b.total.amount)
      : m + '월 · 배분 없음';

    var ul = $('budget-plan');
    clear(ul);
    if (!hasPlan && !b.plan.length) {
      ul.appendChild(h('li', { class: 'empty', text: '이 달 배분이 없습니다. 오른쪽 위 \'배분\' 을 누르세요.' }));
    }
    b.plan.forEach(function (r) {
      ul.appendChild(h('li', { class: 'list-row' }, [
        h('span', { class: 'budget-cat', text: r.category }),
        h('span', { class: 'budget-left' + (r.left < 0 ? ' over' : ''), text: fmtWon(r.left) + ' 남음' }),
        h('span', { class: 'budget-amt', text: r.extra ? '배분 없음' : '(' + fmtWon(r.amount) + ')' })
      ]));
    });

    // 항목 셀렉트: 배분 항목 + 기타. 마지막 선택을 기억
    var sel = $('spend-cat');
    var want = sel.value || (function () { try { return localStorage.getItem(SPEND_CAT_KEY) || ''; } catch (e) { return ''; } })();
    clear(sel);
    b.plan.filter(function (r) { return !r.extra; }).forEach(function (r) {
      sel.appendChild(h('option', { value: r.category, text: r.category }));
    });
    sel.appendChild(h('option', { value: '', text: '기타' }));
    sel.value = want;
    if (sel.value !== want) sel.value = sel.options.length ? sel.options[0].value : '';

    var sl = $('spend-list');
    clear(sl);
    b.day.forEach(function (s) {
      sl.appendChild(h('li', { class: 'list-row' }, [
        h('span', { class: 'spend-amt', text: fmtWon(s.amount) }),
        h('span', { class: 'spend-note', text: s.note || '' }),
        h('span', { class: 'spend-cat', text: s.category || '기타' }),
        h('button', { class: 'act btn-del', title: '삭제', text: '×', onclick: function () {
          api('/api/spend/delete', { id: s.id }).then(loadBudget).catch(fail('삭제 실패'));
        } })
      ]));
    });
    $('btn-budget-prev').hidden = !(b.prev && b.prev.length);
  }

  function addSpend() {
    var input = $('spend-input');
    var text = input.value.trim();
    if (!text) return;
    var cat = $('spend-cat').value;
    try { localStorage.setItem(SPEND_CAT_KEY, cat); } catch (e) {}
    input.value = '';
    api('/api/spend', { day: sideDate(), text: text, category: cat }).then(loadBudget).catch(function (e) {
      input.value = text; fail('지출 추가 실패')(e);
    });
  }

  function openBudgetEdit() {
    var b = state.budget || { plan: [] };
    $('budget-text').value = b.plan.filter(function (r) { return !r.extra; })
      .map(function (r) { return r.category + ' ' + r.amount; }).join('\n');
    $('budget-edit').hidden = false;
    $('budget-text').focus();
  }
  function closeBudgetEdit() { $('budget-edit').hidden = true; }
  function saveBudgetPlan(items) {
    var b = state.budget;
    if (!b) return;
    api('/api/budget/plan', { month: b.month, items: items }).then(function () {
      closeBudgetEdit(); return loadBudget();
    }).catch(fail('배분 저장 실패'));
  }
  function parsePlanText(text) {
    // 한 줄 '항목 금액'. 금액은 마지막 숫자 덩어리(쉼표·만·천 허용)
    var items = [];
    text.split('\n').forEach(function (line) {
      line = line.trim();
      if (!line) return;
      var m = line.match(/^(.*?)\s*([\d,]+(?:\.\d+)?)\s*(만|천|원)?\s*$/);
      if (!m || !m[1].trim()) throw new Error('알아듣지 못한 줄: ' + line);
      var n = parseFloat(m[2].replace(/,/g, ''));
      if (m[3] === '만') n *= 10000; else if (m[3] === '천') n *= 1000;
      items.push({ category: m[1].trim(), amount: Math.round(n) });
    });
    return items;
  }

  // ------------------------------------------------------------ 구역 접기 (§29.5)
  // 구역 머리를 누르면 다음 구역 머리 전까지의 카드를 접는다. 접힌 상태는 기억한다(localStorage).
  var ZONE_KEY = 'horarium.zonesFolded';
  function zoneCards(z) {
    var out = [], el = z.nextElementSibling;
    while (el && !el.classList.contains('zone')) { if (!el.classList.contains('foot-links')) out.push(el); el = el.nextElementSibling; }
    return out;
  }
  function applyZoneFold(z, folded) {
    z.classList.toggle('folded', folded);
    z.querySelector('.zone-fold').textContent = folded ? '펼치기' : '접기';
    zoneCards(z).forEach(function (c) { c.classList.toggle('zfold', folded); });
  }
  function initZones() {
    var folded = {};
    try { folded = JSON.parse(localStorage.getItem(ZONE_KEY) || '{}') || {}; } catch (e) { folded = {}; }
    document.querySelectorAll('#sidebar .zone').forEach(function (z) {
      var key = z.getAttribute('data-zone');
      applyZoneFold(z, !!folded[key]);
      z.addEventListener('click', function (ev) {
        if (ev.target.tagName === 'A') return;
        var now = !z.classList.contains('folded');
        applyZoneFold(z, now);
        folded[key] = now;
        try { localStorage.setItem(ZONE_KEY, JSON.stringify(folded)); } catch (e) {}
      });
    });
  }
  function setZoneCount(id, text) { var el = $(id); if (el) el.textContent = text || ''; }

  // ------------------------------------------------------------ 할 일 (통합, §29)
  // 입력 하나: 숫자·날짜 표현·마감 낱말·때가 있는 낱말이 있으면 일정 쪽(/api/events, Claude 가 가름), 아니면 그냥 할 일
  var NEEDS_PARSE = /\d|오늘|내일|모레|다음|이번|요일|매주|마감|제출|접수|신청|학회|수업|회의|발표|실습|까지/;
  function addTask() {
    var input = $('todo-input'), text = input.value.trim();
    if (!text) return;
    input.value = '';
    var p = NEEDS_PARSE.test(text)
      ? api('/api/events', { text: text }).then(loadEvents)
      : api('/api/todos', { text: text, day: sideDate() }).then(loadTodos);
    p.catch(function (e) { input.value = text; fail('추가 실패')(e); });
  }
  function addTodo() { addTask(); }
  function renderTodos() { renderTasks(); }

  function todoRow(it, carried) {
    var kids = [h('span', { class: 'evt-d', text: '·' }), h('span', { class: 'todo-text', text: it.text })];
    if (!it.done) {
      // 글자가 곧 상태다: 대기(회색) ↔ 진행(accent)
      kids.push(h('button', { class: 'act todo-doing' + (it.doing ? ' on' : ''), title: it.doing ? '누르면 대기로 되돌립니다' : '누르면 진행 중으로 표시합니다', text: it.doing ? '진행' : '대기', onclick: function () {
        api('/api/todos/update', { id: it.id, doing: it.doing ? 0 : 1 }).then(loadTodos).catch(fail('할 일 갱신 실패'));
      } }));
    }
    kids.push(h('button', { class: 'act', title: it.done ? '다시 안 끝난 것으로' : '끝났다고 표시', text: it.done ? '되돌리기' : '완료', onclick: function () {
      var body = { id: it.id, done: it.done ? 0 : 1 };
      if (carried && !it.done) body.day = sideDate();   // §20: 전에 적은 할 일을 끝내면 '그 날 한 일' 로
      api('/api/todos/update', body).then(loadTodos).catch(fail('할 일 갱신 실패'));
    } }));
    kids.push(h('button', { class: 'act btn-del', title: '삭제', text: '×', onclick: function () {
      api('/api/todos/delete', { id: it.id }).then(loadTodos).catch(fail('삭제 실패'));
    } }));
    var li = h('li', { class: 'list-row undated' + (it.done ? ' done' : '') + (it.doing && !it.done ? ' doing' : '') }, kids);
    if (carried) li.title = it.day + ' 에 적음';
    return li;
  }
  // 한 목록에 묶음별로: 지남·오늘 → 날짜 확인 → 이번 주 → 날짜 없음 → 나중에 → 완료(접힘)
  function renderTasks() {
    var ul = $('todo-list');
    clear(ul);
    var t = state.todos || { today: [], carried: [] };
    var plans = ((state.events && state.events.items) || []).filter(function (x) { return x.kind !== 'event'; });
    var open = plans.filter(function (x) { return !x.done; });
    var groups = [
      ['지남 · 오늘', open.filter(function (x) { return x.occ && x.d <= 0; }).map(function (x) { return evtRow(x, false); })],
      ['날짜 확인', open.filter(function (x) { return !x.occ; }).map(function (x) { return evtRow(x, false); })],
      ['이번 주', open.filter(function (x) { return x.occ && x.d > 0 && x.d <= 7; }).map(function (x) { return evtRow(x, false); })],
      ['날짜 없음', (t.today || []).filter(function (x) { return !x.done; }).map(function (x) { return todoRow(x, false); })
                   .concat((t.carried || []).map(function (x) { return todoRow(x, true); }))],
      ['나중에', open.filter(function (x) { return x.occ && x.d > 7; }).map(function (x) { return evtRow(x, false); })]
    ];
    var any = false;
    groups.forEach(function (g) {
      if (!g[1].length) return;
      any = true;
      ul.appendChild(h('li', { class: 'sub-title', text: g[0] }));
      g[1].forEach(function (li) { ul.appendChild(li); });
    });
    if (!any) ul.appendChild(h('li', { class: 'empty', text: '할 일이 없습니다. 위에 적어 보세요 — 날짜가 있으면 알아서 가릅니다.' }));
    var done = (t.today || []).filter(function (x) { return x.done; }).map(function (x) { return todoRow(x, false); })
      .concat(plans.filter(function (x) { return x.done; }).map(function (x) { return evtRow(x, false); }));
    if (done.length) {
      var tog = h('button', { class: 'act', type: 'button', text: state.tasksDoneOpen ? '접기' : '보기', onclick: function () { state.tasksDoneOpen = !state.tasksDoneOpen; renderTasks(); } });
      ul.appendChild(h('li', { class: 'sub-title' }, [document.createTextNode('완료 ' + done.length + ' '), tog]));
      if (state.tasksDoneOpen) done.forEach(function (li) { ul.appendChild(li); });
    }
    var openTodos = ((t.today || []).filter(function (x) { return !x.done; })).length + (t.carried || []).length;
    setZoneCount('zone-mine-n', '할 일 ' + (open.length + openTodos));
    renderTasksPhone();   // §39.2 폰 전용 목록
  }

  // ------------------------------------------------------------ 메모
  var notes = {
    today: { name: null, timer: null, el: null, stateEl: null, loaded: '' },
    pinned: { name: 'pinned', timer: null, el: null, stateEl: null, loaded: '' }
  };
  // §30 칸을 내용만큼 늘린다. 숨은 탭(폭 0)에서는 scrollHeight 가 폭발하므로 건너뛴다(HANDOFF §1.2)
  function autogrowNote(el) {
    if (!el || el.clientWidth < 80) return;
    el.style.height = 'auto';
    var max = Math.round(parseFloat(getComputedStyle(el).lineHeight || '18') * 21);
    el.style.height = Math.min(el.scrollHeight + 2, max) + 'px';
  }
  function setNoteText(which, text, name, ver) {
    var n = notes[which];
    n.name = name;
    n.loaded = text;
    n.ver = ver || null;      // §27 불러온 판 — 저장할 때 같이 보내 덮어쓰기를 막는다
    if (n.timer) { clearTimeout(n.timer); n.timer = null; }
    n.el.value = text;
    autogrowNote(n.el);
    n.el.disabled = false;
    n.stateEl.textContent = '';
  }
  // 날짜 전환 중 / 불러오기 실패 시 입력을 잠가 엉뚱한 날짜 파일에 저장되는 것을 막는다
  function lockNote(which, msg) {
    var n = notes[which];
    if (!n.el) return;
    n.name = null;
    if (n.timer) { clearTimeout(n.timer); n.timer = null; }
    n.el.value = '';
    n.el.disabled = true;
    n.stateEl.textContent = msg || '';
  }
  // 대기 중인 디바운스 저장을 즉시 실행 (날짜 변경·포커스 아웃·창 닫기)
  function flushNote(which, keepalive) {
    var n = notes[which];
    if (!n || !n.timer || !n.name) return;
    clearTimeout(n.timer); n.timer = null;
    var name = n.name, text = n.el.value;
    if (keepalive && !MOCK && typeof fetch === 'function') {
      try {
        var kb = { name: name, text: text };
        // §27 창을 닫는 중엔 되물을 수 없다 → 다른 기기가 바꿨으면 서버가 뒤에 붙이게 한다(버리지 않음)
        if (n.ver) { kb.base = n.ver; kb.on_conflict = 'append'; if (text.indexOf(n.loaded) === 0) kb.tail = text.slice(n.loaded.length); }
        fetch('/api/note', { method: 'POST', keepalive: true, headers: { 'Content-Type': 'application/json; charset=utf-8' }, body: JSON.stringify(kb) });
      } catch (e) { /* 창 닫는 중 — 무시 */ }
      return;
    }
    saveNote(n, name, text);
  }
  // ---- §27 두 기기에서 쓰기
  function noteWhich(n) { return n === notes.today ? 'today' : 'pinned'; }
  // 저장: 내가 불러온 판(base)을 같이 보낸다. 그 사이 다른 기기가 바꿨으면 서버가 덮어쓰지 않고 그 내용을 돌려준다
  function saveNote(n, name, text) {
    var body = { name: name, text: text };
    if (n.ver) body.base = n.ver;
    return api('/api/note', body).then(function (r) {
      if (n.name !== name) return;
      if (r && r.conflict) { mergeNote(n, text, r); return; }
      if (r && r.ver) n.ver = r.ver;
      n.loaded = text;
      n.stateEl.textContent = '저장됨 ' + hms(new Date());
    }).catch(function (e) {
      if (n.name === name) n.stateEl.textContent = '저장 실패';
      fail('메모 저장 실패')(e);
    });
  }
  // 다른 기기가 먼저 바꿨다 — 아무것도 버리지 않고 합친 뒤 다시 저장한다
  function mergeNote(n, mine, r) {
    var theirs = r.text || '';
    var merged;
    if (mine.indexOf(n.loaded) === 0) merged = theirs + mine.slice(n.loaded.length);   // 뒤에 덧붙이기만 했으면 그 부분만 얹는다
    else merged = theirs + (theirs && !/\n$/.test(theirs) ? '\n' : '') + '\n— 이 기기에서 쓴 것 (' + hms(new Date()) + ') —\n' + mine;
    n.loaded = theirs; n.ver = r.ver;
    n.el.value = merged;
    n.stateEl.textContent = '다른 기기 내용과 합침';
    if (merged !== theirs) scheduleNoteSave(noteWhich(n));
  }
  // 쓰는 중이 아닐 때만 서버 것으로 갈아 끼운다
  function refreshNotes() {
    ['today', 'pinned'].forEach(function (which) {
      var n = notes[which];
      if (!n.name || !n.el || n.el.disabled) return;
      if (n.timer || n.el.value !== n.loaded || document.activeElement === n.el) return;
      var name = n.name;
      api('/api/note?name=' + encodeURIComponent(name)).then(function (r) {
        if (n.name !== name || n.timer || n.el.value !== n.loaded) return;   // 그 사이 쓰기 시작했으면 손대지 않는다
        var t = r.text || '';
        if (t !== n.loaded) { n.el.value = t; n.loaded = t; n.stateEl.textContent = '다른 기기에서 바뀜 ' + hms(new Date()); }
        n.ver = r.ver || null;
      }).catch(function () { /* 다음 번에 다시 */ });
    });
  }
  // 창에 돌아왔을 때, 또는 켜 둔 채 안 쓰는 동안 옆 판을 다시 받는다 (다른 기기에서 적은 것이 보이게)
  var sideAt = 0;
  function refreshSide() {
    if (!state.date || state.popover) return;
    // §48 앱으로 돌아오면 focus 와 visibilitychange 가 같이 와서 같은 요청을 두 벌 보냈다 — 2초 안의 두 번째는 건너뛴다
    var t = Date.now();
    if (t - sideAt < 2000) return;
    sideAt = t;
    loadTodos(); loadNight(); loadBudget(); loadSchedule(); loadEvents();
    loadDrawer();
    refreshNotes();
  }
  function scheduleNoteSave(which) {
    var n = notes[which];
    if (!n.name) { n.stateEl.textContent = '불러오기 실패 — 저장 안 됨'; return; }
    n.stateEl.textContent = '저장 중…';
    if (n.timer) clearTimeout(n.timer);
    n.timer = setTimeout(function () {
      n.timer = null;
      saveNote(n, n.name, n.el.value);
    }, NOTE_DEBOUNCE_MS);
  }

  // ------------------------------------------------------------ 팝오버
  function openPopover(ctx, ev) {
    var pop = $('popover');
    var span = ctx.kind === 'span' ? ctx.span : null;
    var item = ctx.kind === 'unknown' ? ctx.item : null;
    var cat = catOf(span ? span.category : 'unknown');
    var exe = span ? baseName(span.exe) : (item ? item.exe : '');
    var title = span ? (span.title || '') : (item ? (item.title || '') : '');
    var system = span && cat.kind === 'system';
    ctx.exe = exe; ctx.title = title; ctx.category = cat.id;
    state.popover = ctx;

    if (span) $('pop-time').textContent = hm(span.start) + '–' + hm(span.end) + ' (' + mins(span.end - span.start) + '분)';
    else $('pop-time').textContent = (state.view === 'day' ? '오늘 합계 ' : '기간 합계 ') + mins(item.sec) + '분';
    // §15: 제목 줄은 카테고리 · 표시 이름(label; 없으면 exe)
    var headName = span ? spanLabel(span) : appName(item);
    var catLine = cat.name;
    if (headName) catLine += ' · ' + headName;
    if (span && span.source === 'override') catLine += ' · 정정됨';
    $('pop-cat').textContent = catLine;

    var apps = $('pop-apps');
    clear(apps);
    var list = span ? (span.apps || []) : [{ exe: item.exe, title: item.title, sec: item.sec, name: item.name }];
    if (!list.length && (exe || title)) list = [{ exe: exe, title: title, sec: span.end - span.start, name: span.label }];
    if (!list.length) apps.appendChild(h('li', { class: 'none', text: '프로그램 정보 없음' }));
    list.forEach(function (a) {
      // name — 원문 제목 · m분 (name 없으면 exe). exe 는 hover 툴팁으로.
      var nm = appName(a) || '?';
      apps.appendChild(h('li', { title: (baseName(a.exe) || '') + (a.title ? ' · ' + a.title : '') }, [
        h('span', { class: 'name', text: nm }),
        h('span', { class: 'ttl', text: a.title ? '— ' + a.title : '' }),
        h('span', { class: 'mins', text: mins(a.sec) + '분' })
      ]));
    });

    var grid = $('pop-cats');
    clear(grid);
    manualCategories().forEach(function (c) {
      var b = h('button', { class: 'dlbl' + (c.id === cat.id ? ' on' : ''), title: c.name + '(으)로 지정', onclick: function () { applyChoice(c.id); } }, [
        h('span', { class: 'cdot', style: 'background:' + c.color }), c.name
      ]);
      grid.appendChild(b);
    });

    // 범위 라디오
    var onlyWrap = $('scope-only-wrap'), exeWrap = $('scope-exe-wrap'), titleWrap = $('scope-title-wrap');
    var radios = pop.querySelectorAll('input[name=scope]');
    $('scope-exe-name').textContent = exe || '(없음)';
    $('scope-title-input').value = cleanTitleKeyword(title);
    if (system) {
      $('pop-scope').hidden = true;
      onlyWrap.hidden = false; exeWrap.hidden = true; titleWrap.hidden = true;
      radios[0].checked = true;
    } else if (item) {
      $('pop-scope').hidden = false;
      onlyWrap.hidden = true; exeWrap.hidden = false; titleWrap.hidden = false;
      radios[1].checked = true;
    } else {
      $('pop-scope').hidden = false;
      onlyWrap.hidden = false; exeWrap.hidden = !exe; titleWrap.hidden = !title;
      radios[0].checked = true;
    }
    $('pop-undo').hidden = !(span && span.source === 'override' && span.override_id);

    pop.hidden = false;
    placePopover(pop, ev);
  }

  function placePopover(pop, ev) {
    var vw = document.documentElement.clientWidth, vh = document.documentElement.clientHeight;
    var pw = pop.offsetWidth, ph = pop.offsetHeight;
    var cx = ev ? ev.clientX : vw / 2, cy = ev ? ev.clientY : vh / 2;
    var left = cx + 14, top = cy - 20;
    if (left + pw > vw - 8) left = cx - pw - 14;
    if (left < 8) left = 8;
    if (top + ph > vh - 8) top = vh - ph - 8;
    if (top < 8) top = 8;
    pop.style.left = (left + window.scrollX) + 'px';
    pop.style.top = (top + window.scrollY) + 'px';
  }

  function closePopover() {
    var pop = $('popover');
    if (pop.hidden) return;
    pop.hidden = true;
    state.popover = null;
    if (state.view !== 'day') {
      if (state.rangePending) { state.rangePending = false; loadRange(); }
      return;
    }
    if (state.dayPending) { state.dayPending = false; loadDay(); }
  }
  // 현재 보기에 맞는 재조회 (정정·규칙 적용 뒤)
  function reloadCurrent() { return state.view === 'day' ? loadDay() : loadRange(); }

  function currentScope() {
    var r = document.querySelector('#popover input[name=scope]:checked');
    return r ? r.value : 'only';
  }

  function applyChoice(catId) {
    var ctx = state.popover;
    if (!ctx) return;
    var scope = ctx.kind === 'unknown' ? currentScope() : (catOf(ctx.category).kind === 'system' ? 'only' : currentScope());
    if (scope === 'only') applyOverride(catId);
    else applyRule(catId, scope);
  }

  function applyOverride(catId) {
    var ctx = state.popover;
    if (!ctx || ctx.kind !== 'span') return;
    var sp = ctx.span;
    api('/api/override', { start: sp.start, end: sp.end, label: catId }).then(function () {
      toast(hm(sp.start) + '–' + hm(sp.end) + ' → ' + catOf(catId).name, 1800);
      closePopover();
      return reloadCurrent();
    }).catch(fail('정정 실패'));
  }

  function applyRule(catId, target) {
    var ctx = state.popover;
    if (!ctx) return;
    var pattern = target === 'exe' ? ctx.exe : $('scope-title-input').value.trim();
    if (!pattern) { toast(target === 'exe' ? '프로그램 이름이 없습니다.' : '제목 키워드를 입력하세요.'); return; }
    api('/api/rule', { target: target, pattern: pattern, category: catId, is_regex: false }).then(function () {
      toast((target === 'exe' ? '프로그램 ' : '제목 "') + pattern + (target === 'exe' ? '' : '"') + ' → ' + catOf(catId).name + ' 규칙 추가', 2200);
      closePopover();
      return reloadCurrent();
    }).catch(fail('규칙 추가 실패'));
  }

  function undoOverride() {
    var ctx = state.popover;
    if (!ctx || ctx.kind !== 'span' || !ctx.span.override_id) return;
    api('/api/override/delete', { id: ctx.span.override_id }).then(function () {
      toast('정정을 취소했습니다.', 1800);
      closePopover();
      return reloadCurrent();
    }).catch(fail('정정 취소 실패'));
  }

  // ------------------------------------------------------------ 일시정지
  function pause(body) {
    api('/api/pause', body).then(function (r) {
      if (state.status) state.status.paused_until = r.paused_until;
      renderStatus();
      return loadStatus();
    }).catch(fail('일시정지 실패'));
  }

  // ------------------------------------------------------------ 이벤트 배선
  function wire() {
    notes.today.el = $('note-today'); notes.today.stateEl = $('note-today-state');
    notes.pinned.el = $('note-pinned'); notes.pinned.stateEl = $('note-pinned-state');
    // §30 메모 탭(오늘/고정) — 마지막에 보던 탭을 기억. 칸은 쓴 만큼 자란다
    var noteTab = 'today';
    try { noteTab = localStorage.getItem('horarium.noteTab') || 'today'; } catch (e) {}
    function showNoteTab(which) {
      noteTab = which;
      document.querySelectorAll('.note-tabs .seg-btn').forEach(function (b) { b.classList.toggle('active', b.getAttribute('data-note') === which); });
      $('note-pane-today').hidden = which !== 'today';
      $('note-pane-pinned').hidden = which !== 'pinned';
      autogrowNote(notes[which].el);
      try { localStorage.setItem('horarium.noteTab', which); } catch (e) {}
    }
    document.querySelectorAll('.note-tabs .seg-btn').forEach(function (b) { b.addEventListener('click', function () { showNoteTab(b.getAttribute('data-note')); }); });
    showNoteTab(noteTab === 'pinned' ? 'pinned' : 'today');
    notes.today.el.addEventListener('input', function () { autogrowNote(notes.today.el); });
    notes.pinned.el.addEventListener('input', function () { autogrowNote(notes.pinned.el); });
    notes.today.el.addEventListener('input', function () { scheduleNoteSave('today'); });
    notes.pinned.el.addEventListener('input', function () { scheduleNoteSave('pinned'); });
    notes.today.el.addEventListener('blur', function () { flushNote('today'); });
    notes.pinned.el.addEventListener('blur', function () { flushNote('pinned'); });
    window.addEventListener('beforeunload', function () { flushNote('today', true); flushNote('pinned', true); });

    $('btn-prev').addEventListener('click', function () { navigatePeriod(-1); });
    $('btn-next').addEventListener('click', function () { navigatePeriod(1); });
    $('btn-today').addEventListener('click', todayClick);
    $('date-input').addEventListener('change', function () { if (this.value && state.view === 'day') changeDate(this.value); });
    Array.prototype.forEach.call(document.querySelectorAll('#view-seg .seg-btn'), function (b) {
      b.addEventListener('click', function () { setView(b.getAttribute('data-view'), state.date || todayStr()); });
    });
    $('link-restart').addEventListener('click', function (ev) {
      ev.preventDefault();
      if (!window.confirm('UI 서버를 다시 시작할까요? (3초 뒤 화면을 새로고침합니다)')) return;
      restartServer();
    });

    $('btn-pause-1h').addEventListener('click', function () { pause({ minutes: 60 }); });
    $('btn-pause-day').addEventListener('click', function () { pause({ until: 'today' }); });
    $('btn-resume').addEventListener('click', function () { pause({ minutes: 0 }); });

    $('btn-todo-add').addEventListener('click', addTodo);
    // §21 예산
    $('btn-spend-add').addEventListener('click', addSpend);
    $('btn-anki-refresh').addEventListener('click', function () { loadAnki(true); });
    $('spend-input').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); addSpend(); } });
    $('btn-budget-edit').addEventListener('click', function () { if ($('budget-edit').hidden) openBudgetEdit(); else closeBudgetEdit(); });
    $('btn-budget-cancel').addEventListener('click', closeBudgetEdit);
    $('btn-budget-save').addEventListener('click', function () {
      var items;
      try { items = parsePlanText($('budget-text').value); } catch (e) { toast(e.message, 3000); return; }
      saveBudgetPlan(items);
    });
    $('btn-budget-prev').addEventListener('click', function () {
      var b = state.budget; if (b && b.prev && b.prev.length) saveBudgetPlan(b.prev);
    });
    // §20.8: 단어 맞추기 팝업 — 데이터는 Athenaeum(8770) 에 있고 여기서는 창만 연다
    $('btn-vocab').addEventListener('click', function () {
      // §36.3 Athenaeum 은 http 로 열려 있다 — 호라리움을 https(테일넷)로 열었어도 단어장은 http 로
      var w = window.open('http://' + location.hostname + ':8770/vocab/quiz', 'horarium-vocab', 'popup=yes,width=380,height=560');
      if (!w) toast('팝업이 막혔습니다. 브라우저의 팝업 허용을 확인하세요.', 3000);
    });
    // §39.1 테마 바꾸기 (이 기기에만)
    $('link-theme').textContent = THEME === 'dark' ? '밝은 화면' : '어두운 화면';
    $('link-theme').addEventListener('click', function (ev) { ev.preventDefault(); toggleTheme(); });
    // §36 다른 기기
    $('link-logout').addEventListener('click', function (ev) {
      ev.preventDefault();
      api('/api/logout', {}).catch(function () {}).then(function () { location.href = '/login'; });
    });
    // §35 서랍
    $('btn-drawer-add').addEventListener('click', function () { if ($('drawer-add').hidden) openDrawerAdd(null); else closeDrawerAdd(); });
    $('btn-drawer-cancel').addEventListener('click', closeDrawerAdd);
    $('btn-drawer-save').addEventListener('click', saveDrawer);
    $('btn-drawer-file').addEventListener('click', function () { $('drawer-file').click(); });
    $('drawer-file').addEventListener('change', function () { setDrawerFile(this.files && this.files[0]); });
    $('drawer-label').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); saveDrawer(); } });
    $('drawer-value').addEventListener('paste', function (e) {
      var items = (e.clipboardData && e.clipboardData.items) || [];
      for (var i = 0; i < items.length; i++) {
        if (items[i].kind === 'file' && /^image\//.test(items[i].type)) { e.preventDefault(); setDrawerFile(items[i].getAsFile()); return; }
      }
    });
    $('todo-input').addEventListener('keydown', function (e) { if (e.key === 'Enter') { e.preventDefault(); addTodo(); } });

    $('pop-close').addEventListener('click', closePopover);
    $('pop-undo').addEventListener('click', undoOverride);
    $('scope-title-input').addEventListener('focus', function () {
      var r = document.querySelector('#popover input[name=scope][value=title]');
      if (r) r.checked = true;
    });

    $('timeline').addEventListener('click', function (ev) {
      var t = ev.target;
      while (t && t !== this && !(t.classList && t.classList.contains('span'))) t = t.parentNode;
      if (!t || t === this) return;
      var idx = +t.getAttribute('data-idx');
      var sp = state.day && state.day.spans && state.day.spans[idx];
      if (!sp) return;
      ev.stopPropagation();
      openPopover({ kind: 'span', span: sp }, ev);
    });
    $('week-svg').addEventListener('click', function (ev) {
      var t = ev.target;
      while (t && t !== this && !(t.classList && t.classList.contains('span'))) t = t.parentNode;
      if (!t || t === this) return;
      var di = +t.getAttribute('data-di'), idx = +t.getAttribute('data-idx');
      var d = wk.days && wk.days[di];
      var sp = d && d.spans && d.spans[idx];
      if (!sp) return;
      ev.stopPropagation();
      openPopover({ kind: 'span', span: sp, date: d.date }, ev);
    });

    document.addEventListener('click', function (ev) {
      if (!state.popover) return;
      var pop = $('popover');
      if (pop.contains(ev.target)) return;
      var t = ev.target;
      while (t) {
        if (t.classList && (t.classList.contains('span') || t.classList.contains('unknown-item'))) return;
        t = t.parentNode;
      }
      closePopover();
    });
    document.addEventListener('keydown', function (ev) { if (ev.key === 'Escape') closePopover(); });
    wireZoom();

    var resizeTimer = null;
    window.addEventListener('resize', function () {
      if (resizeTimer) cancelAnimationFrame(resizeTimer);
      resizeTimer = requestAnimationFrame(function () {
        if (state.view === 'week' && state.range) {
          var keepW = $('week-scroll').scrollTop;
          renderWeek();
          $('week-scroll').scrollTop = keepW;
          return;
        }
        if (state.view !== 'day') return;
        if (state.day) {
          var keep = $('timeline-scroll').scrollTop;
          renderTimeline(false);
          $('timeline-scroll').scrollTop = keep;
        }
      });
    });

    setInterval(function () {
      if (state.view !== 'day') {
        // 주·월 보기에서 04:00(day_start) 을 넘긴 경우: 투두·메모의 기준 날짜(sideDate=오늘)가 바뀌었으므로
        // 새 오늘 것으로 다시 불러온다(안 그러면 전날 목록이 남고 새 메모가 전날 파일에 저장됨).
        // 팝오버·메모 입력 중이면 하루 보기와 같이 다음 틱으로 미룬다.
        if (state.sideLoaded && sideDate() !== state.sideLoaded) {
          if (state.popover || noteFocused()) return;
          flushNote('today');
          lockNote('today', '불러오는 중…');
          renderNoteLabel();
          loadTodos();
          loadNight();
          loadBudget();
          loadSchedule();
          loadEvents();
          loadNotes(false);
        }
        // 주·월 보기: 오늘이 포함된 기간이면 30초마다 range 재조회 (팝오버·메모 입력 중이면 미룸)
        if (!periodIncludesToday()) return;
        if (state.popover) { state.rangePending = true; return; }
        if (document.activeElement === notes.today.el || document.activeElement === notes.pinned.el) return;
        loadRange();
        return;
      }
      if (!isToday()) {
        // 페이지를 켜 둔 채 04:00(day_start) 을 넘긴 경우: '오늘' 로 보던 화면을 새 오늘로 옮긴다
        // (팝오버가 열려 있거나 메모를 입력 중이면 다음 틱으로 미룸)
        if (state.renderedAsToday && state.date && !state.popover &&
            document.activeElement !== notes.today.el && document.activeElement !== notes.pinned.el) {
          changeDate(todayStr());
        }
        return;
      }
      if (state.popover) { state.dayPending = true; return; }
      updateTonight();   // §22: 16시를 넘기면 페이지를 다시 열지 않아도 블록이 뜬다
      loadDay();
    }, DAY_REFRESH_MS);
    setInterval(loadStatus, STATUS_REFRESH_MS);
    // §27 두 기기에서 쓴다: 창에 돌아오면 바로, 보이지만 안 쓰는 동안(포커스 없음)은 60초마다 옆 판을 다시 받는다.
    // 쓰고 있는 창(포커스 있음)은 주기 갱신을 하지 않는다 — 누르는 도중 다시 그려지지 않게(§19.9 와 같은 이유).
    initZones();
    loadDrawer();      // §35 날짜와 무관 — 시작 때와 refreshSide 때만
    loadDevices();     // §36 이 PC 에서 열었으면 설정 카드, 밖이면 로그아웃
    initOrbitMark();   // §37.9 로고의 위성 = 분침
    initPhone();       // §39.2 폰 화면: 아래 탭 · 한 번에 한 화면
    loadAnki();
    setInterval(function () { if (!document.hidden) loadAnki(); }, ANKI_REFRESH_MS);   // §31 10분마다(서버 캐시가 파일 변경 때만 다시 읽음)
    window.addEventListener('focus', refreshSide);
    document.addEventListener('visibilitychange', function () { if (!document.hidden) refreshSide(); });
    setInterval(function () { if (!document.hidden && !document.hasFocus()) refreshSide(); }, SIDE_REFRESH_MS);
  }

  function init() {
    loadZoomPref(); // 저장된 배율이 있으면 그 배율, 없으면 맞춤 (첫 렌더 전에)
    wire();
    // 콜드 로드 시 Pretendard 가 아직 없으면 measureText 가 대체 글꼴로 재서 라벨이 과하게 잘린다
    // (블록 라벨·밤 밴드 공통). 글꼴이 준비되면 한 번만 다시 그린다.
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () {
        if (state.view === 'day' && state.day) renderTimeline(false);
        else if (state.view === 'week' && state.range) renderWeek();
      }).catch(function () { /* 무시 */ });
    }
    updateViewChrome();
    renderLegend();
    renderStatus();
    lockNote('today', '불러오는 중…');
    lockNote('pinned', '불러오는 중…');
    loadDay(null).then(function () {
      if (!state.date) state.date = todayStr();
      loadTodos();
      loadNight();
      loadBudget();
      loadSchedule();
      loadEvents();
      loadNotes(true);
    });
    loadStatus();
  }

  // ============================================================ 2차: 보기 전환 (하루 / 주 / 월) — DESIGN §11.3
  var VIEW_NEXT = { day: 'week', week: 'month', month: 'day' };
  var TODAY_LABEL = { day: '오늘', week: '이번 주', month: '이번 달' };
  var NAV_TITLE = { day: ['이전 날짜', '다음 날짜'], week: ['이전 주', '다음 주'], month: ['이전 달', '다음 달'] };
  var WEEK_MON = ['월', '화', '수', '목', '금', '토', '일'];
  var COL_GAP = 4;

  // 투두·메모가 기준으로 삼는 날짜: 하루 보기는 표시 중인 날짜, 주·월 보기는 실제 오늘
  function sideDate() { return state.view === 'day' ? state.date : todayStr(); }

  function weekStartOf(str) { var d = parseDate(str); d.setDate(d.getDate() - (d.getDay() + 6) % 7); return dateStr(d); }
  function monthStartOf(str) { var d = parseDate(str); return dateStr(new Date(d.getFullYear(), d.getMonth(), 1)); }
  function monthEndOf(str) { var d = parseDate(str); return dateStr(new Date(d.getFullYear(), d.getMonth() + 1, 0)); }
  function shiftMonth(str, n) { var d = parseDate(str); return dateStr(new Date(d.getFullYear(), d.getMonth() + n, 1)); }
  function periodOf(view, date) {
    if (view === 'week') { var ws = weekStartOf(date); return { start: ws, end: shiftDate(ws, 6) }; }
    if (view === 'month') return { start: monthStartOf(date), end: monthEndOf(date) };
    return { start: date, end: date };
  }
  function periodIncludesToday() {
    if (!state.date) return false;
    var p = periodOf(state.view, state.date), t = todayStr();
    return p.start <= t && t <= p.end;
  }
  function dayWindowOf(str) {
    var h0 = state.config.day_start_hour || 4;
    var d = parseDate(str); d.setHours(h0, 0, 0, 0);
    return { start: d.getTime() / 1000, end: d.getTime() / 1000 + 86400 };
  }
  function fmtShort(sec) {
    var m = mins(sec), hh = Math.floor(m / 60), mm = m % 60;
    return hh > 0 ? hh + 'h ' + mm + 'm' : mm + 'm';
  }
  function mdStr(str) { var d = parseDate(str); return (d.getMonth() + 1) + '/' + d.getDate(); }
  function cfgHour(key, dflt) { var v = state.config[key]; return (v === undefined || v === null) ? dflt : v; }
  function noteFocused() { return document.activeElement === notes.today.el || document.activeElement === notes.pinned.el; }

  // ---- 보기 전환 · 이동
  function setView(view, date) {
    if (!VIEW_NEXT[view]) return;
    var newDate = date || state.date || todayStr();
    if (view === state.view && newDate === state.date) return;
    var prevSide = sideDate();
    closePopover();
    flushNote('today'); // 디바운스 대기 중인 메모를 먼저 저장
    state.view = view;
    state.date = newDate;
    state.rangePending = false;
    state.dayPending = false;
    updateViewChrome();
    if (view === 'day') {
      state.scrolledOnce = false;
      loadDay(newDate);
    } else {
      state.weekScrolledOnce = false;
      renderRangeHeader();
      loadRange();
    }
    if (sideDate() !== prevSide) {
      lockNote('today', '불러오는 중…');
      loadTodos();
      loadNight();
      loadBudget();
      loadSchedule();
      loadEvents();
      loadNotes(false);
    } else renderNoteLabel();
  }

  function navigatePeriod(dir) {
    if (!state.date) return;
    if (state.view === 'day') changeDate(shiftDate(state.date, dir));
    else if (state.view === 'week') setView('week', shiftDate(state.date, 7 * dir));
    else setView('month', shiftMonth(state.date, dir));
  }

  // '오늘' 버튼: 현재 기간에 오늘이 있으면 하루→주→월→하루 순환(항상 오늘 포함), 없으면 현재 보기에서 오늘로만 이동
  function todayClick() {
    var t = todayStr();
    if (periodIncludesToday()) setView(VIEW_NEXT[state.view], t);
    else if (state.view === 'day') changeDate(t);
    else setView(state.view, t);
  }

  function updateViewChrome() {
    var v = state.view;
    Array.prototype.forEach.call(document.querySelectorAll('#view-seg .seg-btn'), function (b) {
      var on = b.getAttribute('data-view') === v;
      b.classList.toggle('active', on);
      b.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    $('btn-today').textContent = TODAY_LABEL[v];
    $('btn-prev').title = NAV_TITLE[v][0];
    $('btn-next').title = NAV_TITLE[v][1];
    $('date-input').disabled = v !== 'day';
    $('timeline-wrap').hidden = v !== 'day';
    $('week-wrap').hidden = v !== 'week';
    $('month-wrap').hidden = v !== 'month';
    // §18: 보기별 레이아웃 규칙이 #main 의 grid-template-columns 를 갈아타므로 현재 보기를 클래스로 남긴다
    var mainEl = $('main');
    mainEl.classList.toggle('view-day', v === 'day');
    renderPhoneHead();   // §39.2 세그먼트·날짜 줄
    mainEl.classList.toggle('view-week', v === 'week');
    mainEl.classList.toggle('view-month', v === 'month');
    setSummaryLabels(v);
    renderNoteLabel();
  }

  function setSummaryLabels(v) {
    var day = v === 'day';
    $('sum-attend-label').textContent = day ? '출근 → 퇴근' : '출근일수';
    $('sum-active-label').textContent = day ? '활동시간' : '총 활동시간';
    $('sum-distraction-label').textContent = '딴짓';
    $('sum-away-label').textContent = day ? '자리비움 / 원격' : '평균 출근 → 평균 퇴근';
    $('sum-focus-label').textContent = day ? '최장 집중' : '가장 길게 일한 날';
    var sub = $('sum-active-sub');
    if (day) { sub.hidden = true; sub.textContent = ''; }
    if (day) {
      // 하루 보기로 돌아오면 기존 renderSummary 가 값을 채운다(하루 경로 그대로)
      if (state.day) { renderSummary(); renderUnknownTop(); }
    }
  }

  function renderNoteLabel() {
    var el = $('note-today-label');
    if (!el) return;
    if (state.view === 'day') el.textContent = '오늘 메모';
    else {
      var t = todayStr(), d = parseDate(t);
      el.textContent = '오늘 메모 (' + (d.getMonth() + 1) + '월 ' + d.getDate() + '일)';
    }
  }

  // ---- 데이터 로더
  var rangeSeq = 0;
  function loadRange() {
    if (state.view === 'day' || !state.date) return Promise.resolve();
    var view = state.view, p = periodOf(view, state.date);
    var seq = ++rangeSeq;
    var path = '/api/range?start=' + encodeURIComponent(p.start) + '&end=' + encodeURIComponent(p.end) + '&spans=' + (view === 'week' ? 1 : 0);
    return api(path).then(function (r) {
      if (seq !== rangeSeq || state.view !== view) return; // 늦게 온 응답(◀ 연타·보기 전환)은 버림
      if (state.popover) { state.rangePending = true; return; }
      state.range = r;
      if (r.categories && r.categories.length) state.categories = themedCats(r.categories);
      if (r.config) {
        state.config = Object.assign({}, state.config, r.config);
        if (!state.configSeen) { state.configSeen = true; loadAnki(); }   // §31.6 설정이 처음 온 뒤에 카드를 켤지 정한다
      }
      if (r.status) { state.status = r.status; renderStatus(); }
      state.rangePending = false;
      renderRangeHeader();
      if (view === 'week') renderWeek(); else renderMonth();
      renderRangeSummary();
      renderRangeUnknownTop();
      renderLegend();
      loadPeriod(p, view, seq);
      return r;
    }).catch(fail('기간 기록 불러오기 실패'));
  }

  // ------------------------------------------------------------ 기간 판 (§23.3)
  function loadPeriod(p, view, seq) {
    return api('/api/period?start=' + encodeURIComponent(p.start) + '&end=' + encodeURIComponent(p.end)).then(function (r) {
      if (seq !== rangeSeq || state.view !== view) return;
      state.period = r;
      if (view === 'week') renderWeek(); else renderMonth();   // §25 밤 일정 밴드·칸 줄
      renderPeriod();
    }).catch(fail('기간 판 불러오기 실패'));
  }
  function nightMark(n) {
    if (!n) return '';
    var s = '밤 ' + (n.kind_name || nightName(n.kind));
    if (n.ask) s += n.checked ? (n.done ? ' 했음' : ' 안 했음') : ' ?';
    return s;
  }
  function renderPeriod() {
    var pr = state.period, ul = $('period-list');
    if (!pr || state.view === 'day') return;
    clear(ul);
    var p = periodOf(state.view, state.date), today = todayStr();
    var byDate = {};
    (pr.days || []).forEach(function (d) { byDate[d.date] = d; });
    var days = rangeDayList(p);
    if (state.view === 'week') {
      $('period-title').textContent = periodIncludesToday() ? '이번 주' : '그 주';
      $('period-sub').textContent = '지출 ' + fmtWon(pr.totals.spend) + ' · 할 일 완료 ' + pr.totals.todos_done;
      days.forEach(function (d) {
        var sm = d.summary || {}, x = byDate[d.date] || {}, future = d.date > today;
        var attend = '—';
        if (sm.first_active !== null && sm.first_active !== undefined) {
          attend = hm(sm.first_active) + ' → ' + (sm.in_progress ? '진행 중' : hm(sm.last_active));
        }
        var l1 = h('div', { class: 'period-l1' }, [
          h('b', { text: WEEK[parseDate(d.date).getDay()] + ' ' + mdStr(d.date) }),
          h('span', { text: attend + ((sm.active_sec || 0) > 0 ? ' · 활동 ' + fmtShort(sm.active_sec) : '') })
        ]);
        var bits = [];
        if (x.night) {
          var nm = nightMark(x.night);
          bits.push(h('span', { class: x.night.ask && x.night.checked ? (x.night.done ? 'ok' : 'no') : '', text: nm }));
        }
        if (x.spend) bits.push(h('span', { text: '지출 ' + fmtWon(x.spend) }));
        if (x.todos_done || x.todos_open) bits.push(h('span', { text: '할 일 ' + x.todos_done + '/' + (x.todos_done + x.todos_open) }));
        (x.events || []).forEach(function (ev) { bits.push(h('span', { class: ev.done ? '' : 'evt', text: (ev.kind === 'deadline' ? '마감 ' : '') + ev.title })); });
        var l2 = h('div', { class: 'period-l2' });
        bits.forEach(function (b, i) { if (i) l2.appendChild(document.createTextNode(' · ')); l2.appendChild(b); });
        ul.appendChild(h('li', { class: 'list-row period-row' + (future ? ' future' : ''),
          title: future ? '' : '누르면 하루 보기',
          onclick: function () { if (!future) setView('day', d.date); } }, [l1, l2]));
      });
    } else {
      $('period-title').textContent = periodIncludesToday() ? '이 달' : '그 달';
      $('period-sub').textContent = '';
      var t = pr.totals || {};
      var rows = [
        ['밤 계획', t.nights + '일' + (t.nights ? ' · 이행 ' + t.nights_done + '/' + t.nights : '')],
        ['운동', t.workouts + '회'],
        ['지출', fmtWon(t.spend)],
        ['할 일 완료', t.todos_done + '개']
      ];
      rows.forEach(function (r) {
        ul.appendChild(h('li', { class: 'list-row period-num' }, [h('span', { class: 'k', text: r[0] }), h('span', { class: 'v', text: r[1] })]));
      });
      var b = pr.budget || { plan: [] };
      if (budgetOn() && b.plan && b.plan.length) {
        ul.appendChild(h('li', { class: 'list-row period-num' }, [
          h('span', { class: 'k', text: '예산 남음' }), h('span', { class: 'v', text: fmtWon(b.total.left) + ' / ' + fmtWon(b.total.amount) })]));
        b.plan.forEach(function (r) {
          ul.appendChild(h('li', { class: 'list-row period-num' }, [
            h('span', { class: 'k', text: '  ' + r.category }),
            h('span', { class: 'v' + (r.left < 0 ? ' over' : ''), text: fmtWon(r.left) + (r.extra ? '' : ' (' + fmtWon(r.amount) + ')') })]));
        });
      }
    }
  }

  function renderRangeHeader() {
    if (!state.date || state.view === 'day') return;
    var p = periodOf(state.view, state.date);
    var label;
    if (state.view === 'week') {
      var a = parseDate(p.start), b = parseDate(p.end);
      label = a.getFullYear() + '년 ' + (a.getMonth() + 1) + '월 ' + a.getDate() + '일 (' + WEEK[a.getDay()] + ') ~ ' +
        (b.getFullYear() !== a.getFullYear() ? b.getFullYear() + '년 ' : '') + (b.getMonth() + 1) + '월 ' + b.getDate() + '일 (' + WEEK[b.getDay()] + ')';
      if (periodIncludesToday()) label += ' · 이번 주';
    } else {
      var m = parseDate(p.start);
      label = m.getFullYear() + '년 ' + (m.getMonth() + 1) + '월';
      if (periodIncludesToday()) label += ' · 이번 달';
    }
    $('date-label').textContent = label;
    $('date-input').value = state.date;
    renderPhoneHead();   // §39.2
  }

  // 기간 응답을 날짜 → day 로. 빠진 날짜(미래·응답 누락)는 빈 값으로 채움
  function rangeDayList(p) {
    var byDate = {};
    ((state.range && state.range.days) || []).forEach(function (d) { byDate[d.date] = d; });
    var out = [], cur = p.start, guard = 0;
    while (cur <= p.end && guard++ < 62) {
      var d = byDate[cur] || { date: cur, window: null, spans: [], summary: {} };
      if (!d.window) d.window = dayWindowOf(cur);
      if (!d.summary) d.summary = {};
      if (!d.spans) d.spans = [];
      out.push(d);
      cur = shiftDate(cur, 1);
    }
    return out;
  }

  // ---- 주 보기: 7열 세로 타임라인
  var wk = { startHour: 0, endHour: 24, days: null, colW: 0, width: 0 };
  function xOfCol(i) { return GUTTER + COL_GAP + i * (wk.colW + COL_GAP); }
  function yW(ts, windowStart) {
    // 하루 보기 yOf 와 같은 계산: window.start 기준 경과 분 → px (열마다 window 가 다름)
    var h0 = state.config.day_start_hour || 4;
    return PAD_TOP + ((ts - windowStart) / 60 - (wk.startHour - h0) * 60) * state.pxPerMin;
  }

  function renderWeek() {
    var svg = $('week-svg'), head = $('week-head'), scroll = $('week-scroll'), emptyEl = $('week-empty');
    clear(svg); clear(head);
    if (!state.range || state.view !== 'week') return;
    var p = periodOf('week', state.date);
    var days = rangeDayList(p);
    wk.days = days;
    var h0 = state.config.day_start_hour || 4;
    var today = todayStr(), now = nowTs();

    // 표시 범위 = 7일 합집합 (기본 06~24)
    var startHour = cfgHour('display_start_hour', 6), endHour = cfgHour('display_end_hour', 24);
    var anySpans = false;
    days.forEach(function (d) {
      var sp = d.spans.filter(function (x) { return x.category !== 'off' && x.end > x.start; });
      if (sp.length) {
        anySpans = true;
        startHour = Math.min(startHour, Math.floor(h0 + (sp[0].start - d.window.start) / 3600 + 1e-9));
        endHour = Math.max(endHour, Math.ceil(h0 + (sp[sp.length - 1].end - d.window.start) / 3600 - 1e-9));
      }
      if (d.date === today && now >= d.window.start && now <= d.window.end) {
        endHour = Math.max(endHour, Math.ceil(h0 + (now - d.window.start) / 3600 - 1e-9));
      }
    });
    var nightsBy = {};
    ((state.period && state.period.days) || []).forEach(function (x) { if (x.night) nightsBy[x.date] = x.night; });
    if (Object.keys(nightsBy).length) {           // §25 밤 일정이 있는 주는 23시까지 보인다
      var he = cfgHour('night_band_end_hour', 23); if (he <= h0) he += 24;
      endHour = Math.max(endHour, he);
    }
    startHour = Math.max(h0, startHour);
    endHour = Math.min(h0 + 24, Math.max(endHour, startHour + 1));
    wk.startHour = startHour; wk.endHour = endHour;

    var width = scroll.clientWidth || $('week-wrap').clientWidth || 800;
    wk.width = width;
    var colW = Math.max(36, (width - GUTTER - COL_GAP * 8) / 7);
    wk.colW = colW;

    // 열 머리 (SVG 바깥, 고정)
    head.appendChild(h('div', { class: 'wk-gutter' }));
    days.forEach(function (d) {
      var sm = d.summary || {};
      var dt = parseDate(d.date);
      var cls = 'wk-col' + (d.date === today ? ' today' : '') + (d.date > today ? ' future' : '');
      // 열 폭이 좁아(1300px 창에서 약 110px) 한 줄에 들어가지 않으므로 출근→퇴근 / 활동시간 두 줄로
      var sub = '—', sub2 = '';
      if (sm.first_active !== null && sm.first_active !== undefined) {
        var arrow = colW < 90 ? '→' : ' → ';
        sub = hm(sm.first_active) + arrow + (sm.in_progress ? '진행 중' : hm(sm.last_active));
        sub2 = fmtShort(sm.active_sec);
      }
      var col = h('div', {
        class: cls, style: 'width:' + colW + 'px;margin-left:' + COL_GAP + 'px',
        title: koreanDate(d.date) + (d.date === today ? ' · 오늘' : '') + ' — 누르면 하루 보기로',
        onclick: function () { setView('day', d.date); }
      }, [
        h('div', { class: 'wk-day', text: WEEK[dt.getDay()] + ' ' + mdStr(d.date) }),
        h('div', { class: 'wk-sub', text: sub }),
        h('div', { class: 'wk-sub wk-sub2', text: sub2 })
      ]);
      head.appendChild(col);
    });

    if (state.zoomFit) state.pxPerMin = fitPxFor(scroll, (endHour - startHour) * 60); // 맞춤 (하루 보기와 같은 배율 규칙)
    var px = state.pxPerMin;
    var height = PAD_TOP + (endHour - startHour) * 60 * px + PAD_BOTTOM;
    svg.setAttribute('width', width);
    svg.setAttribute('height', height);
    svg.style.height = height + 'px';

    // 오늘 열 배경
    var gBg = s('g', { class: 'wk-bg' });
    days.forEach(function (d, i) {
      if (d.date === today) gBg.appendChild(s('rect', { class: 'wk-today-bg', x: xOfCol(i), y: 0, width: colW, height: height }));
    });
    svg.appendChild(gBg);

    // 격자 + 눈금 (공통 시각축, 밀도 규칙은 하루 보기와 동일)
    svg.appendChild(drawHourGrid(startHour, endHour, px, width));

    // spans (하루 보기와 같은 색·패턴·툴팁)
    var gSpans = s('g', { class: 'spans' });
    var showText = colW >= 70;
    days.forEach(function (d, di) {
      var x0 = xOfCol(di);
      d.spans.forEach(function (sp, idx) {
        if (sp.category === 'off' || !(sp.end > sp.start)) return;
        var yA = yW(sp.start, d.window.start), yB = yW(sp.end, d.window.start);
        var hgt = Math.max(1, yB - yA);
        var cat = catOf(sp.category);
        var attrs = {
          class: 'span cat-' + sp.category + (sp.source === 'override' ? ' override' : ''),
          'data-di': di, 'data-idx': idx, x: x0, y: yA, width: colW, height: hgt, fill: cat.color, rx: 2
        };
        if (sp.category === 'paused') {
          attrs.stroke = '#9CA3AF'; attrs['stroke-width'] = 1.5; attrs['stroke-dasharray'] = '4 3';
          attrs.x = x0 + 1; attrs.width = colW - 2;
        } else if (sp.source === 'override') {
          attrs.stroke = '#1f4fd1'; attrs['stroke-width'] = 1; attrs['stroke-opacity'] = 0.6;
        }
        var rect = s('rect', attrs);
        rect.appendChild(s('title', { text: spanTooltip(sp) }));
        gSpans.appendChild(rect);
        if (sp.category === 'distraction' || sp.category === 'away') {
          gSpans.appendChild(s('rect', {
            class: 'span-overlay', x: x0, y: yA, width: colW, height: hgt, rx: 2,
            fill: sp.category === 'distraction' ? 'url(#hatch-white)' : 'url(#hatch-gray)'
          }));
        }
        if (showText && hgt >= 14) {
          var lbl = spanLabel(sp);
          var label = cat.name + (lbl ? ' · ' + lbl : '');
          label = truncateToWidth(label, colW - 10, 11);
          if (label) gSpans.appendChild(s('text', {
            class: 'span-text', x: x0 + 5, y: yA + Math.min(hgt / 2 + 4, 12), fill: textColorFor(cat.color), text: label
          }));
        }
      });
    });
    svg.appendChild(gSpans);

    // §25 밤 일정 밴드 (하루 보기와 같은 점선 괄호, 20~23시)
    var gNight = s('g', { class: 'night-band' });
    days.forEach(function (d, i) {
      var n = nightsBy[d.date];
      if (!n) return;
      var yA = yW(nightShowStartTs(d.window.start), d.window.start), yB = yW(nightShowEndTs(d.window.start), d.window.start);
      if (!(yB > yA)) return;
      var x = xOfCol(i);
      gNight.appendChild(s('rect', { x: x + 0.5, y: yA, width: colW - 1, height: yB - yA, rx: 2,
        fill: 'none', stroke: '#1f4fd1', 'stroke-opacity': 0.45, 'stroke-width': 1, 'stroke-dasharray': '3 3' }));
      var label = truncateToWidth(colW >= 70 ? nightText(n) : (n.kind_name || nightName(n.kind)), colW - 8, 11);
      if (label) gNight.appendChild(s('text', { class: 'night-label', x: x + colW - 4, y: yA + 13, 'text-anchor': 'end', text: label }));
    });
    svg.appendChild(gNight);

    // 오늘 열 현재 시각선
    var todayIdx = -1;
    days.forEach(function (d, i) { if (d.date === today && now >= d.window.start && now <= d.window.end) todayIdx = i; });
    if (todayIdx >= 0) {
      var yn = yW(now, days[todayIdx].window.start);
      svg.appendChild(s('line', { id: 'week-now-line', class: 'now-line', x1: xOfCol(todayIdx) - 2, y1: yn, x2: xOfCol(todayIdx) + colW + 2, y2: yn, stroke: '#1f4fd1', 'stroke-width': 1.5 }));
    }

    emptyEl.hidden = anySpans;

    if (!state.weekScrolledOnce) {
      state.weekScrolledOnce = true;
      var targetY = null;
      if (todayIdx >= 0) targetY = yW(now, days[todayIdx].window.start);
      else {
        var firstY = null;
        days.forEach(function (d) {
          d.spans.forEach(function (sp) {
            if (sp.category === 'off' || !(sp.end > sp.start)) return;
            var yy = yW(sp.start, d.window.start);
            if (firstY === null || yy < firstY) firstY = yy;
          });
        });
        targetY = firstY;
      }
      if (targetY !== null) scroll.scrollTop = Math.max(0, targetY - scroll.clientHeight * 0.55);
    }
  }

  function updateWeekNowLine() {
    var line = $('week-now-line');
    if (!state.range || state.view !== 'week') return;
    var now = nowTs(), today = todayStr();
    var d = null, di = -1;
    (wk.days || []).forEach(function (x, i) { if (x.date === today) { d = x; di = i; } });
    if (!d) { if (periodIncludesToday()) renderWeek(); return; } // 04:00 경계를 넘김
    if (now > d.window.end) return;
    var h0 = state.config.day_start_hour || 4;
    if (!line || h0 + (now - d.window.start) / 3600 > wk.endHour) { renderWeek(); return; }
    var y = yW(now, d.window.start);
    line.setAttribute('y1', y); line.setAttribute('y2', y);
  }

  // ---- 월 보기: 달력 격자
  function renderMonth() {
    var grid = $('month-grid'), wd = $('month-weekdays'), emptyEl = $('month-empty');
    clear(grid); clear(wd);
    if (!state.range || state.view !== 'month') return;
    WEEK_MON.forEach(function (n, i) { wd.appendChild(h('div', { class: 'mwd' + (i === 6 ? ' sun' : ''), text: n })); });
    var p = periodOf('month', state.date);
    var days = rangeDayList(p);
    var today = todayStr();
    var maxActive = 0, anyActive = false;
    days.forEach(function (d) { var a = (d.summary && d.summary.active_sec) || 0; if (a > 0) anyActive = true; maxActive = Math.max(maxActive, a); });
    var activeCats = state.categories.filter(function (c) { return c.kind === 'active'; });
    var monthNights = {}, monthEvents = {};
    ((state.period && state.period.days) || []).forEach(function (x) { if (x.night) monthNights[x.date] = x.night; if (x.events && x.events.length) monthEvents[x.date] = x.events; });

    var lead = (parseDate(p.start).getDay() + 6) % 7;
    for (var i = 0; i < lead; i++) grid.appendChild(h('div', { class: 'mcell pad' }));
    days.forEach(function (d) {
      var sm = d.summary || {};
      var dt = parseDate(d.date);
      var isT = d.date === today, future = d.date > today, sun = dt.getDay() === 0;
      var cls = 'mcell' + (isT ? ' today' : '') + (future ? ' future' : '') + (sun ? ' sun' : '');
      var attend = '—';
      if (sm.first_active !== null && sm.first_active !== undefined) {
        attend = hm(sm.first_active) + ' → ' + (sm.in_progress ? '진행 중' : hm(sm.last_active));
      }
      var active = sm.active_sec || 0;
      var bar = h('div', { class: 'mbar', style: 'width:' + (maxActive > 0 ? Math.round(active / maxActive * 1000) / 10 : 0) + '%' });
      if (active > 0) {
        var by = sm.by_category || {};
        activeCats.forEach(function (c) {
          var sec = by[c.id] || 0;
          if (sec <= 0) return;
          bar.appendChild(h('div', {
            class: 'mseg ' + c.id, title: c.name + ' ' + fmtShort(sec),
            style: 'width:' + (sec / active * 100) + '%;background-color:' + c.color
          }));
        });
      }
      var tip = koreanDate(d.date) + (isT ? ' · 오늘' : '') + '\n출근 → 퇴근: ' + attend + '\n활동 ' + (active > 0 ? fmtHM(active) : '—');
      if (sm.distraction_sec) tip += '\n딴짓 ' + mins(sm.distraction_sec) + '분';
      tip += '\n(누르면 하루 보기)';
      // §26 출퇴근 트랙: 표시 범위(기본 06~24시) 위에 출근~퇴근 막대. 9시·18시 눈금 — 일찍/늦게는 위치로만 보인다(판단 문구 없음)
      var trackKids = [];
      var ax0 = cfgHour('display_start_hour', 6), ax1 = cfgHour('display_end_hour', 24);
      var pct = function (hr) { return Math.max(0, Math.min(100, (hr - ax0) / (ax1 - ax0) * 100)); };
      [9, 18].forEach(function (tk) { if (tk > ax0 && tk < ax1) trackKids.push(h('i', { class: 'mtick', style: 'left:' + pct(tk) + '%' })); });
      if (sm.first_active !== null && sm.first_active !== undefined && d.window) {
        var h0m = state.config.day_start_hour || 4;
        var hin = h0m + (sm.first_active - d.window.start) / 3600;
        var outTs = sm.in_progress ? Math.min(nowTs(), d.window.end) : sm.last_active;
        var hout = h0m + ((outTs || sm.first_active) - d.window.start) / 3600;
        // 한 줄로: 출근~퇴근 자리(연한 회색) 안을 활동 분류 색으로 채운다(비율). 빈 곳 = 자리비움 등
        var spanSec = Math.max(1, (outTs || sm.first_active) - sm.first_active);
        var segs = [];
        var byc = sm.by_category || {};
        activeCats.forEach(function (c) {
          var sec = byc[c.id] || 0;
          if (sec <= 0) return;
          segs.push(h('i', { class: 'mseg ' + c.id, title: c.name + ' ' + fmtShort(sec),
            style: 'width:' + Math.min(100, sec / spanSec * 100) + '%;background-color:' + c.color }));
        });
        trackKids.push(h('span', { class: 'mspan' + (sm.in_progress ? ' live' : ''), style: 'left:' + pct(hin) + '%;width:' + Math.max(1.5, pct(hout) - pct(hin)) + '%' }, segs));
      }
      var track = h('div', { class: 'mtrack', title: '출근 → 퇴근 (' + pad2(ax0) + '~' + pad2(ax1 % 24) + '시 축, 눈금 9시·18시)' }, trackKids);
      var mn = monthNights[d.date];
      if (mn) tip += '\n' + nightText(mn);
      var mev = monthEvents[d.date] || [];
      mev.forEach(function (ev) { tip += '\n' + (ev.kind === 'deadline' ? '마감: ' : '') + ev.title; });
      var cell = h('div', { class: cls, title: tip, onclick: function () { setView('day', d.date); } }, [
        h('div', { class: 'mday', text: String(dt.getDate()) }),
        h('div', { class: 'mattend', text: attend }),
        track,
        mn ? h('div', { class: 'mnight' + (mn.checked ? (mn.done ? ' ok' : ' no') : ''), text: nightText(mn).replace('밤 · ', '밤 ') }) : null,
        mev[0] ? h('div', { class: 'mevt ' + mev[0].kind + (mev[0].done ? ' done' : ''), text: mev[0].title }) : null,
        mev[1] ? h('div', { class: 'mevt ' + mev[1].kind + (mev[1].done ? ' done' : ''), text: mev.length > 2 ? mev[1].title + ' 외 ' + (mev.length - 2) : mev[1].title }) : null,
        h('div', { class: 'mbar-wrap' }, [
          h('div', { class: 'mact' + (active > 0 ? '' : ' none'), text: active > 0 ? fmtShort(active) : '—' })
        ])
      ]);
      grid.appendChild(cell);
    });
    var trail = (7 - (lead + days.length) % 7) % 7;
    for (var j = 0; j < trail; j++) grid.appendChild(h('div', { class: 'mcell pad' }));
    emptyEl.hidden = anyActive;
  }

  // ---- 기간 요약 카드 (주·월)
  function renderRangeSummary() {
    if (state.view === 'day') return;
    var p = periodOf(state.view, state.date);
    var days = rangeDayList(p);
    var h0 = state.config.day_start_hour || 4;
    var n = 0, active = 0, dist = 0, entries = 0, sumIn = 0, nIn = 0, sumOut = 0, nOut = 0, best = null;
    days.forEach(function (d) {
      var sm = d.summary || {};
      var a = sm.active_sec || 0;
      if (sm.first_active === null || sm.first_active === undefined || !(a > 0)) return; // 활동 0 인 날 제외
      n++; active += a; dist += sm.distraction_sec || 0; entries += sm.distraction_entries || 0;
      var ws = d.window.start;
      sumIn += sm.first_active - ws; nIn++;
      if (sm.last_active !== null && sm.last_active !== undefined && !sm.in_progress) { sumOut += sm.last_active - ws; nOut++; }
      if (!best || a > best.sec) best = { date: d.date, sec: a };
    });
    function avgHM(secFromWindow) {
      var t = h0 * 3600 + secFromWindow;
      return pad2(Math.floor(t / 3600) % 24) + ':' + pad2(Math.floor((t % 3600) / 60));
    }
    $('sum-attend').textContent = n + '일';
    $('sum-active').textContent = active > 0 ? fmtHM(active) : '—';
    var sub = $('sum-active-sub');
    sub.textContent = n > 0 ? '일평균 ' + fmtHM(active / n) : '';
    sub.hidden = !(n > 0);
    if (active > 0) {
      $('sum-distraction').textContent = mins(dist) + '분 · ' + Math.round(dist / active * 100) + '%';
      $('sum-distraction-sub').textContent = '진입 ' + entries + '회';
    } else {
      $('sum-distraction').textContent = '—';
      $('sum-distraction-sub').textContent = '';
    }
    $('sum-away').textContent = nIn > 0 ? avgHM(sumIn / nIn) + ' → ' + (nOut > 0 ? avgHM(sumOut / nOut) : '—') : '—';
    $('sum-focus').textContent = best ? mdStr(best.date) + ' · ' + fmtShort(best.sec) : '—';
  }

  // ---- 기간 미분류 Top 5 ((exe, 표시 이름) 합산 — 서버의 하루 unknown_top 과 같은 키. name 이 없으면 (exe,title))
  function renderRangeUnknownTop() {
    if (state.view === 'day') return;
    var p = periodOf(state.view, state.date);
    var acc = {};
    rangeDayList(p).forEach(function (d) {
      ((d.summary && d.summary.unknown_top) || []).forEach(function (it) {
        var k = (it.exe || '') + '|' + (it.name ? 'n:' + it.name : 't:' + (it.title || ''));
        if (!acc[k]) acc[k] = { exe: it.exe, title: it.title, name: it.name, sec: 0, best: 0 };
        acc[k].sec += it.sec || 0;
        // 대표 제목 = 기간 안에서 가장 긴 날의 제목
        if ((it.sec || 0) > acc[k].best) { acc[k].best = it.sec || 0; acc[k].title = it.title; }
      });
    });
    var items = Object.keys(acc).map(function (k) { return acc[k]; }).sort(function (a, b) { return b.sec - a.sec; }).slice(0, 5);
    clear($('unknown-list'));
    renderUnknownItems(items);
  }

  // ---- 서버 재시작 (§11.2 POST /api/restart)
  function restartServer() {
    api('/api/restart', {}).then(function () {
      toast('서버를 다시 시작하는 중… 3초 뒤 새로고침합니다.', 3000);
      setTimeout(function () { location.reload(); }, 3000);
    }).catch(fail('서버 재시작 실패'));
  }

  // ------------------------------------------------------------ 목업 (?mock=1)
  var mockDb = null;
  function mockApi(path, body) {
    var u = path.split('?');
    var p = u[0];
    var q = {};
    (u[1] || '').split('&').forEach(function (kv) { if (!kv) return; var a = kv.split('='); q[decodeURIComponent(a[0])] = decodeURIComponent(a[1] || ''); });
    function mockBudget(date) {
      var bm = date.slice(0, 7);
      var plan = db.budget_plan.filter(function (r) { return r.month === bm; });
      var spentBy = {};
      db.spend.forEach(function (s) { if (s.day.slice(0, 7) === bm) spentBy[s.category || ''] = (spentBy[s.category || ''] || 0) + s.amount; });
      var rows = plan.map(function (r) { var sp = spentBy[r.category] || 0; delete spentBy[r.category]; return { category: r.category, amount: r.amount, spent: sp, left: r.amount - sp }; });
      var other = Object.keys(spentBy).reduce(function (a, k) { return a + spentBy[k]; }, 0);
      if (other) rows.push({ category: '기타', amount: 0, spent: other, left: -other, extra: true });
      var ta = plan.reduce(function (a, r) { return a + r.amount; }, 0), ts = rows.reduce(function (a, r) { return a + r.spent; }, 0);
      var prevM = plan.length ? [] : db.budget_plan.filter(function (r) { return r.month < bm; }).map(function (r) { return { category: r.category, amount: r.amount }; });
      return { month: bm, plan: rows, total: { amount: ta, spent: ts, left: ta - ts },
        day: db.spend.filter(function (s) { return s.day === date; }), prev: prevM };
    }
    if (!mockDb) mockDb = buildMock();
    var db = mockDb;
    var out;
    var delay = 120;
    if (p === '/api/day') {
      var d = q.date || db.today;
      if (d === db.today) out = Object.assign({}, db.day, { status: mockStatus() });
      else out = Object.assign({}, mockDayFor(db, d), { categories: DEFAULT_CATEGORIES, config: db.day.config, status: mockStatus(), overrides: [] });
    } else if (p === '/api/status') out = mockStatus();
    else if (p === '/api/override') { db.overrides.push(body); out = { id: 100 + db.overrides.length }; mockApplyOverride(body); }
    else if (p === '/api/override/delete') { out = { ok: true }; mockDeleteOverride(body.id); }
    else if (p === '/api/rule') { db.rules.push(body); out = { ok: true, rules: db.rules }; }
    else if (p === '/api/rule/delete') out = { ok: true };
    else if (p === '/api/night' && body === undefined) {
      var nd = q.date || db.today;
      var since = shiftDate(nd, -3);
      out = {
        date: nd,
        today: db.night.filter(function (x) { return x.day === nd; })[0] || null,
        pending: db.night.filter(function (x) {
          return x.day < nd && x.day >= since && !x.checked_ts && x.ask;
        }),
        workout: mockWorkout(db, nd),
        kinds: DEFAULT_NIGHT_KINDS
      };
    } else if (p === '/api/night') {
      var kk = DEFAULT_NIGHT_KINDS.filter(function (x) { return x.id === body.kind; })[0] || {};
      var dy = body.day || db.today;
      var altDone = (body.done !== undefined && kk.ask) ? { done: body.done ? 1 : 0, checked_ts: Date.now() / 1000 } : {};
      db.night = db.night.filter(function (x) { return x.day !== dy; });
      var row = { id: ++db.seq, day: dy, kind: body.kind, kind_name: kk.name, ask: !!kk.ask,
                  note: null, created_ts: Date.now() / 1000, done: 0, checked_ts: null };
      Object.assign(row, altDone);
      db.night.push(row);
      if (dy === db.today) db.day.night = Object.assign({}, row, mockNightWindow(db, dy));
      out = { id: row.id };
    } else if (p === '/api/night/confirm') {
      db.night.forEach(function (x) {
        if (x.id !== body.id) return;
        x.done = body.done ? 1 : 0; x.checked_ts = Date.now() / 1000;
      });
      out = { ok: true };
    } else if (p === '/api/night/delete') {
      db.night = db.night.filter(function (x) { return x.id !== body.id; });
      if (db.day.night && db.day.night.id === body.id) db.day.night = null;
      out = { ok: true };
    }
    else if (p === '/api/anki') {
      var ad = [], an = [12, 0, 45, 30, 28, 31, 32];
      for (var ai = 6; ai >= 0; ai--) ad.push({ day: shiftDate(db.today, -ai), n: an[6 - ai] });
      var pq = new URLSearchParams(location.search);   // 목업 분기는 페이지 주소로: ?mock=1&connect=1 / &stale=1
      out = { available: true, mtime: Date.now() / 1000 - 1800, connect: { present: pq.get('connect') === '1', busy: false, error: null },
        data: { today: { reviews: 32, minutes: 7.4, correct: 28, accuracy: 88 },
        due: { review: 14, learn: 3, new_waiting: 120 }, days: ad, streak: 6, retention30: 91,
        decks: [{ name: '노랭이', n: 20 }, { name: '초록이', n: 12 }], last_review: Date.now() / 1000 - 5400,
        last_sync: Date.now() / 1000 - (pq.get('stale') === '1' ? 19 * 3600 : 1800), cards: { total: 3000, mature: 410 } } };
    }
    else if (p === '/api/events' && body === undefined) {
      var ed = q.date || db.today;
      var eitems = db.events.map(function (e) {
        var occ = e.day, dd = occ ? Math.round((parseDate(occ) - parseDate(ed)) / 86400000) : null;
        return Object.assign({ occ: occ, d: dd, kind_name: { deadline: '마감', task: '할 일', event: '일정' }[e.kind] }, e);
      });
      out = { date: ed, items: eitems, claude: true };
    } else if (p === '/api/events') {
      db.events.push({ id: ++db.seq, text: body.text, title: body.text, day: shiftDate(db.today, 5), time: null, kind: 'deadline', repeat: '', source_url: '', confidence: 'confirmed', note: '', status: 'ready', done: 0, skip_until: null, created_ts: Date.now() / 1000, done_ts: null });
      out = { id: db.seq, parsing: false };
    } else if (p === '/api/events/update') {
      db.events.forEach(function (e) { if (e.id === body.id) { if (body.done !== undefined) e.done = body.done ? 1 : 0; if (body.day !== undefined) { e.day = body.day; e.confidence = 'confirmed'; e.status = 'ready'; } if (body.skip_until !== undefined) e.skip_until = body.skip_until; } });
      out = { ok: true };
    } else if (p === '/api/events/delete') { db.events = db.events.filter(function (e) { return e.id !== body.id; }); out = { ok: true }; }
    else if (p === '/api/events/retry') { out = { ok: true }; }
    else if (p === '/api/schedule' && body === undefined) {
      var sd = q.date || db.today;
      var raw = [
        { start: shiftDate(sd, -3), end: shiftDate(sd, 4), title: '2026학년도 2학기 수강신청 정정', gubun: '9', holiday: false },
        { start: shiftDate(sd, 2), end: shiftDate(sd, 2), title: '추석', gubun: '9', holiday: true },
        { start: shiftDate(sd, 12), end: shiftDate(sd, 16), title: '학위청구논문 및 개별연구과제 심사 신청 및 심사료 납부', gubun: '2', holiday: false },
        { start: shiftDate(sd, 40), end: shiftDate(sd, 40), title: '(심사용)학위청구논문 보고서 제출', gubun: '2', holiday: false },
        { start: shiftDate(sd, 47), end: shiftDate(sd, 79), title: '학위청구 논문 및 개별연구과제 심사', gubun: '2', holiday: false },
        { start: shiftDate(sd, 61), end: shiftDate(sd, 103), title: '2026학년도 2학기 강의평가(예정)', gubun: '2', holiday: false },
        { start: shiftDate(sd, 82), end: shiftDate(sd, 82), title: '2026학년도 2학기 종강', gubun: '9', holiday: false }
      ];
      var sitems = raw.map(function (x) {
        var ds = Math.round((parseDate(x.start) - parseDate(sd)) / 86400000), de = Math.round((parseDate(x.end) - parseDate(sd)) / 86400000);
        return Object.assign({ gubun_name: x.gubun === '2' ? '대학원' : '공통', d: ds, d_end: de, ongoing: ds <= 0 && de >= 0 }, x);
      });
      out = { date: sd, items: sitems, fetched: Date.now() / 1000 - 3600, error: null, source: '' };
    }
    else if (p === '/api/period' && body === undefined) {
      var pdays = [], ptot = { spend: 0, nights: 0, nights_done: 0, workouts: 0, todos_done: 0 };
      for (var pc = q.start, pg = 0; pc <= q.end && pg++ < 62; pc = shiftDate(pc, 1)) {
        var nrow = db.night.filter(function (x) { return x.day === pc; })[0] || null;
        var pn = nrow ? { kind: nrow.kind, kind_name: nrow.kind_name, ask: !!nrow.ask, done: nrow.done ? 1 : 0, checked: nrow.checked_ts != null } : null;
        var psp = db.spend.filter(function (s) { return s.day === pc; }).reduce(function (a, s) { return a + s.amount; }, 0);
        var ptd = db.todos.filter(function (t) { return t.day === pc; });
        var pdone = ptd.filter(function (t) { return t.done; }).length;
        pdays.push({ date: pc, night: pn, spend: psp, todos_done: pdone, todos_open: ptd.length - pdone, events: db.events.filter(function (e) { return e.day === pc; }).map(function (e) { return { id: e.id, title: e.title, kind: e.kind, done: e.done }; }) });
        ptot.spend += psp; ptot.todos_done += pdone;
        if (pn) { ptot.nights++; if (pn.done) { ptot.nights_done++; if (pn.kind === 'workout') ptot.workouts++; } }
      }
      var pb = mockBudget(q.start);
      out = { start: q.start, end: q.end, days: pdays, totals: ptot, budget: { month: pb.month, plan: pb.plan, total: pb.total } };
    }
    else if (p === '/api/budget' && body === undefined) {
      out = mockBudget(q.date || db.today);
    } else if (p === '/api/budget/plan') {
      db.budget_plan = db.budget_plan.filter(function (r) { return r.month !== body.month; })
        .concat(body.items.map(function (it) { return { month: body.month, category: it.category, amount: it.amount }; }));
      out = { ok: true, n: body.items.length };
    } else if (p === '/api/spend') {
      var mm = String(body.text || '').match(/(\d[\d,]*(?:\.\d+)?)\s*(만|천|원)?/);
      var amt = mm ? Math.round(parseFloat(mm[1].replace(/,/g, '')) * (mm[2] === '만' ? 10000 : mm[2] === '천' ? 1000 : 1)) : body.amount;
      var rest = mm ? String(body.text).replace(mm[0], '').trim() : (body.note || '');
      var ns = { id: ++db.seq, day: body.day || db.today, amount: amt, category: body.category || '', note: rest, created_ts: Date.now() / 1000 };
      db.spend.push(ns); out = { id: ns.id };
    } else if (p === '/api/spend/delete') { db.spend = db.spend.filter(function (s) { return s.id !== body.id; }); out = { ok: true }; }
    else if (p === '/api/spend/update') { db.spend.forEach(function (s) { if (s.id === body.id) { if (body.amount !== undefined) s.amount = body.amount; if (body.note !== undefined) s.note = body.note; if (body.category !== undefined) s.category = body.category; } }); out = { ok: true }; }
    else if (p === '/api/todos' && body === undefined) {
      var dd = q.date || db.today;
      out = {
        today: db.todos.filter(function (t) { return t.day === dd; }),
        carried: db.todos.filter(function (t) { return t.day < dd && !t.done; })
      };
    } else if (p === '/api/todos') {
      var nt = { id: ++db.seq, text: body.text, day: body.day || db.today, done: 0, doing: 0, created_ts: Date.now() / 1000, done_ts: null };
      db.todos.push(nt); out = { id: nt.id };
    } else if (p === '/api/todos/update') {
      db.todos.forEach(function (t) {
        if (t.id !== body.id) return;
        if (body.done !== undefined) { t.done = body.done ? 1 : 0; t.done_ts = body.done ? Date.now() / 1000 : null; if (t.done) t.doing = 0; }
        if (body.doing !== undefined) t.doing = (body.doing && !t.done) ? 1 : 0;
        if (body.text !== undefined) t.text = body.text;
        if (body.day !== undefined) t.day = body.day;
      });
      out = { ok: true };
    } else if (p === '/api/todos/delete') { db.todos = db.todos.filter(function (t) { return t.id !== body.id; }); out = { ok: true }; }
    else if (p === '/api/devices' && body === undefined) {
      // §36 목업: ?remote=1 이면 밖에서 연 것처럼(카드 숨김·로그아웃 보임)
      if (!db.devices) db.devices = { on: false, has_password: false, session_days: 60, sessions: [],
        tailscale: { found: true, running: true, state: 'Running', name: 'pc', dns: 'pc.example.ts.net', serving: false, urls: [] } };
      out = Object.assign({}, db.devices, { remote: /[?&]remote=1/.test(location.search) });
    } else if (p === '/api/devices') {
      var dv = db.devices, rr = { ok: true };
      if (body.op === 'password') { if (String(body.password || '').length < 8) rr = { error: '비밀번호는 8자 이상이어야 합니다' }; else { dv.has_password = true; dv.sessions = []; } }
      else if (body.op === 'on') { if (body.on && !dv.has_password) rr = { error: '먼저 비밀번호를 정해 주세요' }; else dv.on = !!body.on; }
      else if (body.op === 'logout_all') dv.sessions = [];
      else if (body.op === 'serve') { dv.tailscale.serving = true; dv.tailscale.urls = ['https://pc.example.ts.net:8771']; }
      out = Object.assign({}, dv, { remote: false, result: rr });
    } else if (p === '/api/logout') out = { ok: true };
    else if (p === '/api/drawer' && body === undefined) {
      // §35 목업: 자리 표시만 — 실제 번호·사인은 어디에도 적지 않는다
      if (!db.drawer) db.drawer = [
        { id: 9001, kind: 'text', label: '학교 사업자번호', value: '000-00-00000 (자리 표시)', mime: null, size: null, url: null, is_image: false },
        { id: 9002, kind: 'text', label: '학교 주소', value: '서울특별시 마포구 와우산로 94 홍익대학교 (자리 표시)', mime: null, size: null, url: null, is_image: false },
        { id: 9003, kind: 'file', label: '사인', value: 'sign.svg', mime: 'image/svg+xml', size: 300, is_image: true,
          url: 'data:image/svg+xml;utf8,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40"><path d="M5 30 C 20 5, 35 5, 45 25 S 70 35, 80 15 S 100 5, 115 30" fill="none" stroke="#1c1c1a" stroke-width="2"/></svg>') }
      ];
      out = { items: db.drawer.slice() };
    } else if (p === '/api/drawer') {
      var isF = body.data !== undefined;
      var nd = { id: ++db.seq, kind: isF ? 'file' : 'text', mime: null, size: null, url: null, is_image: false,
        label: body.label || (isF ? String(body.name || '파일').replace(/\.[^.]+$/, '') : String(body.value || '').slice(0, 20)),
        value: isF ? (body.name || '') : (body.value || '') };
      if (!db.drawer) db.drawer = [];
      db.drawer.push(nd); out = { id: nd.id };
    } else if (p === '/api/drawer/update') { (db.drawer || []).forEach(function (d) { if (d.id === body.id) { if (body.label !== undefined) d.label = body.label; if (body.value !== undefined) d.value = body.value; } }); out = { ok: true }; }
    else if (p === '/api/drawer/delete') { db.drawer = (db.drawer || []).filter(function (d) { return d.id !== body.id; }); out = { ok: true }; }
    else if (p === '/api/drawer/reveal') out = { ok: true };
    else if (p === '/api/note' && body === undefined) out = { name: q.name, text: db.notes[q.name] || '', ver: 'm' + (db.notes[q.name] || '').length };
    else if (p === '/api/note') { db.notes[body.name] = body.text; out = { ok: true, ver: 'm' + String(body.text || '').length }; }
    else if (p === '/api/pause') {
      var now = Date.now() / 1000;
      if (body.until === 'today') db.pausedUntil = db.day.window.end;
      else if (body.minutes > 0) db.pausedUntil = now + body.minutes * 60;
      else db.pausedUntil = null;
      out = { paused_until: db.pausedUntil };
    } else if (p === '/api/config') out = db.day.config;
    else if (p === '/api/range') {
      var days = [], cur = q.start, guard = 0, withSpans = q.spans === '1';
      if (!cur || !q.end || cur > q.end) out = { error: 'start/end 가 필요합니다' };
      else {
        while (cur <= q.end && guard++ < 62) {
          var md = mockDayFor(db, cur);
          var item = { date: md.date, window: md.window, summary: md.summary };
          if (withSpans) item.spans = md.spans;
          days.push(item);
          cur = shiftDate(cur, 1);
        }
        out = { start: q.start, end: q.end, days: days, categories: DEFAULT_CATEGORIES, config: db.day.config, status: mockStatus() };
      }
    } else if (p === '/api/restart') out = { ok: true };
    else out = { error: '목업에 없는 경로: ' + p };
    return new Promise(function (res, rej) {
      setTimeout(function () {
        if (out && out.error) rej(new Error(out.error));
        else res(JSON.parse(JSON.stringify(out)));
      }, delay);
    });
  }
  function mockWindow(d) {
    var dt = parseDate(d); dt.setHours(4, 0, 0, 0);
    return { start: dt.getTime() / 1000, end: dt.getTime() / 1000 + 86400 };
  }
  function mockStatus() {
    var now = Date.now() / 1000;
    return { now: now, alive: true, last_seen: now - 7, seconds_since: 7, paused_until: mockDb.pausedUntil,
      current: { exe: 'chrome.exe', title: 'Orbit · 출근기록', category: 'neutral' } };
  }
  function mockNightWindow(db, dy) {
    var base = parseDate(dy).getTime() / 1000;
    return { start: base + 20 * 3600, end: base + 28 * 3600 };
  }

  function mockWorkout(db, nd) {
    var done = db.night.filter(function (x) { return x.kind === 'workout' && x.done && x.day <= nd; })
                       .map(function (x) { return x.day; }).sort();
    var last = done.length ? done[done.length - 1] : null;
    if (!last) return { last: null, gap: null, due: true, interval_days: 2 };
    var gap = Math.round((parseDate(nd) - parseDate(last)) / 86400000);
    return { last: last, gap: gap, due: gap >= 2, interval_days: 2 };
  }

  function buildMock() {
    var today = dateStr(new Date(Date.now() - 4 * 3600 * 1000));
    var win = mockWindow(today);
    var midnight = parseDate(today).getTime() / 1000;
    var T = function (hh, mm) { return midnight + hh * 3600 + mm * 60; };
    var now = Date.now() / 1000;
    var endAt = now > T(14, 30) ? now : T(14, 30);
    var raw = [
      [4, 0, 6, 50, 'absent', null, null, []],
      [6, 50, 7, 5, 'unknown', 'SomeTool.exe', '알 수 없는 프로그램 창', [{ exe: 'SomeTool.exe', title: '알 수 없는 프로그램 창', sec: 900 }]],
      [7, 5, 8, 30, 'focus', 'Code.exe', 'engine.py - MAENG_worklog - Visual Studio Code', [
        { exe: 'Code.exe', title: 'engine.py - MAENG_worklog - Visual Studio Code', sec: 3900 },
        { exe: 'chrome.exe', title: 'sqlite3 — Python 3.8 documentation - Chrome', sec: 900 },
        { exe: 'WindowsTerminal.exe', title: 'python', sec: 300 }]],
      [8, 30, 8, 45, 'distraction', 'chrome.exe', '(3) 재밌는 영상 모음 - YouTube - Chrome', [{ exe: 'chrome.exe', title: '(3) 재밌는 영상 모음 - YouTube - Chrome', sec: 900 }]],
      [8, 45, 9, 30, 'focus', 'Code.exe', 'server.py - MAENG_worklog - Visual Studio Code', [{ exe: 'Code.exe', title: 'server.py - MAENG_worklog - Visual Studio Code', sec: 2700 }]],
      [9, 30, 10, 0, 'away', null, null, []],
      [10, 0, 10, 20, 'paused', null, null, []],
      [10, 20, 10, 35, 'off', null, null, []],
      [10, 35, 12, 0, 'experiment', 'LabVIEW.exe', '공구마모 측정 v3.vi - LabVIEW', [{ exe: 'LabVIEW.exe', title: '공구마모 측정 v3.vi - LabVIEW', sec: 5100 }]],
      [12, 0, 12, 40, 'break', 'KakaoTalk.exe', '[카카오톡]', [{ exe: 'KakaoTalk.exe', title: '[카카오톡]', sec: 2400 }]],
      [12, 40, 13, 0, 'remote', null, null, []],
      [13, 0, 13, 30, 'meeting', 'Zoom.exe', '주간 랩미팅 - Zoom', [{ exe: 'Zoom.exe', title: '주간 랩미팅 - Zoom', sec: 1800 }], 'override', 7],
      [13, 30, 13, 45, 'neutral', 'explorer.exe', 'MAENG_worklog', [{ exe: 'explorer.exe', title: 'MAENG_worklog', sec: 900 }]],
      [13, 45, 14, 20, 'unknown', 'Obsidian.exe', '연구노트 - Obsidian v1.5', [{ exe: 'Obsidian.exe', title: '연구노트 - Obsidian v1.5', sec: 2100 }]],
      [14, 20, 14, 23, 'distraction', 'msedge.exe', '뉴스 - Personal - Microsoft Edge', [{ exe: 'msedge.exe', title: '뉴스 - Personal - Microsoft Edge', sec: 180 }]]
    ];
    var spans = raw.map(function (r) {
      return { start: T(r[0], r[1]), end: T(r[2], r[3]), category: r[4], exe: r[5], title: r[6],
        source: r[8] || 'auto', override_id: r[9] || null, apps: r[7] };
    });
    spans.push({ start: T(14, 23), end: endAt, category: 'focus', exe: 'Code.exe', title: 'app.js - MAENG_worklog - Visual Studio Code',
      source: 'auto', override_id: null, apps: [{ exe: 'Code.exe', title: 'app.js - MAENG_worklog - Visual Studio Code', sec: endAt - T(14, 23) }] });
    var db = {
      today: today, seq: 10, overrides: [], rules: [], pausedUntil: null, past: {},
      todos: [
        { id: 1, text: 'engine 테스트 보강', day: today, done: 0, created_ts: now - 3600, done_ts: null },
        { id: 2, text: '초점 맵 결과 정리', day: today, done: 1, created_ts: now - 7200, done_ts: now - 600 },
        { id: 3, text: '논문 그림 3 수정', day: shiftDate(today, -1), done: 0, created_ts: now - 90000, done_ts: null },
        { id: 4, text: '장비 예약', day: shiftDate(today, -3), done: 0, created_ts: now - 300000, done_ts: null }
      ],
      notes: { pinned: '고정 메모 예시\n- 실험실 PC 데이터는 G: 드라이브' },
      budget_plan: [
        { month: today.slice(0, 7), category: '식비', amount: 300000 },
        { month: today.slice(0, 7), category: '교통', amount: 60000 },
        { month: today.slice(0, 7), category: '책·학회', amount: 150000 },
        { month: today.slice(0, 7), category: '여가', amount: 390000 }
      ],
      events: [
        { id: 41, text: '정밀공학회 구두발표 초록 내야 해', title: '정밀공학회 추계학술대회 초록 제출', day: shiftDate(today, 1), time: null, kind: 'deadline', repeat: '', source_url: 'https://www.kspe.or.kr/', confidence: 'confirmed', note: '학회 공지: 초록 접수 마감', status: 'ready', done: 0, skip_until: null, created_ts: now - 86400, done_ts: null },
        { id: 42, text: '24일 열전달 숙제', title: '열전달 숙제', day: shiftDate(today, 6), time: null, kind: 'task', repeat: '', source_url: '', confidence: 'confirmed', note: '', status: 'ready', done: 0, skip_until: null, created_ts: now - 3600, done_ts: null },
        { id: 43, text: '매주 목 교수님 수업 보조', title: '교수님 수업 보조', day: shiftDate(today, 3), time: '13:00', kind: 'event', repeat: 'weekly', source_url: '', confidence: 'confirmed', note: '', status: 'ready', done: 0, skip_until: null, created_ts: now - 3600, done_ts: null },
        { id: 44, text: '기계학회 포스터 마감', title: '대한기계학회 포스터 초록', day: null, time: null, kind: 'deadline', repeat: '', source_url: '', confidence: 'unknown', note: '공식 페이지에서 날짜를 확인하지 못함', status: 'ready', done: 0, skip_until: null, created_ts: now - 600, done_ts: null }
      ],
      spend: [
        { id: 31, day: today, amount: 9000, category: '식비', note: '점심', created_ts: now - 5000 },
        { id: 32, day: today, amount: 4500, category: '식비', note: '커피', created_ts: now - 3000 },
        { id: 33, day: shiftDate(today, -1), amount: 62000, category: '책·학회', note: '교재', created_ts: now - 90000 },
        { id: 34, day: shiftDate(today, -2), amount: 15000, category: '', note: '택배', created_ts: now - 170000 }
      ],
      night: [
        { id: 20, day: today, kind: 'english', kind_name: '영어공부', ask: true,
          note: null, created_ts: now - 3600, done: 0, checked_ts: null },
        { id: 21, day: shiftDate(today, -1), kind: 'workout', kind_name: '운동', ask: true,
          note: null, created_ts: now - 90000, done: 0, checked_ts: null }
      ]
    };
    db.day = { date: today, window: win, spans: spans, summary: {}, categories: DEFAULT_CATEGORIES,
      config: { display_start_hour: 6, display_end_hour: 24, day_start_hour: 4, night_start_hour: 20, night_prompt_hour: 16, budget_card: true, anki_card: true }, overrides: [] };
    db.day.night = Object.assign({}, db.night[0], mockNightWindow(db, today));
    mockRecalc(db);
    return db;
  }
  function mockRecalc(db) {
    db.day.summary = mockSummary(db.day.spans, true);
  }
  // 지난 날짜 목업: 날짜 문자열로 시드한 의사난수로 결정적으로 생성 (일요일은 기록 없음)
  function mockDayFor(db, d) {
    if (d === db.today) return db.day;
    var win = mockWindow(d);
    if (d > db.today) return { date: d, window: win, spans: [], summary: mockSummary([], false) };
    if (db.past[d]) return db.past[d];
    var seed = 0;
    for (var i = 0; i < d.length; i++) seed = (seed * 31 + d.charCodeAt(i)) >>> 0;
    var rnd = function () { seed = (seed * 1664525 + 1013904223) >>> 0; return seed / 4294967296; };
    var dow = parseDate(d).getDay();
    var spans = [];
    var midnight = parseDate(d).getTime() / 1000;
    var T = function (h, m) { return midnight + h * 3600 + (m || 0) * 60; };
    if (dow !== 0 && !(dow === 6 && rnd() < 0.5)) {
      var arrive = T(8, 20) + Math.floor(rnd() * 100) * 60;
      var leave = T(17, 30) + Math.floor(rnd() * 150) * 60;
      spans.push({ start: win.start, end: arrive, category: 'absent', exe: null, title: null, source: 'auto', override_id: null, apps: [] });
      var pool = [
        ['focus', 'Code.exe', 'engine.py - MAENG_worklog - Visual Studio Code', 5],
        ['focus', 'WINWORD.EXE', '논문 초안 v4.docx - Word', 3],
        ['experiment', 'LabVIEW.exe', '공구마모 측정 v3.vi - LabVIEW', 3],
        ['distraction', 'chrome.exe', '(2) 재밌는 영상 - YouTube - Chrome', 2],
        ['neutral', 'explorer.exe', 'MAENG_worklog', 1],
        ['unknown', 'Obsidian.exe', '연구노트 - Obsidian v1.5', 2],
        ['unknown', 'SomeTool.exe', '알 수 없는 프로그램 창', 1],
        ['break', 'KakaoTalk.exe', '[카카오톡]', 1],
        ['meeting', 'Zoom.exe', '주간 랩미팅 - Zoom', 1]
      ];
      var weightSum = pool.reduce(function (a, r) { return a + r[3]; }, 0);
      var t = arrive;
      while (t < leave) {
        var len = (15 + Math.floor(rnd() * 75)) * 60;
        var end = Math.min(leave, t + len);
        if (t < T(12, 0) && end > T(12, 0) && rnd() < 0.7) {
          if (T(12, 0) > t) spans.push(mockPick(pool, weightSum, rnd, t, T(12, 0)));
          var lunchEnd = Math.min(leave, T(12, 50));
          spans.push({ start: T(12, 0), end: lunchEnd, category: 'away', exe: null, title: null, source: 'auto', override_id: null, apps: [] });
          t = lunchEnd;
          continue;
        }
        spans.push(mockPick(pool, weightSum, rnd, t, end));
        t = end;
      }
      spans.push({ start: leave, end: win.end, category: 'absent', exe: null, title: null, source: 'auto', override_id: null, apps: [] });
    }
    var day = { date: d, window: win, spans: spans, summary: mockSummary(spans, false) };
    db.past[d] = day;
    return day;
  }
  function mockPick(pool, weightSum, rnd, start, end) {
    var r = rnd() * weightSum, pick = pool[0];
    for (var i = 0; i < pool.length; i++) { r -= pool[i][3]; if (r <= 0) { pick = pool[i]; break; } }
    return { start: start, end: end, category: pick[0], exe: pick[1], title: pick[2], source: 'auto', override_id: null,
      apps: [{ exe: pick[1], title: pick[2], sec: end - start }] };
  }
  function mockSummary(spans, inProgress) {
    var by = {}, active = 0, first = null, last = null, entries = 0, prevActive = null, longest = 0, run = 0, runEnd = null;
    var sys = { away: 0, absent: 0, remote: 0, paused: 0, off: 0 };
    var unk = {};
    spans.forEach(function (sp) {
      var len = sp.end - sp.start;
      var c = catOf(sp.category);
      by[sp.category] = (by[sp.category] || 0) + len;
      if (c.kind === 'active') {
        active += len;
        if (first === null) first = sp.start;
        last = sp.end;
        if (sp.category === 'distraction' && prevActive !== 'distraction') entries++;
        prevActive = sp.category;
        if (sp.category === 'focus' || sp.category === 'experiment') {
          run = (runEnd === sp.start) ? run + len : len; runEnd = sp.end; longest = Math.max(longest, run);
        } else { run = 0; runEnd = null; }
        if (sp.category === 'unknown') (sp.apps || []).forEach(function (a) {
          var k = a.exe + '|' + a.title; unk[k] = unk[k] || { exe: a.exe, title: a.title, name: a.name, sec: 0 }; unk[k].sec += a.sec;
        });
      } else sys[sp.category] = (sys[sp.category] || 0) + len;
    });
    var top = Object.keys(unk).map(function (k) { return unk[k]; }).sort(function (a, b) { return b.sec - a.sec; }).slice(0, 5);
    return {
      first_active: first, last_active: last, attend_sec: first !== null ? last - first : 0, active_sec: active, by_category: by,
      distraction_sec: by.distraction || 0, distraction_ratio: active ? (by.distraction || 0) / active : 0, distraction_entries: entries,
      away_sec: sys.away, absent_sec: sys.absent, remote_sec: sys.remote, paused_sec: sys.paused, off_sec: sys.off, focus_longest_sec: longest,
      unknown_top: top, in_progress: !!inProgress
    };
  }
  // 목업 override: 오늘(db.day) 과 이미 만들어진 지난 날짜 모두에 적용
  function mockAllDays(db) {
    var list = [db.day];
    Object.keys(db.past).forEach(function (k) { list.push(db.past[k]); });
    return list;
  }
  function mockApplyOverride(o) {
    var db = mockDb;
    var id = 100 + db.overrides.length;
    mockAllDays(db).forEach(function (day) {
      var res = [], touched = false;
      day.spans.forEach(function (sp) {
        if (sp.category === 'off' || sp.end <= o.start || sp.start >= o.end) { res.push(sp); return; }
        touched = true;
        if (sp.start < o.start) res.push(Object.assign({}, sp, { end: o.start }));
        res.push(Object.assign({}, sp, { start: Math.max(sp.start, o.start), end: Math.min(sp.end, o.end), category: o.label, source: 'override', override_id: id }));
        if (sp.end > o.end) res.push(Object.assign({}, sp, { start: o.end }));
      });
      if (!touched) return;
      day.spans = res;
      day.summary = mockSummary(day.spans, day === db.day);
    });
  }
  function mockDeleteOverride(id) {
    var db = mockDb;
    mockAllDays(db).forEach(function (day) {
      var touched = false;
      day.spans.forEach(function (sp) { if (sp.override_id === id) { touched = true; sp.source = 'auto'; sp.override_id = null; sp.category = sp.exe ? 'unknown' : 'away'; } });
      if (touched) day.summary = mockSummary(day.spans, day === db.day);
    });
  }

  // ------------------------------------------------------------ 시작
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

  // 디버그용 노출
  // ============================================================ 시간축 확대·축소 — DESIGN §14
  // 배율 state.pxPerMin 은 하루·주 보기 공용. zoomFit=true 면 renderTimeline/renderWeek 가 매번 fitPxFor 로 다시 계산한다.
  function clampPx(v) { return Math.min(PX_MAX, Math.max(PX_MIN, v)); }
  function tickStep(px) { return px >= 0.9 ? 1 : (px >= 0.45 ? 2 : 3); } // 라벨 간격(시간)

  // 시각 눈금·격자 (하루·주 보기 공통). step 배수 시각 + 표시 범위의 양끝(startHour/endHour)은 항상 선을 긋고,
  // 양끝 라벨은 이웃 라벨과 겹치면(간격 < LABEL_MIN_GAP px) 생략한다. step===1 이면 30분 점선.
  var LABEL_MIN_GAP = 14;
  function drawHourGrid(startHour, endHour, px, width) {
    var g = s('g', { class: 'grid' });
    var step = tickStep(px);
    var isTick = function (hh) { return hh % step === 0; };
    var yAt = function (hh) { return PAD_TOP + (hh - startHour) * 60 * px; };
    var lastLabelY = -Infinity;
    for (var hh = startHour; hh <= endHour; hh++) {
      var edge = hh === startHour || hh === endHour;
      if (!isTick(hh) && !edge) continue;
      var y = yAt(hh);
      g.appendChild(s('line', { class: 'grid-line' + ((hh % 24) % 6 === 0 ? ' major' : ''), x1: GUTTER, y1: y, x2: width, y2: y }));
      var label = true;
      if (!isTick(hh)) {
        if (hh === startHour) { // 다음 정규 라벨과 겹치면 시작 라벨 생략
          var next = hh + 1; while (next <= endHour && !isTick(next)) next++;
          if (next <= endHour && yAt(next) - y < LABEL_MIN_GAP) label = false;
        }
        if (hh === endHour && y - lastLabelY < LABEL_MIN_GAP) label = false; // 이전 라벨과 겹치면 끝 라벨 생략
      }
      if (label) {
        g.appendChild(s('text', { class: 'hour-label', x: GUTTER - 6, y: y + 4, 'text-anchor': 'end', text: pad2(hh % 24) + ':00' }));
        lastLabelY = y;
      }
      if (step === 1 && hh < endHour) {
        var y30 = y + 30 * px;
        g.appendChild(s('line', { class: 'grid-line', x1: GUTTER, y1: y30, x2: width, y2: y30, 'stroke-dasharray': '2 4' }));
      }
    }
    return g;
  }

  // 맞춤: (스크롤 컨테이너 clientHeight − 상하 여백) / 표시 분수 → 스크롤바 없이 한 화면에
  function fitPxFor(container, minutes) {
    var ch = container && container.clientHeight ? container.clientHeight : 600;
    var avail = ch - PAD_TOP - PAD_BOTTOM - 1; // −1: 반올림으로 1px 넘쳐 스크롤바가 생기는 것 방지
    return clampPx(avail / Math.max(60, minutes || 0));
  }

  function loadZoomPref() {
    try {
      var v = parseFloat(localStorage.getItem(ZOOM_KEY));
      if (isFinite(v) && v > 0) { state.pxPerMin = clampPx(v); state.zoomFit = false; return; }
    } catch (e) { /* 저장소 사용 불가 → 맞춤 */ }
    state.zoomFit = true;
  }
  function saveZoomPref() { try { localStorage.setItem(ZOOM_KEY, String(state.pxPerMin)); } catch (e) { /* 무시 */ } }
  function clearZoomPref() { try { localStorage.removeItem(ZOOM_KEY); } catch (e) { /* 무시 */ } }

  function zoomContainer() {
    if (state.view === 'day') return $('timeline-scroll');
    if (state.view === 'week') return $('week-scroll');
    return null;
  }
  function rerenderZoomView() {
    if (state.view === 'day') { if (state.day) renderTimeline(false); }
    else if (state.view === 'week') { if (state.range) renderWeek(); }
  }

  // 배율 변경. anchorY = 컨테이너의 보이는 영역 기준 y(px). 그 위치의 시각이 변경 전후 같도록 scrollTop 을 맞춘다.
  function setZoom(newPx, anchorY) {
    var c = zoomContainer();
    if (!c) return; // 월 보기: 무관
    if (state.popover) closePopover(); // SVG 를 통째로 다시 그리므로 팝오버는 닫는다 (setView 와 같은 규칙)
    newPx = clampPx(newPx);
    var oldPx = state.pxPerMin || 1;
    if (anchorY === undefined || anchorY === null) anchorY = c.clientHeight / 2;
    var minAtAnchor = (c.scrollTop + anchorY - PAD_TOP) / oldPx;
    state.pxPerMin = newPx;
    state.zoomFit = false;
    saveZoomPref();
    rerenderZoomView();
    c.scrollTop = Math.max(0, PAD_TOP + minAtAnchor * newPx - anchorY);
  }

  // 맞춤: 저장값 삭제 + fit 재계산 (렌더 안에서). 한 화면에 들어오면 스크롤 0,
  // PX_MIN 클램프로 넘치면 최초 스크롤 규칙(현재 시각 / 첫 기록)을 다시 적용한다.
  function zoomFit() {
    if (state.popover) closePopover();
    clearZoomPref();
    state.zoomFit = true;
    if (state.view === 'day') state.scrolledOnce = false; else state.weekScrolledOnce = false;
    rerenderZoomView();
    var c = zoomContainer();
    if (c && c.scrollHeight <= c.clientHeight + 2) c.scrollTop = 0;
  }

  // Ctrl+휠(트랙패드 핀치 포함)만 처리. 커서 아래 시각 고정. Ctrl 없는 휠은 평소대로 스크롤.
  // 배율 변화는 deltaY 크기에 비례: 마우스 휠 한 칸(±100px) = 정확히 ×/÷1.15, 핀치의 작은 delta(±1~10) 는 그만큼 미세하게.
  // 리스너는 툴바·열 머리까지 포함한 바깥 영역(#timeline-wrap / #week-wrap)에 걸어 그 위에서도 페이지 확대가 새지 않게 한다.
  function onZoomWheel(ev) {
    if (!ev.ctrlKey) return;
    ev.preventDefault();
    if (!ev.deltaY) return;
    var c = zoomContainer();
    if (!c) return;
    var offsetY = ev.clientY - c.getBoundingClientRect().top;
    if (!(offsetY >= 0 && offsetY <= c.clientHeight)) offsetY = c.clientHeight / 2; // 툴바·열 머리 위: 중앙 고정
    var dy = ev.deltaY;
    if (ev.deltaMode === 1) dy *= 33; else if (ev.deltaMode === 2) dy *= 100; // 줄/페이지 단위(Firefox 등) → px 환산
    var amount = Math.min(1, Math.max(-1, -dy / 100));
    setZoom(state.pxPerMin * Math.pow(ZOOM_STEP_WHEEL, amount), offsetY);
  }

  function wireZoom() {
    $('timeline-wrap').addEventListener('wheel', onZoomWheel, { passive: false });
    $('week-wrap').addEventListener('wheel', onZoomWheel, { passive: false });
    // 툴바는 하루·주 컨테이너에 하나씩 (같은 동작). −/+ 는 컨테이너 중앙 시각 고정
    Array.prototype.forEach.call(document.querySelectorAll('.zoom-bar'), function (bar) {
      bar.querySelector('.zoom-out').addEventListener('click', function () { setZoom(state.pxPerMin / ZOOM_STEP_BTN); });
      bar.querySelector('.zoom-in').addEventListener('click', function () { setZoom(state.pxPerMin * ZOOM_STEP_BTN); });
      bar.querySelector('.zoom-fit').addEventListener('click', zoomFit);
    });
  }

  window.MAENG = { state: state, api: api, loadDay: loadDay, loadStatus: loadStatus, renderTimeline: renderTimeline, openPopover: openPopover, closePopover: closePopover,
    setView: setView, loadRange: loadRange, renderWeek: renderWeek, renderMonth: renderMonth,
    setZoom: setZoom, zoomFit: zoomFit, fitPxFor: fitPxFor, PAD_TOP: PAD_TOP };
})();
