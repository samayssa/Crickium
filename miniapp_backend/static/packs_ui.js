/* Crickium pack artwork + premium opening experience.
   Pure presentation layer: every card, coin and ruby shown here comes from the
   server's saved opening record. Nothing here decides a reward. */
(function () {
  "use strict";
  const TIERS = {
    bronze:   { name: "BRONZE",   a: "#e0a273", b: "#8a5230", c: "#4a2a16", glow: "#e0a273", stars: 1 },
    silver:   { name: "SILVER",   a: "#e6ece8", b: "#8d9a93", c: "#47524c", glow: "#d5ddd7", stars: 2 },
    gold:     { name: "GOLD",     a: "#f6d887", b: "#b78a2c", c: "#5e4410", glow: "#efc66b", stars: 3 },
    diamond:  { name: "DIAMOND",  a: "#a8f0d9", b: "#3e9b88", c: "#154a42", glow: "#a9d7c4", stars: 4 },
    platinum: { name: "PLATINUM", a: "#dcf98f", b: "#8fbb2d", c: "#33490e", glow: "#c5ef61", stars: 5 },
  };
  let uid = 0;
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const tier = (key) => TIERS[String(key || "").toLowerCase()] || TIERS.bronze;

  function starPath(cx, cy, r) {
    const pts = [];
    for (let i = 0; i < 10; i++) {
      const ang = -Math.PI / 2 + (i * Math.PI) / 5, rad = i % 2 ? r * 0.45 : r;
      pts.push(`${(cx + Math.cos(ang) * rad).toFixed(1)},${(cy + Math.sin(ang) * rad).toFixed(1)}`);
    }
    return pts.join(" ");
  }

  /* Foil pack artwork (SVG). `torn` splits the crimped top off for the opening animation. */
  function art(key, opts = {}) {
    const t = tier(key), id = `pk${++uid}`;
    let outline = "M6 12";
    for (let x = 6; x < 114; x += 6) outline += ` L${x + 3} 7 L${x + 6} 12`;
    outline += " L114 156";
    for (let x = 114; x > 6; x -= 6) outline += ` L${x - 3} 161 L${x - 6} 156`;
    outline += " Z";
    const stars = Array.from({ length: t.stars }, (_, i) => `<polygon points="${starPath(60 + (i - (t.stars - 1) / 2) * 13, 126, 5)}" fill="url(#${id}f)"/>`).join("");
    const initial = esc(t.name.slice(0, 1));
    return `<svg class="pack-art-svg" viewBox="0 0 120 168" role="img" aria-label="${esc(t.name)} pack" xmlns="http://www.w3.org/2000/svg">
<defs>
<linearGradient id="${id}f" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${t.a}"/><stop offset=".55" stop-color="${t.b}"/><stop offset="1" stop-color="${t.a}"/></linearGradient>
<linearGradient id="${id}b" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#16271f"/><stop offset=".5" stop-color="#0d1916"/><stop offset="1" stop-color="#07110f"/></linearGradient>
<linearGradient id="${id}s" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".28"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
<clipPath id="${id}c"><path d="${outline}"/></clipPath>
</defs>
<ellipse cx="60" cy="162" rx="38" ry="4" fill="#000" opacity=".45"/>
<g class="pack-foil"><path d="${outline}" fill="url(#${id}b)" stroke="url(#${id}f)" stroke-width="1.6"/>
<g clip-path="url(#${id}c)">
<rect x="6" y="12" width="108" height="18" fill="url(#${id}f)" opacity=".9"/><rect x="6" y="146" width="108" height="12" fill="url(#${id}f)" opacity=".9"/>
<path d="M6 62 L114 34 M6 110 L114 82" stroke="${t.a}" stroke-opacity=".14" stroke-width="10"/>
<g class="pack-sheen"><rect x="-40" y="0" width="34" height="168" fill="url(#${id}s)" transform="skewX(-18)"/></g></g>
<text x="60" y="24" text-anchor="middle" font-size="8.5" font-weight="900" letter-spacing="3" fill="${t.c}" font-family="Inter,system-ui,sans-serif">CRICKIUM</text>
<path d="M60 40 L88 50 V82 Q88 102 60 114 Q32 102 32 82 V50 Z" fill="#07110f" stroke="url(#${id}f)" stroke-width="2.2"/>
<path d="M60 47 L82 55 V81 Q82 97 60 107 Q38 97 38 81 V55 Z" fill="none" stroke="${t.a}" stroke-opacity=".35"/>
<text x="60" y="87" text-anchor="middle" font-size="30" font-weight="950" fill="url(#${id}f)" font-family="Inter,system-ui,sans-serif">${initial}</text>
${stars}
<text x="60" y="154" text-anchor="middle" font-size="7.5" font-weight="900" letter-spacing="2.4" fill="${t.c}" font-family="Inter,system-ui,sans-serif">${esc(t.name)} PACK</text></g></svg>`;
  }

  const rarityClass = (ovr) => (ovr >= 95 ? "mythic" : ovr >= 90 ? "legendary" : ovr >= 85 ? "epic" : ovr >= 80 ? "rare" : "standard");
  const rarityLabel = { mythic: "MYTHIC", legendary: "LEGENDARY", epic: "EPIC", rare: "RARE", standard: "STANDARD" };

  function deliveryChip(card) {
    if (card.status === "added") return `<span class="pk-delivery added">✓ ADDED TO SQUAD</span>`;
    if (card.status === "pending") return `<span class="pk-delivery pending">${card.reason === "full" ? "⚠ SQUAD FULL" : "⚠ ALREADY OWNED"}</span>`;
    return "";
  }

  /* helpers: { playerCard(player, compact), number(n), hydrate() } */
  function begin(packKey, helpers) {
    const t = tier(packKey);
    const root = document.createElement("div");
    root.className = "pk-overlay";
    root.style.setProperty("--pk-glow", t.glow);
    root.innerHTML = `<div class="pk-backdrop"></div><div class="pk-stage"><div class="pk-rays"></div>
      <div class="pk-pack pk-shake">${art(packKey)}</div><div class="pk-flash"></div>
      <p class="pk-caption">Opening your ${esc(t.name)} pack…</p></div>`;
    document.body.appendChild(root);
    document.body.classList.add("pk-lock");
    requestAnimationFrame(() => root.classList.add("show"));
    const stage = root.querySelector(".pk-stage");
    const caption = () => root.querySelector(".pk-caption");
    let closed = false;
    const close = () => { if (closed) return; closed = true; root.classList.remove("show"); document.body.classList.remove("pk-lock"); setTimeout(() => root.remove(), 260); };
    const wait = (ms) => new Promise((r) => setTimeout(r, ms));

    async function reveal(opening, onDone) {
      const cards = opening.cards || [];
      const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      root.querySelector(".pk-pack").classList.remove("pk-shake");
      root.querySelector(".pk-pack").classList.add("pk-tear");
      root.classList.add("burst");
      await wait(reduce ? 50 : 900);
      const num = helpers.number || ((n) => n);
      stage.innerHTML = `<div class="pk-reveal"><span class="pk-eyebrow">${esc(t.name)} PACK · ${cards.length} CARD${cards.length === 1 ? "" : "S"}</span>
        <div class="pk-deck">${cards.map((card, i) => {
          const rc = rarityClass(Number(card.ovr || 0));
          return `<button class="pk-card ${rc}" data-i="${i}" style="--d:${i * 110}ms" aria-label="Reveal card ${i + 1}">
            <span class="pk-card-inner"><span class="pk-face pk-back">${art(packKey)}<b>TAP</b></span>
            <span class="pk-face pk-front"><span class="pk-rarity">${rarityLabel[rc]} · ${num(card.ovr)} OVR</span>${card.player ? helpers.playerCard(card.player, true) : `<span class="pk-missing">Player details unavailable</span>`}${deliveryChip(card)}</span></span></button>`;
        }).join("")}</div>
        <div class="pk-rewards" hidden><span>🪙 ${num(opening.coins)} coins</span><span>◆ ${num(opening.rubies)} rubies</span></div>
        <div class="pk-actions"><button class="button secondary" data-pk="all">Reveal all</button><button class="button primary" data-pk="done" hidden>Done</button></div></div>`;
      helpers.hydrate && helpers.hydrate();
      const deck = stage.querySelector(".pk-deck"), rewards = stage.querySelector(".pk-rewards");
      const doneBtn = stage.querySelector('[data-pk="done"]'), allBtn = stage.querySelector('[data-pk="all"]');
      const check = () => {
        if (deck.querySelectorAll(".pk-card:not(.flipped)").length === 0) { rewards.hidden = false; doneBtn.hidden = false; allBtn.hidden = true; }
      };
      const flip = (el) => {
        if (el.classList.contains("flipped")) return;
        el.classList.add("flipped");
        if (/legendary|mythic/.test(el.className)) { root.classList.add("big-hit"); setTimeout(() => root.classList.remove("big-hit"), 900); }
        try { window.Telegram?.WebApp?.HapticFeedback?.impactOccurred(/legendary|mythic|epic/.test(el.className) ? "heavy" : "light"); } catch (_) {}
        check();
      };
      deck.addEventListener("click", (e) => { const el = e.target.closest(".pk-card"); if (el) flip(el); });
      allBtn.addEventListener("click", async () => {
        for (const el of deck.querySelectorAll(".pk-card:not(.flipped)")) { flip(el); await wait(reduce ? 0 : 260); }
      });
      const waiting = cards.filter((c) => c.status === "pending").length;
      if (waiting) doneBtn.textContent = `Continue · ${waiting} to decide`;
      doneBtn.addEventListener("click", () => { close(); onDone && onDone(opening); });
    }
    return { reveal, close, fail: close };
  }


  /* Release & Replace for pack cards that cannot join the squad (already owned / squad full).
     cards: [{card_id, name.., sell_price, reason, can_replace ...player payload}], squad: [player payloads + sell_price]
     helpers: { playerCard, number, hydrate, ping, toast, release(id), replace(id, playerId) }
     Resolves { changed, balance } when the owner has dealt with every card (or chose "Decide later"). */
  function decide(cards, squad, helpers) {
    return new Promise((resolve) => {
      const num = helpers.number || ((n) => n);
      const queue = cards.slice();
      let changed = false, balance = null;
      const root = document.createElement("div");
      root.className = "pk-overlay pk-dec show";
      root.innerHTML = `<div class="pk-backdrop"></div><div class="pk-stage pk-dec-stage"></div>`;
      document.body.appendChild(root);
      document.body.classList.add("pk-lock");
      const stage = root.querySelector(".pk-dec-stage");
      const finish = () => { root.classList.remove("show"); document.body.classList.remove("pk-lock"); setTimeout(() => root.remove(), 260); resolve({ changed, balance }); };
      const wait = (ms) => new Promise((r) => setTimeout(r, ms));
      const swap = (html) => { stage.classList.remove("pk-dec-in"); void stage.offsetWidth; stage.innerHTML = html; stage.classList.add("pk-dec-in"); helpers.hydrate && helpers.hydrate(); };
      const reasonText = (c) => c.reason === "full" ? `Your squad is full (${num(squad.length)}/25).` : "This player is already in your squad.";
      const price = (c) => Number(c.sell_price || 0);
      const nameOf = (p) => esc(p.name || "Player");
      const busy = (btn, on) => { stage.querySelectorAll("button").forEach((b) => { b.disabled = on; }); if (btn) btn.classList.toggle("loading", on); };

      function stepCard() {
        const card = queue[0];
        if (!card) { finish(); return; }
        const left = queue.length;
        swap(`<div class="pk-dec-card">
          <span class="pk-eyebrow">PACK PULL · ${left} TO DECIDE</span>
          <div class="pk-dec-player">${helpers.playerCard(card, true)}</div>
          <p class="pk-dec-reason">${reasonText(card)}</p>
          <div class="pk-dec-coins"><span>♻</span> Release value <b data-count="${price(card)}">0</b> coins</div>
          <div class="pk-dec-actions">
            <button class="button primary" data-dec="release">♻ Release · +${num(price(card))}</button>
            ${card.can_replace ? `<button class="button secondary" data-dec="replace">🔁 Replace a player</button>` : ""}
          </div>
          <button class="pk-dec-later" data-dec="later">Decide later</button>
          ${card.can_replace ? "" : `<small class="pk-dec-note">A squad can't hold the same card twice, so a duplicate can only be released.</small>`}
        </div>`);
        countUp(stage.querySelector("[data-count]"));
      }

      function stepPick() {
        const card = queue[0];
        const rows = squad.map((p) => `<button class="pk-dec-row" data-dec="pick" data-pid="${Number(p.squad_player_id)}">
            <span class="pk-dec-row-main"><b>${nameOf(p)}</b><small>${esc(p.role || "")} · ${num(p.overall)} OVR</small></span>
            <span class="pk-dec-row-price">+${num(p.sell_price)}</span></button>`).join("");
        swap(`<div class="pk-dec-pick">
          <span class="pk-eyebrow">REPLACE · CHOOSE A PLAYER TO SELL</span>
          <p class="pk-dec-reason"><b>${nameOf(card)}</b> (${num(card.overall || card.card_ovr)} OVR) takes the place of the player you pick. The sold player's value is paid to you in coins.</p>
          <div class="pk-dec-list">${rows || `<p class="pk-missing">No squad players found.</p>`}</div>
          <div class="pk-dec-actions"><button class="button secondary" data-dec="back">⬅ Back</button><button class="button primary" data-dec="release">♻ Release instead</button></div>
        </div>`);
      }

      function stepConfirm(target) {
        const card = queue[0];
        swap(`<div class="pk-dec-confirm">
          <span class="pk-eyebrow">CONFIRM REPLACEMENT</span>
          <div class="pk-dec-swap">
            <div class="pk-dec-out"><small>OUT · SOLD</small>${helpers.playerCard(target, true)}<b class="pk-dec-plus">+${num(target.sell_price)} coins</b></div>
            <div class="pk-dec-arrow">⇄</div>
            <div class="pk-dec-in-card"><small>IN</small>${helpers.playerCard(card, true)}</div>
          </div>
          <p class="pk-dec-reason">Replace <b>${nameOf(target)}</b> with <b>${nameOf(card)}</b>?</p>
          <div class="pk-dec-actions"><button class="button primary" data-dec="yes">✅ Yes, Replace</button><button class="button secondary" data-dec="no">❌ Cancel · Release</button></div>
          <button class="pk-dec-later" data-dec="back2">⬅ Choose another player</button>
        </div>`);
      }

      async function celebrate(text, coins) {
        swap(`<div class="pk-dec-done"><div class="pk-dec-burst">✓</div><h3>${esc(text)}</h3><div class="pk-dec-coins big"><span>🪙</span> +<b data-count="${coins}">0</b> coins</div></div>`);
        countUp(stage.querySelector("[data-count]"));
        helpers.ping && helpers.ping("medium");
        await wait(1100);
      }

      async function act(kind, target, btn) {
        const card = queue[0];
        busy(btn, true);
        try {
          const res = kind === "release" ? await helpers.release(card.card_id) : await helpers.replace(card.card_id, target.squad_player_id);
          if (res.status !== "success") throw Object.assign(new Error(res.detail || res.status || "Failed"), { payload: res });
          changed = true; balance = res.balance;
          if (kind === "replace") {
            const i = squad.findIndex((p) => Number(p.squad_player_id) === Number(target.squad_player_id));
            if (i >= 0) squad[i] = { ...card, squad_player_id: Number(card.player_id || card.entity_id || 0) };
          }
          queue.shift();
          await celebrate(kind === "release" ? `${card.name || "Player"} released` : `${target.name || "Player"} replaced`, Number(res.coins || res.sell_price || 0));
          stepCard();
        } catch (error) {
          busy(btn, false);
          helpers.toast && helpers.toast(error.message || "Could not complete that. Nothing was changed.", true);
          if (error.status === 409) { queue.shift(); changed = true; stepCard(); }
        }
      }

      function countUp(el) {
        if (!el) return;
        const to = Number(el.dataset.count || 0), t0 = performance.now(), dur = 700;
        const tick = (now) => { const k = Math.min(1, (now - t0) / dur); el.textContent = num(Math.round(to * (1 - Math.pow(1 - k, 3)))); if (k < 1) requestAnimationFrame(tick); };
        requestAnimationFrame(tick);
      }

      let pickedTarget = null;
      stage.addEventListener("click", (e) => {
        const btn = e.target.closest("[data-dec]");
        if (!btn || btn.disabled) return;
        const what = btn.dataset.dec;
        if (what === "release" || what === "no") act("release", null, btn);
        else if (what === "replace") stepPick();
        else if (what === "back") stepCard();
        else if (what === "back2") stepPick();
        else if (what === "pick") {
          pickedTarget = squad.find((p) => Number(p.squad_player_id) === Number(btn.dataset.pid));
          if (pickedTarget) stepConfirm(pickedTarget);
        } else if (what === "yes" && pickedTarget) act("replace", pickedTarget, btn);
        else if (what === "later") finish();
      });
      stepCard();
    });
  }

  window.CrickiumPacks = { art, begin, tier, decide };
})();
