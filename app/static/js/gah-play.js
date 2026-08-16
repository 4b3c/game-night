/* Gifs Against Humanity — the phone client.
 *
 * The server is the only authority: this file renders whatever `state` arrives and turns
 * taps into events. It never decides a rule, and it never sees a card it shouldn't.
 *
 * DOM contract (a redesign may restyle freely, but these hooks must survive):
 *   ids       play, playbar, bar-code, bar-round, bar-judge, timer, timer-fill,
 *             timer-label, scorestrip, stage, panel, table, handarea, handarea-title,
 *             hand, toasts
 *   data-*    [data-action], [data-slot], [data-prompt], [data-pid], [data-opt], [data-gif]
 */
(function () {
  'use strict';

  const root = GN.el('play');
  const CODE = root.dataset.code;
  const GIF_BASE = root.dataset.gifBase;
  const TV_URL = root.dataset.tvUrl;
  const HOME_URL = root.dataset.homeUrl;
  const NICK_KEY = 'gn:nick:' + CODE;

  const stage = GN.el('stage');
  const playbar = GN.el('playbar');
  const scorestrip = GN.el('scorestrip');
  const handarea = GN.el('handarea');
  const handEl = GN.el('hand');
  const handTitle = GN.el('handarea-title');

  // Persistent nodes so flipping a card doesn't reload every other GIF.
  stage.innerHTML = '<div class="panel-host" id="panel"></div><div class="table" id="table" hidden></div>';
  const panel = GN.el('panel');
  const table = GN.el('table');

  let state = null;
  let nickname = sessionStorage.getItem(NICK_KEY) || '';
  let myPid = null;
  let joinError = '';
  let selected = null; // gif tapped, not yet confirmed
  let lastPanel = null;
  let lastStrip = null;
  let tableKey = null;
  let moreOpen = false; // the "More settings" disclosure, kept across re-renders

  const conn = new GN.Connection({
    joinPayload: function () {
      return nickname
        ? { event: 'join_as_player', data: { code: CODE, nickname: nickname } }
        : { event: 'join_as_watcher', data: { code: CODE } };
    },
    onState: function (next) {
      // Ignore a spectator view (no `you`) that arrives while we're still listed as a
      // player — it would blank out our hand and controls. Being genuinely removed from
      // the game takes us out of `players` too, so a real kick still lands.
      if (!next.you && myPid && (next.players || []).some(function (p) { return p.pid === myPid; })) {
        return;
      }
      state = next;
      // Debug aid: your own view, already in your browser — inspect it in devtools with
      // `__gnState`. Never contains another player's cards.
      window.__gnState = next;
      render();
    },
    onJoined: function (info) {
      nickname = info.nickname;
      myPid = info.pid;
      sessionStorage.setItem(NICK_KEY, nickname);
      joinError = '';
    },
    onRejected: function (info) {
      joinError = info.message || 'Could not join';
      nickname = '';
      myPid = null;
      sessionStorage.removeItem(NICK_KEY);
      render();
    },
    onGone: function () {
      dedicatedPanel(
        'That game is over',
        'Rooms disappear a few minutes after everyone leaves.'
      );
    },
    onKicked: function () {
      nickname = '';
      myPid = null;
      sessionStorage.removeItem(NICK_KEY);
      dedicatedPanel('The host removed you', 'Rude. You can still start a game of your own.');
    },
    onStatus: function (up) {
      root.classList.toggle('play--offline', !up);
    },
  });

  const countdown = new GN.Countdown(conn, GN.el('timer'), GN.el('timer-fill'), GN.el('timer-label'));

  // Debug aid: `__gnConn.socket.connected` in devtools tells you if the phone is live.
  window.__gnConn = conn;

  // --- helpers ---------------------------------------------------------------
  /** A final screen: no game state, nothing to render but a way out. */
  function dedicatedPanel(title, hint) {
    state = null;
    setPanel(
      '<section class="panel panel--dead">' +
        '<h1 class="panel__title">' + GN.esc(title) + '</h1>' +
        '<p class="panel__hint">' + GN.esc(hint) + '</p>' +
        '<a class="btn btn--big btn--go" href="' + HOME_URL + '">Back to Game Night</a>' +
      '</section>'
    );
    playbar.hidden = true;
    scorestrip.hidden = true;
    handarea.hidden = true;
    table.hidden = true;
  }

  function gifUrl(gif) {
    return GIF_BASE + gif.file;
  }

  function esc(v) {
    return GN.esc(v);
  }

  function me() {
    return state && state.you;
  }

  function judge() {
    if (!state) return null;
    return state.players.filter(function (p) { return p.is_judge; })[0] || null;
  }

  function judgeName() {
    const j = judge();
    return j ? j.avatar + ' ' + j.nickname : 'someone';
  }

  function promptHtml(prompt) {
    if (!prompt) return '';
    return esc(prompt.text).replace(/_{2,}/g, '<span class="prompt__blank"></span>');
  }

  function avatarHtml(p, extra) {
    return (
      '<span class="avatar avatar--' + esc(p.color) + (p.connected ? '' : ' avatar--away') +
      (extra ? ' ' + extra : '') + '">' + esc(p.avatar) + '</span>'
    );
  }

  function pipsHtml(score, target) {
    let out = '';
    for (let i = 0; i < target; i++) {
      out += '<span class="pip' + (i < score ? ' pip--on' : '') + '"></span>';
    }
    return '<span class="pips">' + out + '</span>';
  }

  function setPanel(html) {
    if (html !== lastPanel) {
      panel.innerHTML = html;
      lastPanel = html;
    }
  }

  /** True when the phone has nothing to do and a TV is showing the action. */
  function watchOnly() {
    if (!state || !state.tv_connected || !me()) return false;
    const you = me();
    switch (state.phase) {
      case 'ROUND_READY':
      case 'PROMPT_PICK':
        return !you.is_judge;
      case 'SUBMIT':
        return you.is_judge || !!you.submitted_gif;
      case 'REVEAL':
      case 'PICK_WINNER':
      case 'ROUND_RESULT':
        return !you.is_judge;
      // GAME_OVER stays on the phone even with a TV up: everyone wants the final
      // scoreboard in their hand, and it's where Rematch and Leave live.
      default:
        return false;
    }
  }

  // --- top bar and scores ----------------------------------------------------
  function renderBar() {
    const inGame = state && state.phase !== 'LOBBY';
    playbar.hidden = !state;
    if (!state) return;
    GN.el('bar-code').textContent = CODE;
    GN.el('bar-round').textContent = inGame && state.round ? 'Round ' + state.round : '';
    const j = judge();
    GN.el('bar-judge').innerHTML = j
      ? '<span class="crown" aria-hidden="true">👑</span>' + avatarHtml(j) + '<span class="playbar__judgename">' + esc(j.nickname) + '</span>'
      : '';

    const total = state.phase === 'PROMPT_PICK' ? state.options.prompt_seconds : state.options.submit_seconds;
    countdown.set(state.deadline_ts, total);
  }

  function renderScores() {
    if (!state || state.phase === 'LOBBY') {
      scorestrip.hidden = true;
      lastStrip = null;
      return;
    }
    const target = state.options.target_score;
    const html = state.players
      .map(function (p) {
        const you = me() && p.pid === me().pid;
        return (
          '<div class="sscore' + (p.is_judge ? ' sscore--judge' : '') + (you ? ' sscore--you' : '') +
          (p.connected ? '' : ' sscore--away') + '">' +
          avatarHtml(p) +
          '<span class="sscore__name">' + esc(p.nickname) + '</span>' +
          pipsHtml(p.score, target) +
          (state.phase === 'SUBMIT' && !p.is_judge
            ? '<span class="sscore__flag">' + (p.has_submitted ? '✅' : '⏳') + '</span>'
            : '') +
          '</div>'
        );
      })
      .join('');
    if (html !== lastStrip) {
      scorestrip.innerHTML = html;
      lastStrip = html;
    }
    scorestrip.hidden = false;
  }

  // --- the table of submitted cards -----------------------------------------
  function syncTable(mode) {
    const cards = (state && state.cards) || [];
    if (!cards.length) {
      table.hidden = true;
      table.innerHTML = '';
      tableKey = null;
      return;
    }
    table.hidden = false;
    // The round has to be part of the key. Two rounds in a row with the same number of
    // answers would otherwise reuse the previous round's card elements — and since the
    // <img> is only created when one isn't already there, every phone would keep showing
    // last round's GIFs.
    const key = state.round + '|' + cards.length + '|' + mode;
    if (tableKey !== key) {
      table.innerHTML = cards
        .map(function (c) {
          const tag = mode === 'view' ? 'div' : 'button';
          const action = mode === 'flip' ? 'flip' : mode === 'pick' ? 'pick-winner' : '';
          return (
            '<' + tag + ' class="tcard" data-slot="' + c.slot + '"' +
            (action ? ' data-action="' + action + '"' : '') + '>' +
              '<span class="tcard__inner">' +
                '<span class="tcard__back"><span class="tcard__mark">GIF</span></span>' +
                '<span class="tcard__front"></span>' +
              '</span>' +
              '<span class="tcard__mine" aria-hidden="true">Yours</span>' +
              '<span class="tcard__caption"></span>' +
            '</' + tag + '>'
          );
        })
        .join('');
      tableKey = key;
    }
    table.dataset.mode = mode;
    table.dataset.count = cards.length;

    cards.forEach(function (c) {
      const node = table.querySelector('.tcard[data-slot="' + c.slot + '"]');
      if (!node) return;
      node.classList.toggle('tcard--up', !!c.revealed);
      node.classList.toggle('tcard--winner', !!c.is_winner);
      node.classList.toggle('tcard--loser', state.round_winner_slot !== null && !c.is_winner);
      // Your own answer, marked so you can follow the reveal. Server only tells you
      // about yours.
      node.classList.toggle('tcard--mine', me() !== null && me().slot === c.slot);
      // Always make the picture match the payload, whatever was there before.
      const front = node.querySelector('.tcard__front');
      let img = front.querySelector('img');
      if (c.revealed && c.gif) {
        if (!img) {
          img = document.createElement('img');
          img.className = 'tcard__img';
          img.alt = 'Submitted GIF';
          front.appendChild(img);
        }
        if (img.dataset.gif !== c.gif.id) {
          img.dataset.gif = c.gif.id;
          img.src = gifUrl(c.gif);
        }
      } else if (img) {
        img.remove(); // face down again — never leave a stale picture behind the back
      }
      const caption = node.querySelector('.tcard__caption');
      caption.innerHTML = c.author
        ? avatarHtml(c.author) + '<b>' + esc(c.author.nickname) + '</b>'
        : '';
    });
  }

  // --- the hand --------------------------------------------------------------
  function buildCardNode(card) {
    const node = document.createElement('button');
    node.className = 'gifcard';
    node.type = 'button';
    node.dataset.gif = card.id;
    node.innerHTML =
      '<img class="gifcard__img" src="' + gifUrl(card) + '" alt="' + esc(card.label) + '" loading="lazy">' +
      '<span class="gifcard__tick" aria-hidden="true">✔</span>' +
      '<span class="gifcard__new" aria-hidden="true">New</span>';
    return node;
  }

  /**
   * Reconcile the hand card by card rather than rebuilding it.
   *
   * Two reasons. Rebuilding restarts every GIF in the hand, which looks like a glitch.
   * And keeping the nodes that stay means the one card that *is* new can announce
   * itself: the replacement you draw after playing deals itself in.
   */
  function syncHand(enabled) {
    const you = me();
    const cards = (you && you.hand) || [];
    const wanted = cards.map(function (c) { return c.id; });
    const firstFill = handEl.dataset.filled !== 'yes';

    // Cards that left the hand (you played them).
    Array.prototype.slice.call(handEl.children).forEach(function (node) {
      if (wanted.indexOf(node.dataset.gif) === -1) node.remove();
    });

    // Cards that arrived, in hand order.
    cards.forEach(function (card, index) {
      let node = handEl.querySelector('.gifcard[data-gif="' + card.id + '"]');
      if (!node) {
        node = buildCardNode(card);
        // Only a card drawn mid-game gets the animation — not the opening seven.
        if (!firstFill) node.classList.add('gifcard--dealt');
      }
      if (handEl.children[index] !== node) {
        handEl.insertBefore(node, handEl.children[index] || null);
      }
    });
    handEl.dataset.filled = 'yes';

    Array.prototype.forEach.call(handEl.querySelectorAll('.gifcard'), function (node) {
      node.classList.toggle('gifcard--selected', node.dataset.gif === selected);
      node.disabled = !enabled;
    });
    handEl.classList.toggle('hand--locked', !enabled);
  }

  function showHand(mode) {
    // mode: 'play' (tappable) | 'peek' (look but don't touch) | null (hidden)
    if (!mode) {
      handarea.hidden = true;
      selected = null;
      return;
    }
    handarea.hidden = false;
    handTitle.textContent = mode === 'play' ? 'Pick your answer' : 'Your GIFs';
    syncHand(mode === 'play');
    let bar = handarea.querySelector('.confirmbar');
    if (mode === 'play' && selected) {
      if (!bar) {
        const node = document.createElement('div');
        node.className = 'confirmbar';
        node.innerHTML =
          '<button class="btn btn--big btn--go" data-action="confirm-card" type="button">Play this GIF</button>' +
          '<button class="btn btn--ghost" data-action="clear-card" type="button">Cancel</button>';
        handarea.appendChild(node);
      }
    } else if (bar) {
      bar.remove();
      bar = null;
    }
    // The bar is fixed to the bottom of the screen, so leave room for it.
    handarea.classList.toggle('handarea--choosing', !!handarea.querySelector('.confirmbar'));
  }

  // --- panels ----------------------------------------------------------------
  function lookUpPanel(line, extra) {
    return (
      '<section class="panel panel--lookup">' +
        '<span class="lookup__eyes" aria-hidden="true">👀</span>' +
        '<h1 class="panel__title">Watch the TV</h1>' +
        '<p class="panel__hint">' + line + '</p>' +
        (extra || '') +
      '</section>'
    );
  }

  function joinPanel() {
    const players = state ? state.players : [];
    const full = state && players.length >= state.max_players;
    return (
      '<section class="panel panel--join">' +
        '<h1 class="panel__title">Join game <b class="panel__code">' + CODE + '</b></h1>' +
        (joinError ? '<p class="panel__error" role="alert">' + esc(joinError) + '</p>' : '') +
        (full
          ? '<p class="panel__hint">This game is full.</p>'
          : '<form class="joinform" data-form="join">' +
              '<input class="nickinput" name="nickname" maxlength="12" minlength="2" required ' +
                'placeholder="Your nickname" autocomplete="off" autocapitalize="words" spellcheck="false" ' +
                'aria-label="Your nickname">' +
              '<button class="btn btn--big btn--go" type="submit">Let me in</button>' +
            '</form>') +
        '<div class="lobbylist">' + lobbyListHtml(false) + '</div>' +
        '<a class="panel__link" href="' + TV_URL + '">Open the TV view instead →</a>' +
      '</section>'
    );
  }

  function inProgressPanel() {
    return (
      '<section class="panel panel--closed">' +
        '<h1 class="panel__title">Game already started</h1>' +
        '<p class="panel__hint">Codes only let you in before the first round. You can still watch — the spectator view never shows anyone\'s cards.</p>' +
        '<a class="btn btn--big" href="' + TV_URL + '">Watch as spectator</a>' +
        '<a class="panel__link" href="' + HOME_URL + '">Back to Game Night</a>' +
      '</section>'
    );
  }

  function lobbyListHtml(canKick) {
    const you = me();
    return (
      '<ol class="players">' +
      state.players
        .map(function (p) {
          return (
            '<li class="player' + (p.connected ? '' : ' player--away') + '">' +
              avatarHtml(p) +
              '<span class="player__name">' + esc(p.nickname) + '</span>' +
              (p.is_host ? '<span class="tag tag--host">host</span>' : '') +
              (you && p.pid === you.pid ? '<span class="tag tag--you">you</span>' : '') +
              (canKick && !p.is_host
                ? '<button class="btn btn--tiny btn--danger" data-action="kick" data-pid="' + esc(p.pid) + '" type="button">kick</button>'
                : '') +
            '</li>'
          );
        })
        .join('') +
      '</ol>'
    );
  }

  function modeName(id) {
    const found = ((state && state.modes) || []).filter(function (m) { return m.id === id; })[0];
    return found ? found.emoji + ' ' + found.label : id;
  }

  /** The one decision that matters, up front: which set of GIFs are we playing with. */
  function modePickerHtml(editable) {
    const chosen = state.options.mode;
    const modes = state.modes || [];
    if (!editable) {
      return '<p class="modeshown">Mode: <b>' + esc(modeName(chosen)) + '</b></p>';
    }
    return (
      '<div class="option">' +
        '<span class="option__label">Game mode</span>' +
        '<div class="modes">' +
          modes
            .map(function (m) {
              const on = m.id === chosen;
              // A mode with too few curated GIFs can't be played yet — say so instead of
              // failing at Start.
              return (
                '<button class="mode' + (on ? ' mode--on' : '') + (m.enough ? '' : ' mode--empty') + '"' +
                ' data-opt="mode" data-value="' + esc(m.id) + '" aria-pressed="' + (on ? 'true' : 'false') + '"' +
                (m.enough ? '' : ' disabled title="Not enough GIFs curated for this mode yet"') +
                ' type="button">' +
                  '<span class="mode__emoji" aria-hidden="true">' + esc(m.emoji) + '</span>' +
                  '<span class="mode__label">' + esc(m.label) + '</span>' +
                  '<span class="mode__cards">' + (m.enough ? m.cards + ' GIFs' : (m.cards ? 'only ' + m.cards : 'none yet')) + '</span>' +
                '</button>'
              );
            })
            .join('') +
        '</div>' +
      '</div>'
    );
  }

  function moreSettingsHtml() {
    const o = state.options;
    const timerOpts = function (values, current) {
      return values
        .map(function (v) {
          const label = v === 0 ? 'No timer' : v + 's';
          return '<option value="' + v + '"' + (v === current ? ' selected' : '') + '>' + label + '</option>';
        })
        .join('');
    };
    return (
      '<div class="more">' +
        '<button class="more__toggle" data-action="toggle-more" aria-expanded="' + (moreOpen ? 'true' : 'false') + '" type="button">' +
          '⚙︎ More settings' +
        '</button>' +
        '<div class="more__body options"' + (moreOpen ? '' : ' hidden') + '>' +
          '<div class="option">' +
            '<span class="option__label">Who judges next?</span>' +
            '<div class="segmented">' +
              '<button class="seg" data-opt="judge_rotation" data-value="circle" aria-pressed="' + (o.judge_rotation === 'circle' ? 'true' : 'false') + '" type="button">Circle</button>' +
              '<button class="seg" data-opt="judge_rotation" data-value="last_winner" aria-pressed="' + (o.judge_rotation === 'last_winner' ? 'true' : 'false') + '" type="button">Last winner</button>' +
            '</div>' +
          '</div>' +
          '<div class="option">' +
            '<label class="option__label" for="opt-score">Points to win — <b id="score-out">' + o.target_score + '</b></label>' +
            '<input id="opt-score" class="slider" type="range" data-opt="target_score" min="' + root.dataset.scoreMin + '" max="' + root.dataset.scoreMax + '" value="' + o.target_score + '">' +
          '</div>' +
          '<div class="option option--pair">' +
            '<label class="option__label" for="opt-prompt">Prompt pick</label>' +
            '<select id="opt-prompt" class="select" data-opt="prompt_seconds">' + timerOpts([0, 5, 10, 15, 20, 30], o.prompt_seconds) + '</select>' +
          '</div>' +
          '<div class="option option--pair">' +
            '<label class="option__label" for="opt-submit">Answer time</label>' +
            '<select id="opt-submit" class="select" data-opt="submit_seconds">' + timerOpts([0, 30, 45, 60, 90, 120, 180], o.submit_seconds) + '</select>' +
          '</div>' +
          '<label class="checkline">' +
            '<input type="checkbox" data-opt="test_mode"' + (o.test_mode ? ' checked' : '') + '>' +
            '<span>Test mode — start with just ' + root.dataset.testMinPlayers + ' players</span>' +
          '</label>' +
        '</div>' +
      '</div>'
    );
  }

  function settingsSummaryHtml() {
    const o = state.options;
    return (
      '<ul class="optsummary">' +
        '<li>First to <b>' + o.target_score + '</b> points</li>' +
        '<li>Judge: <b>' + (o.judge_rotation === 'circle' ? 'in a circle' : "last round's winner") + '</b></li>' +
        '<li>Answers: <b>' + (o.submit_seconds ? o.submit_seconds + 's' : 'no timer') + '</b></li>' +
      '</ul>'
    );
  }

  function lobbyPanel() {
    const you = me();
    const count = state.players.length;
    const need = state.min_players;
    const canStart = state.can_start;
    return (
      '<section class="panel panel--lobby">' +
        '<h1 class="panel__title">Lobby</h1>' +
        '<p class="panel__code">Code <b>' + CODE + '</b> · ' + count + '/' + state.max_players + ' players</p>' +
        lobbyListHtml(you.is_host) +
        (you.is_host
          ? modePickerHtml(true) +
            moreSettingsHtml() +
            '<button class="btn btn--big btn--go" data-action="start"' + (canStart ? '' : ' disabled') + ' type="button">' +
              (canStart ? 'Start game' : 'Need ' + (need - count) + ' more player' + (need - count === 1 ? '' : 's')) +
            '</button>'
          : modePickerHtml(false) + settingsSummaryHtml() +
            '<p class="panel__hint">Waiting for the host to start' +
              (canStart ? '…' : ' — ' + (need - count) + ' more player' + (need - count === 1 ? '' : 's') + ' needed') +
            '</p>') +
        '<a class="panel__link" href="' + TV_URL + '">Put this game on a TV →</a>' +
        '<button class="btn btn--ghost btn--tiny" data-action="leave" type="button">Leave game</button>' +
      '</section>'
    );
  }

  function readyPanel() {
    const you = me();
    if (you.is_judge) {
      return (
        '<section class="panel panel--ready">' +
          '<span class="panel__crown" aria-hidden="true">👑</span>' +
          '<h1 class="panel__title">You\'re the judge</h1>' +
          '<p class="panel__hint">Everyone else answers, you pick the winner. Take your time.</p>' +
          '<button class="btn btn--big btn--go" data-action="ready" type="button">I\'m ready</button>' +
        '</section>'
      );
    }
    return (
      '<section class="panel">' +
        '<h1 class="panel__title">' + esc(judgeName()) + ' is judging</h1>' +
        '<p class="panel__hint">Waiting for them to start the round…</p>' +
      '</section>'
    );
  }

  function promptPickPanel() {
    const you = me();
    if (you.is_judge) {
      const choices = state.prompt_choices || [];
      return (
        '<section class="panel panel--pick">' +
          '<h1 class="panel__title">Pick a prompt</h1>' +
          '<p class="panel__hint">Choose the one you want to judge.</p>' +
          '<div class="prompts">' +
            choices
              .map(function (p) {
                return (
                  '<button class="promptcard" data-action="pick-prompt" data-prompt="' + esc(p.id) + '" type="button">' +
                    '<span class="prompt">' + promptHtml(p) + '</span>' +
                  '</button>'
                );
              })
              .join('') +
          '</div>' +
        '</section>'
      );
    }
    return (
      '<section class="panel">' +
        '<h1 class="panel__title">' + esc(judgeName()) + ' is choosing a prompt</h1>' +
        '<p class="panel__hint">Get your best GIF ready.</p>' +
      '</section>'
    );
  }

  function submitPanel() {
    const you = me();
    const waiting = state.waiting_on || [];
    const waitLine =
      waiting.length === 0
        ? 'Everyone\'s in!'
        : 'Waiting on ' +
          waiting
            .map(function (w) { return esc(w.avatar) + ' ' + esc(w.nickname) + (w.connected ? '' : ' (away)'); })
            .join(', ');

    if (you.is_judge) {
      return (
        '<section class="panel panel--blind">' +
          '<p class="panel__label">The prompt</p>' +
          '<p class="prompt prompt--big">' + promptHtml(state.prompt) + '</p>' +
          '<div class="facedown">' +
            new Array(state.submitted_count + 1).join('<span class="facedown__card"></span>') +
          '</div>' +
          '<p class="panel__hint">' + state.submitted_count + ' of ' + state.expected_count + ' played. ' + waitLine + '</p>' +
          '<p class="panel__note">You can\'t see the answers yet — no peeking.</p>' +
        '</section>'
      );
    }

    if (you.submitted_gif) {
      return (
        '<section class="panel panel--waiting">' +
          '<p class="panel__label">The prompt</p>' +
          '<p class="prompt">' + promptHtml(state.prompt) + '</p>' +
          '<p class="panel__label">You played</p>' +
          '<img class="playedgif" src="' + gifUrl(you.submitted_gif) + '" alt="The GIF you played">' +
          (you.submitted_auto ? '<p class="panel__note">Time ran out, so this one got played for you.</p>' : '') +
          '<p class="panel__hint">' + state.submitted_count + ' of ' + state.expected_count + ' played. ' + waitLine + '</p>' +
        '</section>'
      );
    }

    return (
      '<section class="panel panel--answer">' +
        '<p class="panel__label">' + esc(judgeName()) + ' is judging</p>' +
        '<p class="prompt prompt--big">' + promptHtml(state.prompt) + '</p>' +
        '<p class="panel__hint">Pick the funniest answer from your GIFs below.</p>' +
      '</section>'
    );
  }

  function revealPanel() {
    const you = me();
    const revealed = (state.cards || []).filter(function (c) { return c.revealed; }).length;
    const total = (state.cards || []).length;
    if (you.is_judge) {
      const done = revealed === total;
      return (
        '<section class="panel panel--reveal">' +
          '<p class="panel__label">The prompt</p>' +
          '<p class="prompt">' + promptHtml(state.prompt) + '</p>' +
          '<h1 class="panel__title">' + (done ? 'Pick your favorite' : 'Flip them over') + '</h1>' +
          '<p class="panel__hint">' +
            (done ? 'Tap the winner.' : 'Tap a card to reveal it — ' + revealed + ' of ' + total + ' flipped.') +
          '</p>' +
        '</section>'
      );
    }
    return (
      '<section class="panel">' +
        '<p class="panel__label">The prompt</p>' +
        '<p class="prompt">' + promptHtml(state.prompt) + '</p>' +
        '<p class="panel__hint">' + esc(judgeName()) + ' is revealing the answers — ' + revealed + ' of ' + total + '.</p>' +
      '</section>'
    );
  }

  function resultPanel() {
    const winner = state.players.filter(function (p) { return p.pid === state.round_winner_pid; })[0];
    const you = me();
    const isYou = you && winner && winner.pid === you.pid;
    return (
      '<section class="panel panel--result">' +
        '<p class="panel__label">The prompt</p>' +
        '<p class="prompt">' + promptHtml(state.prompt) + '</p>' +
        '<h1 class="panel__title panel__title--win">' +
          (winner ? (isYou ? '🏆 You win the round!' : '🏆 ' + esc(winner.avatar) + ' ' + esc(winner.nickname) + ' wins the round') : 'Round over') +
        '</h1>' +
        (winner ? '<p class="panel__hint">' + winner.score + ' point' + (winner.score === 1 ? '' : 's') + ' of ' + state.options.target_score + '</p>' : '') +
        (you.is_judge
          ? '<button class="btn btn--big btn--go" data-action="next-round" type="button">Next round</button>'
          : '<p class="panel__hint">Waiting for ' + esc(judgeName()) + ' to start the next round…</p>') +
      '</section>'
    );
  }

  function gameOverPanel() {
    const champ = state.players.filter(function (p) { return p.pid === state.champion_pid; })[0];
    const you = me();
    const isYou = you && champ && champ.pid === you.pid;
    const board = state.players
      .slice()
      .sort(function (a, b) { return b.score - a.score; })
      .map(function (p, i) {
        return (
          '<li class="final' + (p.pid === state.champion_pid ? ' final--champ' : '') + '">' +
            '<span class="final__rank">' + (i + 1) + '</span>' +
            avatarHtml(p) +
            '<span class="final__name">' + esc(p.nickname) + '</span>' +
            '<span class="final__score">' + p.score + '</span>' +
          '</li>'
        );
      })
      .join('');
    return (
      '<section class="panel panel--over">' +
        '<h1 class="panel__title panel__title--win">' +
          (champ ? (isYou ? '🏆 You win!' : '🏆 ' + esc(champ.avatar) + ' ' + esc(champ.nickname) + ' wins!') : 'Game over') +
        '</h1>' +
        '<ol class="finals">' + board + '</ol>' +
        (you.is_host
          ? '<button class="btn btn--big btn--go" data-action="rematch" type="button">Rematch</button>'
          : '<p class="panel__hint">Waiting for the host to start a rematch…</p>') +
        '<button class="btn btn--ghost" data-action="leave" type="button">Leave game</button>' +
      '</section>'
    );
  }

  // --- master render ---------------------------------------------------------
  function render() {
    if (!state) return;
    GN.el('boot') && GN.el('boot').remove();
    renderBar();
    renderScores();

    if (!me()) {
      // Not a player: either the lobby (can still join) or a closed game.
      setPanel(state.phase === 'LOBBY' ? joinPanel() : inProgressPanel());
      syncTable('view');
      table.hidden = true;
      showHand(null);
      return;
    }

    const you = me();
    const phase = state.phase;

    if (watchOnly()) {
      let line = '';
      let extra = '';
      if (phase === 'ROUND_READY') line = esc(judgeName()) + ' is getting ready.';
      else if (phase === 'PROMPT_PICK') line = esc(judgeName()) + ' is picking a prompt.';
      else if (phase === 'SUBMIT' && you.is_judge) line = state.submitted_count + ' of ' + state.expected_count + ' answers are in.';
      else if (phase === 'SUBMIT') line = 'Your GIF is in. ' + state.submitted_count + ' of ' + state.expected_count + ' played.';
      else if (phase === 'REVEAL') line = esc(judgeName()) + ' is flipping the cards.';
      else if (phase === 'PICK_WINNER') line = esc(judgeName()) + ' is deciding.';
      else if (phase === 'ROUND_RESULT') line = 'Round over — next one coming up.';
      else if (phase === 'GAME_OVER') line = 'Game over!';
      if (phase === 'SUBMIT' && !you.is_judge && you.submitted_gif) {
        extra = '<img class="playedgif playedgif--small" src="' + gifUrl(you.submitted_gif) + '" alt="The GIF you played">';
      }
      setPanel(lookUpPanel(line, extra));
      syncTable('view');
      table.hidden = true;
      showHand(null);
      return;
    }

    switch (phase) {
      case 'LOBBY':
        setPanel(lobbyPanel());
        syncTable('view');
        table.hidden = true;
        showHand(null);
        break;

      case 'ROUND_READY':
        setPanel(readyPanel());
        table.hidden = true;
        showHand(you.is_judge ? null : 'peek');
        break;

      case 'PROMPT_PICK':
        setPanel(promptPickPanel());
        table.hidden = true;
        showHand(you.is_judge ? null : 'peek');
        break;

      case 'SUBMIT':
        setPanel(submitPanel());
        table.hidden = true;
        // After you've played, keep the hand on screen in look-don't-touch mode: that's
        // when the replacement card deals itself in, and it's worth seeing.
        showHand(you.is_judge ? null : (you.submitted_gif ? 'peek' : 'play'));
        break;

      case 'REVEAL':
        setPanel(revealPanel());
        syncTable(you.is_judge ? 'flip' : 'view');
        showHand(null);
        break;

      case 'PICK_WINNER':
        setPanel(revealPanel());
        syncTable(you.is_judge ? 'pick' : 'view');
        showHand(null);
        break;

      case 'ROUND_RESULT':
        setPanel(resultPanel());
        syncTable('view');
        showHand(null);
        break;

      case 'GAME_OVER':
        setPanel(gameOverPanel());
        syncTable('view');
        showHand(null);
        break;
    }
  }

  // --- input ----------------------------------------------------------------
  function collectOptions() {
    const out = {};
    Array.prototype.forEach.call(panel.querySelectorAll('[data-opt]'), function (node) {
      const key = node.dataset.opt;
      if (node.tagName === 'BUTTON') {
        if (node.getAttribute('aria-pressed') === 'true') out[key] = node.dataset.value;
      } else if (node.type === 'checkbox') {
        out[key] = node.checked;
      } else {
        out[key] = node.value;
      }
    });
    return out;
  }

  document.addEventListener('click', function (event) {
    const hit = event.target.closest('[data-action], [data-opt][data-value], .gifcard');
    if (!hit) return;

    if (hit.classList.contains('gifcard')) {
      if (hit.disabled) return;
      selected = hit.dataset.gif === selected ? null : hit.dataset.gif;
      showHand('play');
      return;
    }

    if (hit.dataset.opt && hit.dataset.value) {
      // Mode picker / segmented control: flip it locally, then tell the server.
      Array.prototype.forEach.call(panel.querySelectorAll('[data-opt="' + hit.dataset.opt + '"]'), function (n) {
        const on = n === hit;
        n.setAttribute('aria-pressed', on ? 'true' : 'false');
        n.classList.toggle('seg--on', on && n.classList.contains('seg'));
        n.classList.toggle('mode--on', on && n.classList.contains('mode'));
      });
      conn.send('set_options', { options: collectOptions() });
      return;
    }

    if (hit.dataset.action === 'toggle-more') {
      moreOpen = !moreOpen;
      render();
      return;
    }

    const action = hit.dataset.action;
    switch (action) {
      case 'start':
        conn.send('start_game', { options: collectOptions() });
        break;
      case 'kick':
        conn.send('kick', { pid: hit.dataset.pid });
        break;
      case 'ready':
        conn.send('judge_ready');
        break;
      case 'pick-prompt':
        conn.send('pick_prompt', { prompt_id: hit.dataset.prompt });
        break;
      case 'confirm-card':
        if (selected) {
          conn.send('submit_card', { gif_id: selected });
          selected = null;
        }
        break;
      case 'clear-card':
        selected = null;
        showHand('play');
        break;
      case 'flip':
        conn.send('flip', { slot: Number(hit.dataset.slot) });
        break;
      case 'pick-winner':
        conn.send('pick_winner', { slot: Number(hit.dataset.slot) });
        break;
      case 'next-round':
        conn.send('next_round');
        break;
      case 'rematch':
        conn.send('rematch');
        break;
      case 'leave':
        sessionStorage.removeItem(NICK_KEY);
        conn.send('leave_game');
        window.location.href = HOME_URL;
        break;
    }
  });

  document.addEventListener('submit', function (event) {
    const form = event.target.closest('[data-form="join"]');
    if (!form) return;
    event.preventDefault();
    const value = form.querySelector('[name="nickname"]').value.trim();
    if (value.length < 2) {
      GN.toast('Nicknames need at least 2 characters', 'bad');
      return;
    }
    nickname = value;
    conn.send('join_as_player', { code: CODE, nickname: nickname });
  });

  document.addEventListener('change', function (event) {
    if (!event.target.matches('[data-opt]')) return;
    conn.send('set_options', { options: collectOptions() });
  });

  document.addEventListener('input', function (event) {
    if (!event.target.matches('[data-opt="target_score"]')) return;
    const out = GN.el('score-out');
    if (out) out.textContent = event.target.value;
  });
})();
