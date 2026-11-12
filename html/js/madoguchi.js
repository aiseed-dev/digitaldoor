/* デジタルドア — お問い合わせと照会(/contact/)の頁のスクリプト。
 *
 * 仕様は docs/窓口仕様書.md(4 章と 5 章)。外の SDK もライブラリも読まず、
 * PocketBase の API を fetch(と、提供者が apple の時だけ EventSource)で呼ぶ。
 *
 * - 基の URL は main の data-pb から読む。ここに URL を書かない。
 * - サインインは、提供者が apple でない時は「頁に戻ってくる形」(4.2)、
 *   apple の時だけ「全部を一度に行う形」(4.3)。
 * - 札と控えは sessionStorage だけに置く(4.4)。
 * - PocketBase から来た文字は textContent で入れる(12 章)。
 *
 * 前半は DOM に頼らない純粋な関数で、node からも読める(tests/sitebuild/test_madoguchi_js.py)。
 */
(function () {
  "use strict";

  var TOKEN_KEY = "madoguchi.token";
  var CONTROL_KEY = "madoguchi.oauth2";
  var SHEET_FORMAT = "digitalkey-shoukai/1";
  var LIST_PER_PAGE = 50;
  var APPLE_WAIT_MS = 5 * 60 * 1000;

  var TEXT = {
    network: "窓口につながりません。しばらくしてから、もう一度試してください",
    badSend: "送れませんでした。種類、件名、本文を確かめてください",
    signInAgain: "サインインし直してください",
    tooMany: "続けて送りすぎました。しばらく待ってから、もう一度試してください",
    noControl: "サインインの控えがありません。もう一度ボタンを押してください",
    cancelled: "サインインが取り消されたか、うまくいきませんでした。もう一度ボタンを押してください",
    mismatch: "サインインの確かめが合いません。もう一度ボタンを押してください",
    signInFailed: "サインインできませんでした",
    preparing: "今はサインインを用意している所です。",
    popupBlocked: "ブラウザがサインインの窓を止めました。この頁のポップアップを許してから、もう一度押してください",
    noEventSource: "このブラウザでは Apple のサインインを使えません",
    timeout: "時間が切れました。もう一度押してください",
    disconnected: "つながりが切れました。もう一度押してください",
    sent: "送りました。返事は、サインインに使ったメールアドレスに送ります(返事が要る時)",
    empty: "件名と本文を書いてください",
    noItems: "まだ送った物はありません",
    confirmDelete: "この問い合わせを消します。元に戻せません",
    notLinked: "まだ結ばれていません",
    unreadable: "このコピーの形は読めません",
    noEnd: "期限なし"
  };

  // ---------------------------------------------------------------------
  // 純粋な関数(DOM に頼らない)
  // ---------------------------------------------------------------------

  /** 戻り先: 今の origin と、path の末尾の index.html を除いた物(4.2)。 */
  function redirectURLFrom(origin, pathname) {
    return origin + String(pathname).replace(/index\.html$/, "");
  }

  /** 基の URL(data-pb の値)の末尾の / を除く。 */
  function baseFrom(value) {
    return String(value || "").replace(/\/+$/, "");
  }

  /**
   * 頁の URL から、提供者からの戻りを読む(4.2 の「戻ってきた時」の 1 と 2)。
   * code、state、error のどれも無ければ isReturn は偽。
   * cleanURL は問いの部分を全部除いた URL(この頁は自分の問いを使わないので、
   * 提供者が付けた scope、authuser、prompt なども一緒に消える)。
   */
  function readReturn(href) {
    var u = new URL(href);
    var p = u.searchParams;
    var code = p.get("code");
    var state = p.get("state");
    var error = p.get("error");
    return {
      isReturn: code !== null || state !== null || error !== null,
      code: code,
      state: state,
      error: error,
      cleanURL: u.origin + u.pathname + u.hash
    };
  }

  /**
   * 戻りと控えを照らし合わせる(4.2 の「戻ってきた時」の 4 と 5)。
   * 通れば {ok: true, body}(auth-with-oauth2 に送る本文)、通らなければ {ok: false, message}。
   */
  function checkReturn(ret, control) {
    if (!control || typeof control !== "object" || !control.state || !control.codeVerifier) {
      return { ok: false, message: TEXT.noControl };
    }
    if (ret.error) {
      return { ok: false, message: TEXT.cancelled };
    }
    if (!ret.code || ret.state !== control.state) {
      return { ok: false, message: TEXT.mismatch };
    }
    return {
      ok: true,
      body: {
        provider: control.name,
        code: ret.code,
        codeVerifier: control.codeVerifier,
        redirectURL: control.redirectURL
      }
    };
  }

  /** 控えの文字列を読む。壊れていれば null。 */
  function parseControl(raw) {
    if (!raw) return null;
    try {
      var c = JSON.parse(raw);
      return c && typeof c === "object" ? c : null;
    } catch (e) {
      return null;
    }
  }

  /** auth-methods の応答から、ボタンにする提供者の列 [{name, label}] を作る(5.3)。 */
  function providerButtons(methods) {
    var o = methods && methods.oauth2;
    if (!o || !o.enabled || !Array.isArray(o.providers)) return [];
    var out = [];
    for (var i = 0; i < o.providers.length; i++) {
      var p = o.providers[i];
      if (!p || typeof p.name !== "string" || !p.name) continue;
      var shown = typeof p.displayName === "string" && p.displayName ? p.displayName : p.name;
      out.push({ name: p.name, label: shown + " でサインイン" });
    }
    return out;
  }

  /** 失敗の応答を頁の文にする(4.6)。status 0 はネットワークの失敗。 */
  function errorMessage(status, op) {
    if (status === 0) return TEXT.network;
    if (status === 400 && op === "send") return TEXT.badSend;
    if (status === 401 || status === 403) return TEXT.signInAgain;
    if (status === 429) return TEXT.tooMany;
    return "うまくいきませんでした(" + status + ")";
  }

  function pad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  /** PocketBase の日時(UTC の "YYYY-MM-DD HH:MM:SS.sssZ")を、地方時の "YYYY-MM-DD HH:MM" にする。 */
  function formatCreated(s) {
    if (typeof s !== "string" || !s) return "";
    var d = new Date(s.replace(" ", "T"));
    if (isNaN(d.getTime())) return s;
    return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate()) +
      " " + pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  }

  function str(v) {
    return v === null || v === undefined ? "" : String(v);
  }

  /** 問い合わせの一覧の応答の items を、表示する文字の列にする(5.4)。 */
  function toiawaseRows(items) {
    if (!Array.isArray(items)) return [];
    return items.map(function (it) {
      it = it || {};
      return {
        id: str(it.id),
        created: formatCreated(it.created),
        kind: str(it.kind),
        subject: str(it.subject),
        status: str(it.status),
        reply: it.reply ? "返事が要る" : "返事は要らない",
        body: str(it.body)
      };
    });
  }

  /**
   * 消す操作の並び(頁の中の二段の確かめ)。確かめの前は「消す」だけ、確かめの間は
   * 注意の文と「やめる」「本当に消す」。「消す」のあった所に「やめる」が来るので、
   * 二度押しでは消えない。{note, buttons: [{action, label}]}。
   */
  function deleteControls(confirming) {
    if (!confirming) return { note: "", buttons: [{ action: "ask", label: "消す" }] };
    return {
      note: TEXT.confirmDelete,
      buttons: [{ action: "cancel", label: "やめる" }, { action: "do", label: "本当に消す" }]
    };
  }

  /** 送る本文を作る。件名か本文が空なら null(5.4、4.5)。 */
  function toiawaseBody(ownerId, kind, subject, body, reply) {
    if (!String(subject).trim() || !String(body).trim()) return null;
    return {
      owner: ownerId,
      kind: kind,
      subject: subject,
      body: body,
      reply: !!reply,
      status: "新しい"
    };
  }

  /**
   * 照会用のコピーの一覧の items から、表示の形を作る(5.5)。
   * {state: "none"}、{state: "unreadable"}、{state: "ok", madeAt, issuer, perms, keys} のどれか。
   * perms と keys は、表の一行ずつの文字の列。
   */
  function sheetView(items) {
    if (!Array.isArray(items) || items.length === 0 || !items[0]) return { state: "none" };
    var rec = items[0];
    var sheet = rec.sheet;
    if (!sheet || typeof sheet !== "object" || sheet["形式"] !== SHEET_FORMAT) {
      return { state: "unreadable" };
    }
    var perms = Array.isArray(sheet["権限"]) ? sheet["権限"] : [];
    var keys = Array.isArray(sheet["鍵"]) ? sheet["鍵"] : [];
    return {
      state: "ok",
      madeAt: str(rec.made_at),
      issuer: str(rec.issuer),
      perms: perms.map(function (p) {
        p = p || {};
        return [str(p["番号"]), str(p["対象の種類"]), str(p["対象"]), str(p["開始"]),
          str(p["終了"]) || TEXT.noEnd, str(p["状態"])];
      }),
      keys: keys.map(function (k) {
        k = k || {};
        var doors = Array.isArray(k["扉"]) ? k["扉"].map(str).filter(Boolean) : [];
        var where = doors.length ? doors.join("、") : str(k["機器"]);
        return [str(k["経路"]), where, str(k["識別"]), str(k["状態"]), str(k["効くまで"]), str(k["権限"])];
      })
    };
  }

  /**
   * URL の問いを置き換える(4.3 の 6)。PocketBase の JS SDK の _replaceQueryParams と同じ読み方と書き方。
   * replacements の値が null なら、その問いを除く。
   */
  function replaceQueryParams(url, replacements) {
    var q = url.indexOf("?");
    var path = q >= 0 ? url.substring(0, q) : url;
    var query = q >= 0 ? url.substring(q + 1) : "";
    var params = {};
    var order = [];
    var parts = query.split("&");
    for (var i = 0; i < parts.length; i++) {
      if (parts[i] === "") continue;
      var pair = parts[i].split("=");
      var key = decodeURIComponent(pair.shift().replace(/\+/g, " "));
      var value = decodeURIComponent(pair.join("=").replace(/\+/g, " "));
      if (!Object.prototype.hasOwnProperty.call(params, key)) order.push(key);
      params[key] = value;
    }
    for (var r in replacements) {
      if (!Object.prototype.hasOwnProperty.call(replacements, r)) continue;
      if (replacements[r] === null || replacements[r] === undefined) {
        delete params[r];
      } else {
        if (!Object.prototype.hasOwnProperty.call(params, r)) order.push(r);
        params[r] = String(replacements[r]);
      }
    }
    var out = [];
    for (var j = 0; j < order.length; j++) {
      var k = order[j];
      if (!Object.prototype.hasOwnProperty.call(params, k)) continue;
      out.push(encodeURIComponent(k.replace(/%20/g, "+")) + "=" +
        encodeURIComponent(params[k].replace(/%20/g, "+")));
    }
    return out.length ? path + "?" + out.join("&") : path;
  }

  var pure = {
    TEXT: TEXT,
    redirectURLFrom: redirectURLFrom,
    baseFrom: baseFrom,
    readReturn: readReturn,
    checkReturn: checkReturn,
    parseControl: parseControl,
    providerButtons: providerButtons,
    errorMessage: errorMessage,
    formatCreated: formatCreated,
    toiawaseRows: toiawaseRows,
    deleteControls: deleteControls,
    toiawaseBody: toiawaseBody,
    sheetView: sheetView,
    replaceQueryParams: replaceQueryParams
  };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = pure;
    return;
  }

  // ---------------------------------------------------------------------
  // 頁(DOM)
  // ---------------------------------------------------------------------

  function supported() {
    try {
      if (typeof window.fetch !== "function") return false;
      if (typeof window.Promise !== "function") return false;
      if (typeof window.URL !== "function") return false;
      if (!window.history || typeof window.history.replaceState !== "function") return false;
      if (!window.sessionStorage) return false;
      window.sessionStorage.getItem(TOKEN_KEY);
      return true;
    } catch (e) {
      return false;
    }
  }

  var main = document.querySelector("main[data-pb]");
  if (!main || !supported()) return;

  var B = baseFrom(main.getAttribute("data-pb"));
  var store = window.sessionStorage;
  var record = null; // users のレコード。頁を描く間だけ持つ

  function $(id) {
    return document.getElementById(id);
  }

  var el = {
    nojs: $("madoguchi-nojs"),
    signin: $("madoguchi-signin"),
    signinStatus: $("madoguchi-signin-status"),
    providers: $("madoguchi-providers"),
    preparing: $("madoguchi-preparing"),
    preparingText: $("madoguchi-preparing-text"),
    app: $("madoguchi-app"),
    email: $("madoguchi-email"),
    signout: $("madoguchi-signout"),
    status: $("madoguchi-status"),
    form: $("madoguchi-form"),
    kind: $("madoguchi-kind"),
    subject: $("madoguchi-subject"),
    body: $("madoguchi-body"),
    reply: $("madoguchi-reply"),
    send: $("madoguchi-send"),
    list: $("madoguchi-list"),
    number: $("madoguchi-number"),
    sheet: $("madoguchi-sheet")
  };

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function make(tag, text, cls) {
    var n = document.createElement(tag);
    if (text !== undefined && text !== null) n.textContent = text;
    if (cls) n.className = cls;
    return n;
  }

  function say(node, text) {
    node.textContent = text || "";
  }

  // --- API ---

  function getToken() {
    try {
      return store.getItem(TOKEN_KEY);
    } catch (e) {
      return null;
    }
  }

  function setToken(t) {
    try {
      store.setItem(TOKEN_KEY, t);
    } catch (e) { /* 置けない時は、この頁を開いている間だけ使える */ }
  }

  function dropToken() {
    try {
      store.removeItem(TOKEN_KEY);
    } catch (e) { /* 何もしない */ }
  }

  /** PocketBase を呼ぶ。{status, data}。ネットワークの失敗は status 0。 */
  function api(method, path, body, withToken) {
    var headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (withToken) {
      var t = getToken();
      if (t) headers["Authorization"] = t;
    }
    var init = { method: method, headers: headers, credentials: "omit", cache: "no-store" };
    if (body !== undefined) init.body = JSON.stringify(body);
    return fetch(B + path, init).then(function (res) {
      if (res.status === 204) return { status: 204, data: null };
      return res.text().then(function (txt) {
        var data = null;
        try {
          data = txt ? JSON.parse(txt) : null;
        } catch (e) {
          data = null;
        }
        return { status: res.status, data: data };
      });
    }, function () {
      return { status: 0, data: null };
    });
  }

  function authMethods() {
    return api("GET", "/api/collections/users/auth-methods?fields=mfa,otp,password,oauth2");
  }

  // --- 画面の切り替え ---

  function showSignin(message) {
    record = null;
    el.app.hidden = true;
    el.signin.hidden = false;
    say(el.signinStatus, message);
    loadProviders();
  }

  function needSignInAgain() {
    dropToken();
    showSignin(TEXT.signInAgain);
  }

  function loadProviders() {
    clear(el.providers);
    el.preparing.hidden = true;
    return authMethods().then(function (r) {
      if (r.status !== 200 || !r.data) {
        if (!el.signinStatus.textContent) say(el.signinStatus, r.status === 0 ? TEXT.network : errorMessage(r.status));
        return;
      }
      var list = providerButtons(r.data);
      if (!list.length) {
        say(el.preparingText, TEXT.preparing);
        el.preparing.hidden = false;
        return;
      }
      list.forEach(function (p) {
        var b = make("button", p.label, "madoguchi-button");
        b.type = "button";
        b.setAttribute("data-provider", p.name);
        b.addEventListener("click", function () {
          if (p.name === "apple") signInApple(b);
          else signInRedirect(p.name);
        });
        el.providers.appendChild(b);
      });
    });
  }

  function setProviderButtons(disabled) {
    var bs = el.providers.querySelectorAll("button");
    for (var i = 0; i < bs.length; i++) bs[i].disabled = disabled;
  }

  // --- 4.2 頁に戻ってくる形 ---

  function pickProvider(methods, name) {
    var ps = methods && methods.oauth2 && Array.isArray(methods.oauth2.providers) ? methods.oauth2.providers : [];
    for (var i = 0; i < ps.length; i++) {
      if (ps[i] && ps[i].name === name) return ps[i];
    }
    return null;
  }

  function signInRedirect(name) {
    setProviderButtons(true);
    say(el.signinStatus, "");
    authMethods().then(function (r) {
      var p = r.status === 200 ? pickProvider(r.data, name) : null;
      if (!p || !p.authURL || !p.state || !p.codeVerifier) {
        setProviderButtons(false);
        say(el.signinStatus, r.status === 0 ? TEXT.network : TEXT.signInFailed);
        return;
      }
      var redirectURL = redirectURLFrom(location.origin, location.pathname);
      try {
        store.setItem(CONTROL_KEY, JSON.stringify({
          name: p.name, state: p.state, codeVerifier: p.codeVerifier, redirectURL: redirectURL
        }));
      } catch (e) {
        setProviderButtons(false);
        say(el.signinStatus, TEXT.signInFailed);
        return;
      }
      location.assign(p.authURL + encodeURIComponent(redirectURL));
    });
  }

  /** 戻ってきた時。札を受けた時は true、戻りでない時と受けられなかった時は false(と文)。 */
  function handleReturn() {
    var ret = readReturn(location.href);
    if (!ret.isReturn) return Promise.resolve({ token: false, message: "" });
    // 札を受ける前に、アドレス欄と履歴から code と state を消す
    history.replaceState(null, "", ret.cleanURL);
    var control = null;
    try {
      control = parseControl(store.getItem(CONTROL_KEY));
      store.removeItem(CONTROL_KEY);
    } catch (e) {
      control = null;
    }
    var check = checkReturn(ret, control);
    if (!check.ok) return Promise.resolve({ token: false, message: check.message });
    return exchange(check.body);
  }

  /** auth-with-oauth2 で札を受ける(4.2 の 5、4.3 の 9)。 */
  function exchange(body) {
    return api("POST", "/api/collections/users/auth-with-oauth2", body).then(function (r) {
      if (r.status === 200 && r.data && r.data.token && r.data.record) {
        setToken(r.data.token);
        record = r.data.record;
        return { token: true, message: "" };
      }
      return { token: false, message: r.status === 0 ? TEXT.network : TEXT.signInFailed };
    });
  }

  // --- 4.3 全部を一度に行う形(apple の時だけ) ---

  function signInApple(button) {
    say(el.signinStatus, "");
    if (typeof window.EventSource !== "function") {
      say(el.signinStatus, TEXT.noEventSource);
      return;
    }
    // await より前に窓を開ける(Safari は非同期の処理の後に開いた窓を止める)
    var popup = window.open("", "madoguchi_signin", "width=520,height=680");
    if (!popup) {
      say(el.signinStatus, TEXT.popupBlocked);
      return;
    }
    setProviderButtons(true);
    var es = null;
    var timer = null;
    var done = false;

    function finish(message) {
      if (done) return;
      done = true;
      if (timer) clearTimeout(timer);
      if (es) es.close();
      try {
        popup.close();
      } catch (e) { /* 閉じられない時は本人が閉じる */ }
      setProviderButtons(false);
      if (message) say(el.signinStatus, message);
    }

    authMethods().then(function (r) {
      var p = r.status === 200 ? pickProvider(r.data, "apple") : null;
      if (!p || !p.authURL || !p.codeVerifier) {
        finish(r.status === 0 ? TEXT.network : TEXT.signInFailed);
        return;
      }
      var redirectURL = B + "/api/oauth2-redirect";
      var clientId = "";
      es = new EventSource(B + "/api/realtime");
      timer = setTimeout(function () { finish(TEXT.timeout); }, APPLE_WAIT_MS);
      es.onerror = function () { finish(TEXT.disconnected); };
      es.addEventListener("PB_CONNECT", function (ev) {
        if (clientId || done) return;
        var d = null;
        try {
          d = JSON.parse(ev.data);
        } catch (e) {
          d = null;
        }
        if (!d || !d.clientId) {
          finish(TEXT.signInFailed);
          return;
        }
        clientId = d.clientId;
        api("POST", "/api/realtime", { clientId: clientId, subscriptions: ["@oauth2"] }).then(function (s) {
          if (done) return;
          if (s.status !== 204) {
            finish(s.status === 0 ? TEXT.network : TEXT.signInFailed);
            return;
          }
          var url = replaceQueryParams(p.authURL + redirectURL, { state: clientId });
          try {
            popup.location.href = url;
          } catch (e) {
            finish(TEXT.signInFailed);
          }
        });
      });
      es.addEventListener("@oauth2", function (ev) {
        if (done) return;
        var d = null;
        try {
          d = JSON.parse(ev.data);
        } catch (e) {
          d = null;
        }
        if (!d || d.state !== clientId || d.error || !d.code) {
          finish(TEXT.signInFailed);
          return;
        }
        var body = { provider: "apple", code: d.code, codeVerifier: p.codeVerifier, redirectURL: redirectURL };
        finish("");
        exchange(body).then(function (x) {
          if (x.token) showApp();
          else say(el.signinStatus, x.message);
        });
      });
    });
  }

  // --- サインインの後 ---

  function refresh() {
    return api("POST", "/api/collections/users/auth-refresh", undefined, true).then(function (r) {
      if (r.status === 200 && r.data && r.data.token && r.data.record) {
        setToken(r.data.token);
        record = r.data.record;
        return { ok: true };
      }
      if (r.status === 401 || r.status === 403 || r.status === 404) {
        dropToken();
        return { ok: false, message: "" };
      }
      return { ok: false, message: r.status === 0 ? TEXT.network : errorMessage(r.status) };
    });
  }

  function showApp() {
    el.signin.hidden = true;
    el.app.hidden = false;
    say(el.email, str(record.email));
    say(el.number, str(record.id));
    say(el.status, "");
    loadList();
    loadSheet();
  }

  function cell(tag, text) {
    return make(tag, text);
  }

  function loadList() {
    return api("GET", "/api/collections/toiawase/records?sort=-created&perPage=" + LIST_PER_PAGE + "&skipTotal=1",
      undefined, true).then(function (r) {
      if (r.status === 401 || r.status === 403) return needSignInAgain();
      pendingDelete = null;
      clear(el.list);
      if (r.status !== 200 || !r.data) {
        el.list.appendChild(make("p", errorMessage(r.status), "note"));
        return;
      }
      var rows = toiawaseRows(r.data.items);
      if (!rows.length) {
        el.list.appendChild(make("p", TEXT.noItems, "note"));
        return;
      }
      var ul = make("ul", null, "madoguchi-items");
      rows.forEach(function (row) {
        var li = make("li", null, "madoguchi-item");
        var head = make("p", null, "madoguchi-item-head");
        head.appendChild(make("span", row.created, "madoguchi-when"));
        head.appendChild(make("span", row.kind, "madoguchi-tag"));
        head.appendChild(make("span", row.status, "madoguchi-tag"));
        head.appendChild(make("span", row.reply, "madoguchi-reply-note"));
        li.appendChild(head);
        li.appendChild(make("p", row.subject, "madoguchi-subject"));
        var det = make("details");
        det.appendChild(make("summary", "本文"));
        det.appendChild(make("p", row.body, "madoguchi-body-text"));
        li.appendChild(det);
        var actions = make("div", null, "madoguchi-actions");
        li.appendChild(actions);
        renderDelete(actions, row.id, false);
        ul.appendChild(li);
      });
      el.list.appendChild(ul);
    });
  }

  // 確かめの間の行(一度に一つ)。ほかの操作をした時は、cancelPendingDelete で元に戻す
  var pendingDelete = null;

  function cancelPendingDelete() {
    if (!pendingDelete) return;
    var p = pendingDelete;
    pendingDelete = null;
    if (p.node.isConnected) renderDelete(p.node, p.id, false);
  }

  /** 一件の「消す」の並びを描く。confirming が真なら「やめる」「本当に消す」で、焦点は「やめる」。 */
  function renderDelete(node, id, confirming) {
    clear(node);
    var c = deleteControls(confirming);
    if (c.note) node.appendChild(make("span", c.note, "madoguchi-confirm-note"));
    c.buttons.forEach(function (b) {
      var btn = make("button", b.label, "madoguchi-button madoguchi-delete" +
        (b.action === "do" ? " madoguchi-delete-do" : ""));
      btn.type = "button";
      btn.setAttribute("data-action", b.action);
      btn.addEventListener("click", function () {
        if (b.action === "ask") {
          cancelPendingDelete();
          pendingDelete = { node: node, id: id };
          renderDelete(node, id, true);
          var first = node.querySelector('button[data-action="cancel"]');
          if (first) first.focus();
        } else if (b.action === "cancel") {
          pendingDelete = null;
          renderDelete(node, id, false);
          var again = node.querySelector('button[data-action="ask"]');
          if (again) again.focus();
        } else {
          pendingDelete = null;
          removeItem(id, node);
        }
      });
      node.appendChild(btn);
    });
  }

  function removeItem(id, node) {
    var bs = node.querySelectorAll("button");
    for (var i = 0; i < bs.length; i++) bs[i].disabled = true;
    api("DELETE", "/api/collections/toiawase/records/" + encodeURIComponent(id), undefined, true).then(function (r) {
      if (r.status === 401 || r.status === 403) return needSignInAgain();
      if (r.status === 204 || r.status === 404) return loadList();
      renderDelete(node, id, false);
      say(el.status, errorMessage(r.status));
    });
  }

  function table(headers, rows) {
    var wrap = make("div", null, "table-wrap");
    var t = make("table", null, "madoguchi-table");
    var thead = make("thead");
    var tr = make("tr");
    headers.forEach(function (h) { tr.appendChild(cell("th", h)); });
    thead.appendChild(tr);
    t.appendChild(thead);
    var tbody = make("tbody");
    rows.forEach(function (row) {
      var r = make("tr");
      row.forEach(function (v) { r.appendChild(cell("td", v)); });
      tbody.appendChild(r);
    });
    t.appendChild(tbody);
    wrap.appendChild(t);
    return wrap;
  }

  function loadSheet() {
    return api("GET", "/api/collections/shoukai/records?perPage=1", undefined, true).then(function (r) {
      if (r.status === 401 || r.status === 403) return needSignInAgain();
      clear(el.sheet);
      if (r.status !== 200 || !r.data) {
        el.sheet.appendChild(make("p", errorMessage(r.status), "note"));
        return;
      }
      var v = sheetView(r.data.items);
      if (v.state === "none") {
        el.sheet.appendChild(make("p", TEXT.notLinked));
        return;
      }
      if (v.state === "unreadable") {
        el.sheet.appendChild(make("p", TEXT.unreadable));
        return;
      }
      el.sheet.appendChild(make("p", "コピーを作った時刻: " + v.madeAt));
      el.sheet.appendChild(make("p", "発行者の識別: " + v.issuer));
      el.sheet.appendChild(make("h3", "権限"));
      if (v.perms.length) {
        el.sheet.appendChild(table(["番号", "対象の種類", "対象", "開始", "終了", "状態"], v.perms));
      } else {
        el.sheet.appendChild(make("p", "権限はありません", "note"));
      }
      el.sheet.appendChild(make("h3", "鍵"));
      if (v.keys.length) {
        el.sheet.appendChild(table(["経路", "扉か機器", "識別", "状態", "効くまで", "権限の番号"], v.keys));
      } else {
        el.sheet.appendChild(make("p", "鍵はありません", "note"));
      }
      el.sheet.appendChild(make("p",
        "これは台帳のコピーです。正は鍵の管理者の台帳で、扉はコピーを見ずに自分で確かめます。" +
        "コピーを置き直す間隔は鍵の管理者が決めます。自分の権限が使われた記録は、まだ見られません。", "note"));
    });
  }

  function onSubmit(ev) {
    ev.preventDefault();
    cancelPendingDelete();
    if (!record) return;
    var body = toiawaseBody(record.id, el.kind.value, el.subject.value, el.body.value, el.reply.checked);
    if (!body) {
      say(el.status, TEXT.empty);
      return;
    }
    el.send.disabled = true;
    say(el.status, "");
    api("POST", "/api/collections/toiawase/records", body, true).then(function (r) {
      el.send.disabled = false;
      if (r.status === 401 || r.status === 403) return needSignInAgain();
      if (r.status === 200) {
        say(el.status, TEXT.sent);
        el.subject.value = "";
        el.body.value = "";
        el.kind.value = "質問";
        el.reply.checked = true;
        loadList();
        return;
      }
      say(el.status, errorMessage(r.status, "send"));
    });
  }

  function signOut() {
    pendingDelete = null;
    dropToken();
    showSignin("");
  }

  // --- 始まり ---

  el.nojs.hidden = true;
  el.form.addEventListener("submit", onSubmit);
  el.signout.addEventListener("click", signOut);

  handleReturn().then(function (ret) {
    if (ret.token) {
      showApp();
      return;
    }
    if (!getToken()) {
      showSignin(ret.message);
      return;
    }
    refresh().then(function (x) {
      if (x.ok) showApp();
      else showSignin(ret.message || x.message);
    });
  });
})();
