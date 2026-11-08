/* Crickium My Gifts UI + Diwali reveal. */
(() => {
  "use strict";

  let active = null;
  let animationFrame = 0;
  let particles = [];
  let canvas = null;
  let ctx = null;
  let lastBurst = 0;
  let audioContext = null;
  let soundTimer = null;
  let soundOn = true;
  let resizeHandler = null;

  const app = () => window.CrickiumApp;
  const esc = (value) => app()?.esc ? app().esc(value) : String(value ?? "").replace(/[&<>"']/g, ch => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch]));
  const num = (value) => app()?.number ? app().number(value) : Number(value || 0).toLocaleString("en-IN");

  async function load() {
    const A = app();
    if (!A) return;
    A.shell(`<section class="gifts-loading"><span class="gift-spinner"></span><b>Opening your gift vault…</b></section>`, "My Gifts", "REWARDS VAULT");
    try {
      const data = await A.api("/api/gifts");
      A.updateGiftBadge?.(Number(data.unclaimed || 0));
      renderList(data);
    } catch (error) {
      A.shell(`<section class="gift-empty"><div class="gift-empty-icon">🎁</div><h2>Gift vault unavailable</h2><p>${esc(error.message || "Please try again.")}</p><button class="button secondary" data-gift-action="reload">Try again</button></section>`, "My Gifts", "REWARDS VAULT");
    }
  }

  function rewardChips(item) {
    const parts = [];
    if (item.has_coins) parts.push(`<span class="gift-chip coin">🪙 Coins</span>`);
    if (item.has_rubies) parts.push(`<span class="gift-chip ruby">💎 Rubies</span>`);
    if (item.has_player) parts.push(`<span class="gift-chip player">🏏 Player Card</span>`);
    if (item.has_pack) parts.push(`<span class="gift-chip pack">📦 Pack</span>`);
    return parts.join("") || `<span class="gift-chip">🎁 Surprise reward</span>`;
  }

  function renderList(data) {
    const A = app();
    const items = data.items || [];
    const cards = items.map(item => {
      const isClaimed = item.status === "claimed";
      const banner = item.image_url ? `<div class="gift-banner">${A.image(item.image_url, item.title || "Gift banner", "gift-banner-image")}</div>` : "";
      const status = isClaimed ? `<span class="gift-status claimed">✓ CLAIMED</span>` : `<span class="gift-status available">● AVAILABLE</span>`;
      const button = isClaimed ? `<button class="button secondary" disabled>Reward claimed</button>` : `<button class="button primary gift-open-button" data-gift-action="open" data-gift-id="${Number(item.gift_id)}">${item.status === "opened" ? "Continue Opening" : "Open My Gift"}<span>↗</span></button>`;
      return `<article class="gift-list-card ${item.theme === "diwali" ? "is-diwali" : ""} ${isClaimed ? "is-claimed" : ""}">
        ${banner}
        <div class="gift-card-top"><span class="gift-card-emblem">${item.theme === "diwali" ? "🪔" : "🎁"}</span><div class="gift-card-heading"><span class="gift-eyebrow">${item.theme === "diwali" ? "FESTIVAL SPECIAL" : "CRICKIUM REWARD"}</span><h2>${esc(item.title || "A Gift for You")}</h2></div>${status}</div>
        <div class="gift-chips">${rewardChips(item)}</div>
        <div class="gift-card-footer"><span>${item.theme === "diwali" ? "A little festival surprise is waiting." : "A reward is waiting inside."}</span>${button}</div>
      </article>`;
    }).join("");
    const content = `<section class="gifts-hero"><div class="gifts-hero-orb"></div><div class="gifts-hero-copy"><span class="gift-eyebrow">YOUR PERSONAL REWARD VAULT</span><h2>Good things<br><em>come wrapped.</em></h2><p>Special drops from Crickium appear here. Open a gift to reveal what’s yours.</p></div><div class="gifts-hero-box">🎁<i>✦</i></div></section>
      <div class="gift-section-heading"><div><span class="gift-eyebrow">YOUR COLLECTION</span><h2>Available Gifts</h2></div><span class="gift-count">${Number(data.unclaimed || 0)} waiting</span></div>
      ${cards || `<section class="gift-empty"><div class="gift-empty-icon">🎁</div><h2>No gifts just yet</h2><p>When a new reward drop arrives, it will appear here. Keep your cricket spirit ready.</p></section>`}`;
    A.shell(content, "My Gifts", "REWARDS VAULT");
    A.hydrateImages();
  }

  async function openGift(giftId) {
    const A = app();
    if (!A || !giftId) return;
    // Create/resume Web Audio synchronously from the user's tap. Some mobile
    // browsers reject audio contexts first created after the network request.
    startSound();
    const button = document.querySelector(`[data-gift-action="open"][data-gift-id="${Number(giftId)}"]`);
    if (button) { button.disabled = true; button.textContent = "Opening…"; }
    try {
      const result = await A.api("/api/gifts/open", { method: "POST", body: JSON.stringify({ gift_id: Number(giftId) }) });
      if (result.status !== "success") throw new Error(result.detail || result.status || "This gift could not be opened.");
      active = { ...result, gift_id: Number(giftId), index: 0, revealed: false, error: "" };
      startFireworks();
      renderReveal();
    } catch (error) {
      A.toast(error.message || "This gift could not be opened.", true);
      if (button) { button.disabled = false; button.innerHTML = "Open My Gift <span>↗</span>"; }
    }
  }

  function rewardStages() {
    const reward = active?.reward || {};
    const stages = [];
    if (Number(reward.coins || 0) > 0) stages.push({ kind: "coins", title: "Coins Reveal", eyebrow: "FIRST REVEAL", icon: "🪙", label: `${num(reward.coins)} Coins`, description: "A fresh boost for your Crickium wallet." });
    if (Number(reward.rubies || 0) > 0) stages.push({ kind: "rubies", title: "Rubies Reveal", eyebrow: "SECOND REVEAL", icon: "💎", label: `${num(reward.rubies)} Rubies`, description: "A little extra sparkle for your club." });
    if (reward.player) stages.push({ kind: "player", title: "Player Card Reveal", eyebrow: "THIRD REVEAL", icon: "🏏", label: reward.player.name || "Player Card", description: `OVR ${Number(reward.player.overall || 0)} · ${reward.player.role || "Cricketer"}` });
    // With a player reward, keep the player card as the final main reveal and
    // show the optional pack as a bonus on that same card. A pack-only bonus
    // still gets its own final reveal for coins/rubies-only campaigns.
    if (reward.pack_key && !reward.player) stages.push({ kind: "pack", title: "Pack Reveal", eyebrow: "BONUS REVEAL", icon: "📦", label: `${String(reward.pack_key).toUpperCase()} Pack`, description: "Added to your Pack Shop inventory when claimed." });
    if (!stages.length) stages.push({ kind: "surprise", title: "Your Surprise", eyebrow: "GIFT REVEAL", icon: "🎁", label: "A Crickium Gift", description: "A special reward has been prepared for you." });
    return stages;
  }

  function playerMarkup(player) {
    if (!player) return "";
    const image = player.card_image_url ? app().image(player.card_image_url, player.name || "Player card", "gift-player-image") : "";
    const packBonus = active?.reward?.pack_key ? `<div class="gift-player-bonus">📦 BONUS · ${esc(String(active.reward.pack_key).toUpperCase())} PACK</div>` : "";
    return `<div class="gift-player-card"><div class="gift-player-art">${image}<span class="gift-player-ovr">${Number(player.overall || 0)}<small>OVR</small></span></div><div class="gift-player-meta"><b>${esc(player.name || "Player")}</b><span>${esc(player.country || "CRICKIUM")}</span><small>${esc(player.role || "Cricketer")}</small>${packBonus}</div></div>`;
  }

  function renderReveal() {
    if (!active) return;
    const A = app();
    const stageList = rewardStages();
    const stage = stageList[Math.min(active.index, stageList.length - 1)];
    const isLast = active.index >= stageList.length - 1;
    const diwali = active.theme === "diwali";
    const revealContent = active.revealed ? (stage.kind === "player" ? playerMarkup(active.reward.player) : `<div class="gift-reward-orb gift-${stage.kind}"><span>${stage.icon}</span><b>${esc(stage.label)}</b><small>${esc(stage.description)}</small></div>`) : `<div class="gift-sealed-reward"><div class="gift-seal">✦</div><div class="gift-mystery-box">${diwali ? "🪔" : "🎁"}</div><b>Something special is inside</b><span>Tap below to reveal your reward</span></div>`;
    const action = active.revealed
      ? (isLast ? `<button class="button primary gift-claim-button" data-gift-action="claim">Claim Reward <span>✦</span></button>` : `<button class="button primary gift-next-button" data-gift-action="next">Reveal Next Gift <span>↗</span></button>`)
      : `<button class="button primary gift-reveal-button" data-gift-action="reveal">Tap to Reveal <span>✦</span></button>`;
    const title = diwali ? "Happy Diwali" : (active.title || "A Gift for You");
    const overlay = document.createElement("div");
    overlay.className = `gift-reveal-overlay ${diwali ? "diwali-mode" : "normal-mode"}`;
    const banner = active.image_url ? `<div class="gift-reveal-banner">${A.image(active.image_url, `${title} banner`, "gift-reveal-banner-image")}</div>` : "";
    overlay.innerHTML = `<canvas class="gift-fireworks" aria-hidden="true"></canvas><div class="gift-reveal-glow"></div><section class="gift-reveal-dialog">
      <div class="gift-celebration-brand"><span>✦</span> CRICKIUM PRESENTS <span>✦</span></div>
      <h1 class="gift-reveal-title">${esc(title)}</h1>
      <p class="gift-reveal-subtitle">${diwali ? "May your club shine bright this festival." : "A special reward, picked just for your club."}</p>
      ${banner}
      <div class="gift-progress"><span>${stage.eyebrow}</span><b>${active.index + 1} / ${stageList.length}</b></div>
      <div class="gift-reveal-stage ${active.revealed ? "is-revealed" : ""}"><div class="gift-stage-ring"></div>${revealContent}</div>
      ${active.error ? `<p class="gift-claim-error">${esc(active.error)}</p>` : ""}
      <div class="gift-reveal-action">${action}</div>
      <button class="gift-sound-toggle" data-gift-action="sound">${soundOn ? "🔊 Sound On" : "🔇 Sound Off"}</button>
      <p class="gift-reveal-footnote">Your reward is safely saved. Claim it to add it to your account.</p>
    </section>`;
    document.querySelector(".gift-reveal-overlay")?.remove();
    document.body.append(overlay);
    canvas = overlay.querySelector("canvas");
    ctx = canvas?.getContext("2d") || null;
    resizeCanvas();
    if (!animationFrame) animationFrame = requestAnimationFrame(drawFireworks);
    A.hydrateImages(overlay);
  }

  function resizeCanvas() {
    if (!canvas || !ctx) return;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const width = window.innerWidth;
    const height = window.innerHeight;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  function burst(x, y) {
    const count = 34 + Math.floor(Math.random() * 25);
    const hue = [35, 45, 8, 330, 275, 55][Math.floor(Math.random() * 6)];
    for (let i = 0; i < count; i++) {
      const angle = Math.PI * 2 * (i / count) + Math.random() * .08;
      const speed = 1.2 + Math.random() * 3.8;
      particles.push({ x, y, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed, life: 55 + Math.random() * 35, max: 90, size: 1 + Math.random() * 2.5, hue, alpha: .95 });
    }
  }

  function drawFireworks(now) {
    if (!ctx || !canvas || !document.body.contains(canvas)) { animationFrame = 0; return; }
    const width = window.innerWidth, height = window.innerHeight;
    ctx.clearRect(0, 0, width, height);
    if (now - lastBurst > 1050 + Math.random() * 650) {
      burst(width * (.13 + Math.random() * .74), height * (.10 + Math.random() * .42));
      lastBurst = now;
      if (soundOn) playPop();
    }
    for (let i = particles.length - 1; i >= 0; i--) {
      const p = particles[i];
      p.x += p.vx; p.y += p.vy; p.vy += .035; p.vx *= .991; p.vy *= .991; p.life -= 1;
      p.alpha = Math.max(0, Math.min(1, p.life / 38));
      if (p.life <= 0) { particles.splice(i, 1); continue; }
      ctx.beginPath();
      ctx.fillStyle = `hsla(${p.hue}, 96%, ${62 + Math.random() * 18}%, ${p.alpha})`;
      ctx.shadowBlur = 12; ctx.shadowColor = `hsla(${p.hue}, 100%, 64%, .8)`;
      ctx.arc(p.x, p.y, p.size * p.alpha, 0, Math.PI * 2); ctx.fill();
    }
    ctx.shadowBlur = 0;
    animationFrame = requestAnimationFrame(drawFireworks);
  }

  function startFireworks() {
    particles = [];
    lastBurst = 0;
    if (resizeHandler) window.removeEventListener("resize", resizeHandler);
    resizeHandler = resizeCanvas;
    window.addEventListener("resize", resizeHandler, { passive: true });
  }

  function startSound() {
    try {
      const Audio = window.AudioContext || window.webkitAudioContext;
      if (!Audio) { soundOn = false; return; }
      if (!audioContext) audioContext = new Audio();
      audioContext.resume?.();
    } catch (_) { soundOn = false; }
  }

  function playPop() {
    if (!soundOn || !audioContext) return;
    try {
      const osc = audioContext.createOscillator();
      const gain = audioContext.createGain();
      const now = audioContext.currentTime;
      osc.type = "triangle";
      osc.frequency.setValueAtTime(250 + Math.random() * 180, now);
      osc.frequency.exponentialRampToValueAtTime(58, now + .24);
      gain.gain.setValueAtTime(.0001, now);
      gain.gain.exponentialRampToValueAtTime(.045, now + .025);
      gain.gain.exponentialRampToValueAtTime(.0001, now + .27);
      osc.connect(gain); gain.connect(audioContext.destination);
      osc.start(now); osc.stop(now + .29);
    } catch (_) {}
  }

  function stop() {
    if (animationFrame) cancelAnimationFrame(animationFrame);
    animationFrame = 0;
    particles = [];
    if (resizeHandler) window.removeEventListener("resize", resizeHandler);
    resizeHandler = null;
    canvas = null; ctx = null;
    document.querySelector(".gift-reveal-overlay")?.remove();
    if (soundTimer) clearInterval(soundTimer);
    soundTimer = null;
    active = null;
  }

  async function claimReward() {
    if (!active) return;
    const A = app();
    const button = document.querySelector("[data-gift-action='claim']");
    if (button) { button.disabled = true; button.textContent = "Claiming…"; }
    try {
      const result = await A.api("/api/gifts/claim", { method: "POST", body: JSON.stringify({ gift_id: active.gift_id }) });
      if (result.status !== "success") throw new Error(result.detail || result.status || "Reward could not be claimed.");
      A.ping("heavy");
      A.state.home && (A.state.home.gifts_unclaimed = Math.max(0, Number(A.state.home.gifts_unclaimed || 1) - 1));
      stop();
      A.toast("Your gift has been added to your account.");
      await load();
    } catch (error) {
      if (active) {
        const status = String(error.payload?.status || error.message || "").toLowerCase();
        active.error = status === "squad_full"
          ? "Your squad is full. Free a slot, then return to My Gifts and continue opening."
          : status === "already_owned"
            ? "This player is already in your squad. The rest of your gift is still safe. Please contact support before retrying."
            : error.message || "Please try again.";
      }
      renderReveal();
    }
  }

  document.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-gift-action]");
    if (!button) return;
    const action = button.dataset.giftAction;
    if (action === "reload") { load(); return; }
    if (action === "open") { openGift(button.dataset.giftId); return; }
    if (action === "reveal") {
      if (!active) return;
      active.revealed = true; active.error = ""; app()?.ping("medium"); playPop(); renderReveal(); return;
    }
    if (action === "next") {
      if (!active) return;
      active.index += 1; active.revealed = false; active.error = ""; app()?.ping("medium"); renderReveal(); return;
    }
    if (action === "claim") { claimReward(); return; }
    if (action === "sound") {
      soundOn = !soundOn;
      if (soundOn) startSound();
      renderReveal();
    }
  });

  window.CrickiumGifts = { load, stop };
})();
