/* ============================================================
   form-render.js
   フォーム定義(スキーマ)→ HTMLフォーム(確認画面つき)

     FormRescue.render(container, schema)

   描画・確認画面・検証・送信は、すべて schema から生成する。
   運用する人が触るのは schema だけ。この仕組みは、一度作れば、触らない。
   フレームワーク不使用(FormRescue の実装方針に合わせる)。

   schema の形:
     { action, sitekey, confirm, fields: [ … ] }
   field:
     name          送信キー
     label         表示名
     type          text / email / tel / textarea / select / radio / checkbox
     required      必須
     options       select / radio / checkbox の選択肢
     match         "別の項目のname" と一致必須(メール確認用)
     placeholder   入力欄の、薄い例示
     help          常に見える、項目ヘルプ(説明・書式案内)
     default       初期値(radio/checkbox は文字列 or 配列で初期選択)
     format        書式(名前つき。下の FORMATS)
     pattern       自前の正規表現(文字列)。format より優先
     patternMessage pattern 不一致のメッセージ
   ============================================================ */
(function () {

  // 書式(名前つき)。日本のフォームで、よく使うもの。
  const FORMATS = {
    postal:        { re: /^\d{3}-?\d{4}$/,       msg: '郵便番号(例:123-4567)の形式で、ご入力ください。' },
    tel:           { re: /^[0-9-]{10,13}$/,      msg: '電話番号(半角数字、ハイフン可)で、ご入力ください。' },
    katakana:      { re: /^[ァ-ヶー　\s]+$/, msg: '全角カタカナで、ご入力ください。' },
    hiragana:      { re: /^[ぁ-んー　\s]+$/, msg: 'ひらがなで、ご入力ください。' },
    hankakuNumber: { re: /^\d+$/,                msg: '半角数字で、ご入力ください。' },
  };

  function el(tag, attrs, kids) {
    const e = document.createElement(tag);
    if (attrs) for (const k in attrs) {
      if (attrs[k] == null) continue;
      if (k === 'text') e.textContent = attrs[k]; else e.setAttribute(k, attrs[k]);
    }
    (kids || []).forEach(c => e.append(c));
    return e;
  }

  // 1フィールド → 入力コントロール(.fr-field)
  function fieldControl(f) {
    const wrap  = el('div', { class: 'fr-field' });
    const label = el('label', { text: f.label });
    if (f.required) label.append(el('span', { class: 'fr-req', text: '必須' }));
    wrap.append(label);

    if (f.help) wrap.append(el('p', { class: 'fr-help', text: f.help }));  // 項目ヘルプ

    const attrs = { name: f.name, 'data-label': f.label };
    if (f.required) attrs['data-required'] = '';
    if (f.match)    attrs['data-match'] = f.match;

    if (f.type === 'textarea') {
      const t = el('textarea', Object.assign({ placeholder: f.placeholder }, attrs));
      if (f.default != null) t.value = f.default;
      wrap.append(t);

    } else if (f.type === 'select') {
      const s = el('select', attrs);
      s.append(el('option', { value: '', text: '選択してください' }));
      (f.options || []).forEach(o => {
        const opt = el('option', { value: o, text: o });
        if (o === f.default) opt.setAttribute('selected', '');
        s.append(opt);
      });
      wrap.append(s);

    } else if (f.type === 'radio' || f.type === 'checkbox') {
      const defs = Array.isArray(f.default) ? f.default : (f.default != null ? [f.default] : []);
      (f.options || []).forEach(o => {
        const lab = el('label', { class: 'fr-choice' });
        const inp = el('input', Object.assign({ type: f.type, value: o }, attrs));
        if (defs.indexOf(o) !== -1) inp.setAttribute('checked', '');
        lab.append(inp, document.createTextNode(o));
        wrap.append(lab);
      });

    } else {   // text / email / tel
      const inp = el('input', Object.assign({ type: f.type || 'text', placeholder: f.placeholder }, attrs));
      if (f.default != null) inp.value = f.default;
      wrap.append(inp);
    }
    return wrap;
  }

  function render(container, schema) {
    container.innerHTML = '';
    container.classList.add('fr-root');
    const fields = schema.fields || [];

    // ---- 入力画面 ----
    const form    = el('form', { novalidate: '' });
    fields.forEach(f => form.append(fieldControl(f)));
    const errorEl = el('p', { class: 'fr-error', 'aria-live': 'polite' });
    form.append(errorEl, el('div', { class: 'fr-buttons' }, [
      el('button', { type: 'submit', text: schema.confirm === false ? '送信する' : '確認する' }),
    ]));

    // ---- 確認画面 ----
    const review   = el('dl', { id: 'fr-review' });
    const tsBox    = el('div');
    const statusEl = el('p', { class: 'fr-status', 'aria-live': 'polite' });
    const backBtn  = el('button', { type: 'button', text: '修正する' });
    const sendBtn  = el('button', { type: 'button', text: '送信する' });
    const confirmScreen = el('div', { class: 'fr-screen', 'data-screen': 'confirm', hidden: '' }, [
      el('p', { text: '以下の内容で、よろしいですか?' }), review, tsBox, statusEl,
      el('div', { class: 'fr-buttons' }, [backBtn, sendBtn]),
    ]);

    // ---- 完了画面 ----
    const doneScreen = el('div', { class: 'fr-screen', 'data-screen': 'done', hidden: '' }, [
      el('p', { text: '送信しました。お問い合わせ、ありがとうございました。' }),
    ]);

    container.append(
      el('div', { class: 'fr-screen', 'data-screen': 'input' }, [form]),
      confirmScreen, doneScreen);

    // ---- Turnstile(api.js の非同期ロードを待って、明示レンダー)----
    let token = '', tsRendered = false;
    (function ensureTS() {
      if (!schema.sitekey || tsRendered) return;
      if (window.turnstile) {
        window.turnstile.render(tsBox, {
          sitekey: schema.sitekey,
          callback: t => { token = t; },
          'expired-callback': () => { token = ''; },
        });
        tsRendered = true;
      } else setTimeout(ensureTS, 200);
    })();

    // ---- 共通処理 ----
    function show(name) {
      container.querySelectorAll('.fr-screen').forEach(s => { s.hidden = s.dataset.screen !== name; });
      window.scrollTo(0, 0);
    }

    function collect() {
      const out = {};
      form.querySelectorAll('[name]').forEach(x => {
        const n = x.name, lb = x.dataset.label || n;
        if (x.type === 'checkbox') {
          out[n] || (out[n] = { label: lb, value: [] });
          if (x.checked) out[n].value.push(x.value);
        } else if (x.type === 'radio') {
          out[n] || (out[n] = { label: lb, value: '' });
          if (x.checked) out[n].value = x.value;
        } else out[n] = { label: lb, value: x.value };
      });
      return out;
    }

    function validate(d) {
      // 必須
      for (const f of fields) if (f.required) {
        const v = d[f.name].value, empty = Array.isArray(v) ? v.length === 0 : !String(v).trim();
        if (empty) return f.label + 'を入力してください。';
      }
      // メール形式 / 一致 / 書式
      for (const f of fields) {
        const v = d[f.name].value;
        if (Array.isArray(v) || !v) continue;
        if (f.type === 'email' && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v))
          return f.label + 'の形式が正しくありません。';
        // 書式: pattern 優先、なければ format 名
        let re = null, msg = null;
        if (f.pattern) { re = new RegExp(f.pattern); msg = f.patternMessage || (f.label + 'の形式が正しくありません。'); }
        else if (f.format && FORMATS[f.format]) { re = FORMATS[f.format].re; msg = FORMATS[f.format].msg; }
        if (re && !re.test(v)) return msg;
      }
      for (const f of fields) {
        if (f.match && d[f.name].value !== d[f.match].value) return 'メールアドレスが一致しません。';
      }
      return null;
    }

    // 「確認する」(confirm:false なら、直接送信)
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      const d = collect(), err = validate(d);
      if (err) { errorEl.textContent = err; return; }
      errorEl.textContent = '';
      if (schema.confirm === false) return doSubmit(d, errorEl);
      review.innerHTML = '';
      for (const n in d) {
        const v = d[n].value, shown = Array.isArray(v) ? v.join('、') : v;
        review.append(el('dt', { text: d[n].label }), el('dd', { text: shown || '(未入力)' }));
      }
      show('confirm');
    });

    backBtn.addEventListener('click', () => show('input'));
    sendBtn.addEventListener('click', () => doSubmit(collect(), statusEl));

    async function doSubmit(d, st) {
      if (schema.sitekey && !token) { st.textContent = '認証(確認)を完了してください。'; return; }
      const payload = {};
      for (const n in d) { const v = d[n].value; payload[n] = Array.isArray(v) ? v.join(',') : v; }
      if (token) payload['cf-turnstile-response'] = token;

      st.textContent = '送信中...'; sendBtn.disabled = true;
      try {
        const res = await fetch(schema.action, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        if (res.ok) return show('done');
        st.textContent = (res.status === 429)
          ? '送信が集中しています。時間をおいて、もう一度お試しください。'
          : '送信に失敗しました。時間をおいて、もう一度お試しください。';
      } catch (_) {
        st.textContent = '通信エラーが発生しました。接続をご確認ください。';
      } finally {
        sendBtn.disabled = false;
        if (window.turnstile && tsRendered) { window.turnstile.reset(); token = ''; }
      }
    }
  }

  // 自動初期化: pywashi 等が出した .fr-form を、埋め込みスキーマから描く。
  // [.form] ブロック → <div class="fr-form"><script type="application/json">…</script></div>
  // なので、各ページに初期化スクリプトを書かなくても、これだけで、描画される。
  function autoInit(root) {
    (root || document).querySelectorAll('.fr-form').forEach(function (mount) {
      if (mount.dataset.frInit) return;                 // 二重初期化を防ぐ
      const s = mount.querySelector('script[type="application/json"]');
      if (!s) return;
      let schema;
      try { schema = JSON.parse(s.textContent); }
      catch (e) { mount.innerHTML = '<p class="fr-error">フォーム定義の読み込みに失敗しました。</p>'; return; }
      mount.dataset.frInit = '1';
      render(mount, schema);                             // 先に schema を読んでから描く(render は中身を空にする)
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { autoInit(); });
  } else {
    autoInit();
  }

  window.FormRescue = { render: render, formats: FORMATS, autoInit: autoInit };
})();
