/* Gifs Against Humanity — the TV / spectator view.
 *
 * Read-only: it sends no actions and receives the same public state any spectator gets,
 * so it cannot leak who played what. It just makes the room's shared moment big.
 *
 * DOM contract: ids tv, tv-code, tv-scores, tv-round, tv-target, tv-main, confetti,
 * plus the persistent nodes built below (tv-timer, tv-stage, tv-cards).
 */
(function () {
  'use strict';

  const root = GN.el('tv');
  const CODE = root.dataset.code;
  const GIF_BASE = root.dataset.gifBase;
  const HOME_URL = root.dataset.homeUrl;

  const main = GN.el('tv-main');
  main.innerHTML =
    '<div class="tvtimer" id="tv-timer" hidden><span class="tvtimer__fill" id="tv-timer-fill"></span><span class="tvtimer__label" id="tv-timer-label"></span></div>' +
    '<div class="tvstage" id="tv-stage"></div>' +
    '<div class="tvcards" id="tv-cards" hidden></div>';

  const stage = GN.el('tv-stage');
  const cardsEl = GN.el('tv-cards');
  const scoresEl = GN.el('tv-scores');

  let state = null;
  let lastStage = null;
  let cardsKey = null;
  let rosterKey = null;
  let prevScores = {};
  let lastPhase = null;
  let lastWinnerSlot = null;

  const conn = new GN.Connection({
    joinPayload: function () {
      return { event: 'join_as_watcher', data: { code: CODE, tv: true } };
    },
    onState: function (next) {
      state = next;
      // Debug aid, mirroring the phone client: inspect `__tvState` in devtools. This is
      // the spectator view, so it has no hands and no unrevealed cards in it.
      window.__tvState = next;
      render();
    },
    onGone: function () {
      state = null;
      setStage(
        '<section class="tvpanel">' +
          '<h1 class="tvpanel__title">Game over</h1>' +
          '<p class="tvpanel__sub">This room has closed. Start a new one at ' + HOME_URL + '</p>' +
        '</section>'
      );
      cardsEl.hidden = true;
    },
    onStatus: function (up) {
      root.classList.toggle('tv--offline', !up);
    },
  });

  const countdown = new GN.Countdown(conn, GN.el('tv-timer'), GN.el('tv-timer-fill'), GN.el('tv-timer-label'));

  // --- helpers ---------------------------------------------------------------
  function esc(v) { return GN.esc(v); }

  function gifUrl(gif) { return GIF_BASE + gif.file; }

  function judge() {
    return state.players.filter(function (p) { return p.is_judge; })[0] || null;
  }

  function judgeLabel() {
    const j = judge();
    return j ? esc(j.avatar) + ' ' + esc(j.nickname) : 'the judge';
  }

  function promptHtml(prompt) {
    if (!prompt) return '';
    return esc(prompt.text).replace(/_{2,}/g, '<span class="prompt__blank"></span>');
  }

  function avatarHtml(p, extra) {
    return '<span class="avatar avatar--' + esc(p.color) + (p.connected ? '' : ' avatar--away') +
      (extra ? ' ' + extra : '') + '">' + esc(p.avatar) + '</span>';
  }

  function setStage(html) {
    if (html !== lastStage) {
      stage.innerHTML = html;
      lastStage = html;
    }
  }

  // --- scoreboard ------------------------------------------------------------
  function renderScores() {
    const target = state.options.target_score;
    const key = state.players.map(function (p) { return p.pid; }).join(',') + '|' + target;
    if (rosterKey !== key) {
      scoresEl.innerHTML = state.players
        .map(function (p) {
          let pips = '';
          for (let i = 0; i < target; i++) pips += '<span class="pip" data-i="' + i + '"></span>';
          return (
            '<li class="tvscore" data-pid="' + esc(p.pid) + '">' +
              avatarHtml(p) +
              '<span class="tvscore__name">' + esc(p.nickname) + '</span>' +
              '<span class="pips">' + pips + '</span>' +
              '<span class="tvscore__num">0</span>' +
              '<span class="tvscore__flag"></span>' +
            '</li>'
          );
        })
        .join('');
      rosterKey = key;
      prevScores = {};
    }

    state.players.forEach(function (p) {
      const row = scoresEl.querySelector('.tvscore[data-pid="' + p.pid + '"]');
      if (!row) return;
      row.classList.toggle('tvscore--judge', p.is_judge);
      row.classList.toggle('tvscore--away', !p.connected);
      row.classList.toggle('tvscore--champ', p.pid === state.champion_pid);
      row.querySelector('.tvscore__num').textContent = p.score;
      Array.prototype.forEach.call(row.querySelectorAll('.pip'), function (pip, i) {
        pip.classList.toggle('pip--on', i < p.score);
      });
      const flag = row.querySelector('.tvscore__flag');
      flag.textContent = state.phase === 'SUBMIT' && p.is_answering ? (p.has_submitted ? '✅' : '⏳') : '';

      // Pop the row when a point lands.
      const before = prevScores[p.pid];
      if (before !== undefined && p.score > before) {
        row.classList.remove('tvscore--gain');
        void row.offsetWidth; // restart the animation
        row.classList.add('tvscore--gain');
      }
      prevScores[p.pid] = p.score;
    });

    GN.el('tv-round').textContent = state.phase === 'LOBBY' ? 'Lobby' : 'Round ' + state.round;
    GN.el('tv-target').textContent = 'First to ' + state.options.target_score;
  }

  // --- the pile of cards -----------------------------------------------------
  function renderCards(mode) {
    // mode: 'pile' (face-down, count only) | 'table' (flip as revealed) | null
    if (!mode) {
      cardsEl.hidden = true;
      cardsEl.innerHTML = '';
      cardsKey = null;
      return;
    }
    cardsEl.hidden = false;

    if (mode === 'pile') {
      const count = state.submitted_count;
      const expected = state.expected_count;
      const key = 'pile:' + count + ':' + expected;
      if (cardsKey !== key) {
        let html = '';
        for (let i = 0; i < expected; i++) {
          html += '<div class="tvcard tvcard--slot' + (i < count ? ' tvcard--in' : ' tvcard--empty') + '">' +
            '<span class="tvcard__inner"><span class="tvcard__back"><span class="tvcard__mark">GIF</span></span></span>' +
          '</div>';
        }
        cardsEl.innerHTML = html;
        cardsKey = key;
      }
      cardsEl.dataset.count = expected;
      return;
    }

    const cards = state.cards || [];
    // Keyed by round as well as count: two rounds with the same number of answers would
    // otherwise reuse last round's card elements, and their pictures with them.
    const key = 'table:' + state.round + ':' + cards.length;
    if (cardsKey !== key) {
      cardsEl.innerHTML = cards
        .map(function (c) {
          return (
            '<div class="tvcard" data-slot="' + c.slot + '">' +
              '<span class="tvcard__inner">' +
                '<span class="tvcard__back"><span class="tvcard__mark">GIF</span></span>' +
                '<span class="tvcard__front"></span>' +
              '</span>' +
              '<span class="tvcard__author"></span>' +
            '</div>'
          );
        })
        .join('');
      cardsKey = key;
    }
    cardsEl.dataset.count = cards.length;

    cards.forEach(function (c) {
      const node = cardsEl.querySelector('.tvcard[data-slot="' + c.slot + '"]');
      if (!node) return;
      node.classList.toggle('tvcard--up', !!c.revealed);
      node.classList.toggle('tvcard--winner', !!c.is_winner);
      node.classList.toggle('tvcard--dimmed', state.round_winner_slot !== null && !c.is_winner);
      // Always make the picture match the payload, whatever was there before.
      const front = node.querySelector('.tvcard__front');
      let img = front.querySelector('img');
      if (c.revealed && c.gif) {
        if (!img) {
          img = document.createElement('img');
          img.className = 'tvcard__img';
          img.alt = '';
          front.appendChild(img);
        }
        if (img.dataset.gif !== c.gif.id) {
          img.dataset.gif = c.gif.id;
          img.src = gifUrl(c.gif);
        }
        // Take the GIF's own shape rather than cropping it into a 4:3 slot.
        if (c.gif.w && c.gif.h) node.style.setProperty('--card-ar', c.gif.w + ' / ' + c.gif.h);
        else node.style.removeProperty('--card-ar');
      } else if (img) {
        img.remove();
        node.style.removeProperty('--card-ar');
      }
      const author = node.querySelector('.tvcard__author');
      author.innerHTML = c.author ? avatarHtml(c.author) + '<b>' + esc(c.author.nickname) + '</b>' : '';
    });
  }

  // --- panels ----------------------------------------------------------------
  function lobbyStage() {
    const count = state.players.length;
    return (
      '<section class="tvpanel tvpanel--lobby">' +
        '<p class="tvpanel__kicker">Join on your phone</p>' +
        '<p class="tvjoin">Type code <span class="tvjoin__code">' + esc(state.code) + '</span></p>' +
        '<h1 class="tvpanel__title">' + count + ' / ' + state.max_players + ' in the room</h1>' +
        '<ul class="tvlobby">' +
          state.players
            .map(function (p) {
              return '<li class="tvlobby__player' + (p.connected ? '' : ' tvlobby__player--away') + '">' +
                avatarHtml(p, 'avatar--big') +
                '<span class="tvlobby__name">' + esc(p.nickname) + '</span>' +
                (p.is_host ? '<span class="tag tag--host">host</span>' : '') +
              '</li>';
            })
            .join('') +
        '</ul>' +
        '<p class="tvpanel__sub">' +
          (state.can_start ? 'Waiting for the host to start…' : 'Need ' + (state.min_players - count) + ' more to start') +
        '</p>' +
      '</section>'
    );
  }

  function waitingStage(title, sub) {
    return (
      '<section class="tvpanel">' +
        '<h1 class="tvpanel__title">' + title + '</h1>' +
        (sub ? '<p class="tvpanel__sub">' + sub + '</p>' : '') +
      '</section>'
    );
  }

  function promptStage(extra) {
    return (
      '<section class="tvpanel tvpanel--prompt">' +
        '<p class="tvpanel__kicker">👑 ' + judgeLabel() + ' is judging</p>' +
        '<p class="prompt prompt--tv">' + promptHtml(state.prompt) + '</p>' +
        (extra || '') +
      '</section>'
    );
  }

  function waitingChips() {
    const waiting = state.waiting_on || [];
    if (!waiting.length) return '<p class="tvwait tvwait--done">All answers in!</p>';
    return (
      '<p class="tvwait">Waiting on ' +
        waiting
          .map(function (w) {
            return '<span class="tvchip' + (w.connected ? '' : ' tvchip--away') + '">' + esc(w.avatar) + ' ' + esc(w.nickname) + '</span>';
          })
          .join(' ') +
      '</p>'
    );
  }

  function resultStage() {
    const winner = state.players.filter(function (p) { return p.pid === state.round_winner_pid; })[0];
    if (!winner) return waitingStage('Round over');
    return (
      '<section class="tvpanel tvpanel--result">' +
        '<p class="prompt prompt--tvsmall">' + promptHtml(state.prompt) + '</p>' +
        '<h1 class="tvpanel__title tvpanel__title--win">' +
          avatarHtml(winner, 'avatar--big') + esc(winner.nickname) + ' takes the round!' +
        '</h1>' +
        '<p class="tvpanel__sub">' + winner.score + ' / ' + state.options.target_score + ' points</p>' +
      '</section>'
    );
  }

  function gameOverStage() {
    const champ = state.players.filter(function (p) { return p.pid === state.champion_pid; })[0];
    const board = state.players
      .slice()
      .sort(function (a, b) { return b.score - a.score; })
      .map(function (p, i) {
        return (
          '<li class="tvfinal' + (p.pid === state.champion_pid ? ' tvfinal--champ' : '') + '">' +
            '<span class="tvfinal__rank">' + (i + 1) + '</span>' +
            avatarHtml(p, 'avatar--big') +
            '<span class="tvfinal__name">' + esc(p.nickname) + '</span>' +
            '<span class="tvfinal__score">' + p.score + '</span>' +
          '</li>'
        );
      })
      .join('');
    return (
      '<section class="tvpanel tvpanel--over">' +
        '<p class="tvpanel__kicker">🏆 Winner 🏆</p>' +
        '<h1 class="tvchamp">' + (champ ? avatarHtml(champ, 'avatar--huge') + esc(champ.nickname) : 'Nobody') + '</h1>' +
        '<ol class="tvfinals">' + board + '</ol>' +
      '</section>'
    );
  }

  // --- master render ---------------------------------------------------------
  function render() {
    if (!state) return;
    GN.el('tv-code').textContent = state.code;
    renderScores();

    const total = state.phase === 'PROMPT_PICK' ? state.options.prompt_seconds : state.options.submit_seconds;
    countdown.set(state.deadline_ts, total);

    switch (state.phase) {
      case 'LOBBY':
        setStage(lobbyStage());
        renderCards(null);
        break;

      case 'ROUND_READY':
        setStage(waitingStage('👑 ' + judgeLabel() + ' is judging round ' + state.round, 'Waiting for them to start…'));
        renderCards(null);
        break;

      case 'PROMPT_PICK':
        setStage(waitingStage(judgeLabel() + ' is picking a prompt', 'Three on the table — one gets judged.'));
        renderCards(null);
        break;

      case 'SUBMIT':
        setStage(promptStage(waitingChips()));
        renderCards('pile');
        break;

      case 'REVEAL':
        setStage(promptStage('<p class="tvwait">' + judgeLabel() + ' is flipping the answers…</p>'));
        renderCards('table');
        break;

      case 'PICK_WINNER':
        setStage(promptStage('<p class="tvwait tvwait--think">' + judgeLabel() + ' is deciding…</p>'));
        renderCards('table');
        break;

      case 'ROUND_RESULT':
        setStage(resultStage());
        renderCards('table');
        break;

      case 'GAME_OVER':
        setStage(gameOverStage());
        renderCards('table');
        break;
    }

    // Celebrate exactly once per win.
    if (state.round_winner_slot !== null && state.round_winner_slot !== lastWinnerSlot && state.phase !== 'SUBMIT') {
      lastWinnerSlot = state.round_winner_slot;
      confetti.burst(state.phase === 'GAME_OVER' ? 260 : 120);
    }
    if (state.phase !== lastPhase) {
      if (state.phase === 'GAME_OVER') confetti.burst(320);
      if (state.phase === 'ROUND_READY') lastWinnerSlot = null;
      lastPhase = state.phase;
    }
  }

  // --- confetti --------------------------------------------------------------
  const confetti = (function () {
    const canvas = GN.el('confetti');
    const ctx = canvas.getContext('2d');
    let bits = [];
    let running = false;

    function resize() {
      canvas.width = window.innerWidth;
      canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    function colors() {
      const style = getComputedStyle(document.documentElement);
      return ['--gn-accent-1', '--gn-accent-2', '--gn-accent-3', '--gn-accent-4']
        .map(function (name) { return style.getPropertyValue(name).trim() || '#fff'; });
    }

    function burst(count) {
      if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
      const palette = colors();
      for (let i = 0; i < count; i++) {
        bits.push({
          x: canvas.width * (0.15 + Math.random() * 0.7),
          y: canvas.height * 0.15 + Math.random() * canvas.height * 0.2,
          vx: (Math.random() - 0.5) * 9,
          vy: Math.random() * -7 - 2,
          w: 7 + Math.random() * 9,
          h: 4 + Math.random() * 7,
          spin: (Math.random() - 0.5) * 0.4,
          angle: Math.random() * Math.PI,
          color: palette[i % palette.length],
          life: 150 + Math.random() * 90,
        });
      }
      if (!running) {
        running = true;
        requestAnimationFrame(step);
      }
    }

    function step() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      bits = bits.filter(function (b) { return b.life > 0 && b.y < canvas.height + 40; });
      bits.forEach(function (b) {
        b.life -= 1;
        b.vy += 0.16;
        b.x += b.vx;
        b.y += b.vy;
        b.angle += b.spin;
        ctx.save();
        ctx.translate(b.x, b.y);
        ctx.rotate(b.angle);
        ctx.fillStyle = b.color;
        ctx.fillRect(-b.w / 2, -b.h / 2, b.w, b.h);
        ctx.restore();
      });
      if (bits.length) {
        requestAnimationFrame(step);
      } else {
        running = false;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    }

    return { burst: burst };
  })();
})();
