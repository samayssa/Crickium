/*
 * Crickium Kit Bag opening animation, intentionally isolated from app_v2.js.
 * This module is presentation-only. The existing backend still decides and
 * records rewards; this module never grants a Kit Bag or changes probabilities.
 */
(() => {
  "use strict";

  const ART_URL = "/static/kitbag-crickium.svg?v=kitbag-animation-20261012-1";
  const OPENING_MS = 3020;
  let artPromise = null;
  let activeOverlay = null;
  let activeTimer = null;

  function loadBagArt() {
    if (!artPromise) {
      artPromise = fetch(ART_URL, { cache: "force-cache" })
        .then((response) => {
          if (!response.ok) throw new Error(`Kit Bag artwork request failed (${response.status})`);
          return response.text();
        })
        .then((markup) => {
          const svg = markup.match(/<svg[\s\S]*<\/svg>/i)?.[0];
          if (!svg) throw new Error("Kit Bag SVG is invalid");
          return svg;
        })
        .catch((error) => {
          artPromise = null;
          throw error;
        });
    }
    return artPromise;
  }

  function safelyRemoveOverlay(overlay) {
    if (activeTimer) window.clearTimeout(activeTimer);
    activeTimer = null;
    if (overlay?.isConnected) overlay.remove();
    if (activeOverlay === overlay) activeOverlay = null;
  }

  function playOpen(options = {}) {
    // Do not let a double tap start overlapping animation overlays.
    if (activeOverlay) safelyRemoveOverlay(activeOverlay);
    return new Promise((resolve) => {
      let settled = false;
      let overlay;
      const finish = (skip = false) => {
        if (settled) return;
        settled = true;
        if (activeTimer) window.clearTimeout(activeTimer);
        activeTimer = null;
        if (overlay?.isConnected) {
          if (skip) {
            overlay.classList.add("is-revealed");
            overlay.classList.remove("is-shaking", "is-opening");
            const subtitle = overlay.querySelector(".kitbag-opening-subtitle");
            if (subtitle) subtitle.textContent = "PLAYER UNLOCKED";
            window.setTimeout(() => {
              overlay.classList.add("is-leaving");
              window.setTimeout(() => safelyRemoveOverlay(overlay), 270);
            }, 220);
          } else {
            overlay.classList.add("is-leaving");
            window.setTimeout(() => safelyRemoveOverlay(overlay), 270);
          }
        }
        resolve();
      };

      const mount = async () => {
        let bagArt;
        try {
          bagArt = await loadBagArt();
        } catch (error) {
          console.warn("[Crickium] Kit Bag artwork could not load; continuing to the player card.", error?.message || error);
          resolve();
          return;
        }

        if (settled) return;
        overlay = document.createElement("div");
        overlay.className = "kitbag-opening-overlay is-shaking";
        overlay.setAttribute("role", "dialog");
        overlay.setAttribute("aria-modal", "true");
        overlay.setAttribute("aria-label", "Opening Crickium Kit Bag");
        overlay.innerHTML = `
          <div class="kitbag-opening-dialog">
            <div class="kitbag-opening-brand">CRICKIUM PLAYER DROP</div>
            <h2 class="kitbag-opening-title">Your Kit Bag is opening</h2>
            <p class="kitbag-opening-subtitle">A new player is inside. Let's see who you got…</p>
            <div class="kitbag-opening-stage" aria-live="polite">
              <div class="kitbag-opening-halo"></div>
              <i class="kitbag-opening-particle"></i><i class="kitbag-opening-particle"></i>
              <i class="kitbag-opening-particle"></i><i class="kitbag-opening-particle"></i>
              <div class="kitbag-opening-card">${options.cardMarkup || ""}</div>
              <div class="kitbag-opening-bag">${bagArt}</div>
            </div>
            <div class="kitbag-opening-actions"><button type="button" class="kitbag-opening-skip" data-kitbag-skip>Skip animation</button></div>
          </div>`;

        // SVG markup is from our own static asset. The player card markup is
        // produced by the app's existing escaped playerCard() renderer.
        document.body.appendChild(overlay);
        activeOverlay = overlay;
        overlay.addEventListener("click", (event) => {
          const skip = event.target.closest("[data-kitbag-skip]");
          if (skip) {
            event.preventDefault();
            event.stopPropagation();
            finish(true);
          }
        });
        if (typeof options.hydrateImages === "function") {
          Promise.resolve(options.hydrateImages(overlay)).catch((error) => {
            console.debug("[Crickium] Player card image hydration skipped during Kit Bag animation.", error?.message || error);
          });
        }

        // Shake first, then open the flap and lift the actual selected player card.
        window.setTimeout(() => {
          if (settled || !overlay?.isConnected) return;
          overlay.classList.remove("is-shaking");
          overlay.classList.add("is-opening");
          const subtitle = overlay.querySelector(".kitbag-opening-subtitle");
          if (subtitle) subtitle.textContent = "Unzipping your Kit Bag…";
        }, 780);

        window.setTimeout(() => {
          if (settled || !overlay?.isConnected) return;
          overlay.classList.add("is-revealed");
          const title = overlay.querySelector(".kitbag-opening-title");
          const subtitle = overlay.querySelector(".kitbag-opening-subtitle");
          if (title) title.textContent = options.playerName ? `${options.playerName}!` : "Player unlocked!";
          if (subtitle) subtitle.textContent = "Your player card is ready";
        }, 2280);
        activeTimer = window.setTimeout(() => finish(false), OPENING_MS);
      };

      // Fail-safe: cosmetic animation errors must never prevent the server-
      // confirmed reward from being shown in the normal reveal page.
      mount().catch((error) => {
        console.warn("[Crickium] Kit Bag animation failed safely.", error?.message || error);
        safelyRemoveOverlay(overlay);
        if (!settled) {
          settled = true;
          resolve();
        }
      });
    });
  }

  window.CrickiumKitBagAnimation = Object.freeze({ playOpen });
})();
