/* Game Night — shared client plumbing.
 *
 * One Socket.IO connection, a server-clock offset so countdowns don't drift, and a
 * couple of tiny DOM helpers. Everything game-specific lives in gah-play.js / gah-tv.js.
 */
window.GN = (function () {
  'use strict';

  /** Escape untrusted text (nicknames!) before it goes anywhere near innerHTML. */
  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function el(id) {
    return document.getElementById(id);
  }

  /** Show a short-lived message. Used for rejected actions. */
  function toast(message, kind) {
    const host = el('toasts');
    if (!host) return;
    const node = document.createElement('div');
    node.className = 'toast' + (kind ? ' toast--' + kind : '');
    node.textContent = message;
    host.appendChild(node);
    setTimeout(function () {
      node.classList.add('toast--out');
      setTimeout(function () { node.remove(); }, 400);
    }, 2600);
  }

  /**
   * A connection to one room.
   *
   * opts: { code, role: 'player'|'tv'|'watcher', joinPayload(), onState(state),
   *         onJoined(info), onRejected(info), onGone(info), onStatus(connected) }
   */
  function Connection(opts) {
    const self = this;
    this.opts = opts;
    this.clockOffset = 0; // serverNow - clientNow, in seconds
    this.socket = io({ transports: ['websocket', 'polling'] });

    this.socket.on('connect', function () {
      if (opts.onStatus) opts.onStatus(true);
      self.join();
    });

    this.socket.on('disconnect', function () {
      if (opts.onStatus) opts.onStatus(false);
    });

    this.socket.on('state', function (state) {
      if (typeof state.server_now === 'number') {
        self.clockOffset = state.server_now - Date.now() / 1000;
      }
      if (opts.onState) opts.onState(state);
    });

    this.socket.on('joined', function (info) {
      if (opts.onJoined) opts.onJoined(info);
    });

    this.socket.on('join_rejected', function (info) {
      if (opts.onRejected) opts.onRejected(info);
    });

    this.socket.on('room_gone', function (info) {
      if (opts.onGone) opts.onGone(info);
    });

    this.socket.on('kicked', function (info) {
      if (opts.onKicked) opts.onKicked(info);
    });

    this.socket.on('action_error', function (info) {
      toast(info && info.message ? info.message : 'That didn\'t work', 'bad');
    });
  }

  /** (Re)announce ourselves. Called on every connect, so reconnects restore the seat. */
  Connection.prototype.join = function () {
    const payload = this.opts.joinPayload ? this.opts.joinPayload() : null;
    if (!payload) return;
    this.socket.emit(payload.event, payload.data);
  };

  Connection.prototype.send = function (event, data) {
    this.socket.emit(event, data || {});
  };

  /** Seconds left on a server deadline, corrected for clock skew. */
  Connection.prototype.remaining = function (deadlineTs) {
    if (!deadlineTs) return null;
    return deadlineTs - (Date.now() / 1000 + this.clockOffset);
  };

  /**
   * Drives a countdown element. Call update() whenever new state arrives; the loop
   * stops on its own when there's no deadline.
   */
  function Countdown(conn, wrap, fill, label) {
    this.conn = conn;
    this.wrap = wrap;
    this.fill = fill;
    this.label = label;
    this.deadline = null;
    this.total = null;
    this.raf = null;
  }

  Countdown.prototype.set = function (deadlineTs, totalSeconds) {
    this.deadline = deadlineTs || null;
    if (this.deadline && (!this.total || this.totalFor !== deadlineTs)) {
      const left = this.conn.remaining(this.deadline);
      this.total = Math.max(totalSeconds || left || 1, left || 1);
      this.totalFor = deadlineTs;
    }
    if (!this.deadline) {
      this.wrap.hidden = true;
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = null;
      return;
    }
    this.wrap.hidden = false;
    if (!this.raf) this.tick();
  };

  Countdown.prototype.tick = function () {
    const self = this;
    if (!this.deadline) { this.raf = null; return; }
    const left = Math.max(0, this.conn.remaining(this.deadline));
    const pct = this.total ? Math.max(0, Math.min(1, left / this.total)) : 0;
    this.fill.style.transform = 'scaleX(' + pct.toFixed(4) + ')';
    this.label.textContent = left >= 10 ? Math.ceil(left) + 's' : left.toFixed(1) + 's';
    this.wrap.classList.toggle('timer--urgent', left <= 5);
    this.raf = requestAnimationFrame(function () { self.tick(); });
  };

  return {
    esc: esc,
    el: el,
    toast: toast,
    Connection: Connection,
    Countdown: Countdown,
  };
})();
