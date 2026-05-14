#!/usr/bin/env python3
"""
Polymarket Sniper Bot — Live Dashboard Server
=============================================
Ye script dashboard_data.json ko read karke ek live web dashboard serve karta hai.
Bot ke saath same directory mein rakho aur run karo:

    python dashboard_server.py

Browser mein kholo: http://localhost:8765

Bot aur dashboard_server dono alag terminals mein run karte hain.
Bot dashboard_data.json likhta hai, ye server isse padh ke browser ko bhejta hai.
"""

import http.server
import json
import os
import threading
import time
from datetime import datetime, timezone

DASHBOARD_JSON = "dashboard_data.json"
HOST           = "localhost"
PORT           = 8765

# ─────────────────────────────────────────────────────────────────────────────
# EMBEDDED HTML  (ye wahi design hai jo tumne share kiya tha — real data se)
# ─────────────────────────────────────────────────────────────────────────────
DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SNIPER — Polymarket Dashboard</title>
<link href="https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.js"></script>
<style>
  *{box-sizing:border-box;margin:0;padding:0}
  :root{
    --g:#00ff88;--b:#00cfff;--dim:#003322;--dimb:#001a2e;
    --bg:#050e0a;--card:#0a1a10;--cardb:#06111a;
    --border:#00ff8844;--borderb:#00cfff44;
    --red:#ff3b3b;--yellow:#ffd700;
  }
  body{background:var(--bg);font-family:'Share Tech Mono',monospace;color:var(--g);min-height:100vh;overflow-x:hidden}
  .root{padding:14px;min-height:100vh;position:relative}
  .root::before{content:'';position:fixed;top:0;left:0;right:0;bottom:0;
    background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,255,136,0.012) 2px,rgba(0,255,136,0.012) 4px);
    pointer-events:none;z-index:0}

  /* ── HEADER ── */
  .hdr{display:flex;align-items:center;justify-content:space-between;
    border-bottom:1px solid var(--g);padding-bottom:10px;margin-bottom:14px;position:relative;z-index:1}
  .hdr-left{display:flex;align-items:center;gap:14px}
  .logo{font-family:'Orbitron',monospace;font-size:18px;font-weight:700;
    color:var(--g);letter-spacing:3px;text-shadow:0 0 12px var(--g)}
  .badge{font-size:10px;border:1px solid var(--g);padding:2px 8px;color:var(--g);letter-spacing:2px}
  .live-dot{width:8px;height:8px;border-radius:50%;background:var(--g);animation:pulse 1.2s infinite}
  .stale-dot{width:8px;height:8px;border-radius:50%;background:var(--red)}
  @keyframes pulse{0%,100%{opacity:1;box-shadow:0 0 6px var(--g)}50%{opacity:.3;box-shadow:none}}
  .clock{font-size:12px;color:var(--b);letter-spacing:1px;text-align:right}
  .last-upd{font-size:9px;color:#447766;letter-spacing:1px;margin-top:2px}

  /* ── STATS ── */
  .stats-grid{display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:10px;margin-bottom:14px;position:relative;z-index:1}
  @media(max-width:900px){.stats-grid{grid-template-columns:repeat(4,1fr)}}
  @media(max-width:500px){.stats-grid{grid-template-columns:repeat(2,1fr)}}
  .stat{background:var(--card);border:1px solid var(--border);padding:10px 12px;position:relative;overflow:hidden}
  .stat::before{content:'';position:absolute;top:0;left:0;width:3px;height:100%;background:var(--g)}
  .stat.blue::before{background:var(--b)}
  .stat.red::before{background:var(--red)}
  .stat.yellow::before{background:var(--yellow)}
  .stat-lbl{font-size:9px;color:#448866;letter-spacing:2px;text-transform:uppercase;margin-bottom:4px}
  .stat-val{font-size:18px;font-family:'Orbitron',monospace;font-weight:700}
  .stat-val.green{color:var(--g);text-shadow:0 0 8px var(--g)}
  .stat-val.blue{color:var(--b);text-shadow:0 0 8px var(--b)}
  .stat-val.red{color:var(--red);text-shadow:0 0 6px var(--red)}
  .stat-val.yellow{color:var(--yellow)}
  .stat-sub{font-size:10px;color:#447755;margin-top:3px}

  /* ── MAIN GRID ── */
  .main-grid{display:grid;grid-template-columns:1fr 330px;gap:12px;position:relative;z-index:1}
  @media(max-width:900px){.main-grid{grid-template-columns:1fr}}
  .left-col{display:flex;flex-direction:column;gap:10px}

  /* ── TERMINAL ── */
  .terminal{background:#020a05;border:1px solid var(--border);overflow:hidden}
  .term-hdr{display:flex;align-items:center;gap:8px;padding:8px 12px;
    border-bottom:1px solid var(--border);background:#050e08}
  .term-dots{display:flex;gap:5px}
  .dot{width:10px;height:10px;border-radius:50%}
  .dot-r{background:#ff5f57}.dot-y{background:#ffbd2e}.dot-g{background:#28c840}
  .term-title{font-size:11px;color:#448866;letter-spacing:2px;margin-left:4px;flex:1}
  .term-body{padding:10px 12px;height:280px;overflow-y:auto;font-size:11px;line-height:1.75}
  .term-body::-webkit-scrollbar{width:4px}
  .term-body::-webkit-scrollbar-track{background:#020a05}
  .term-body::-webkit-scrollbar-thumb{background:#00ff8833}
  .log-line{display:flex;gap:8px;align-items:flex-start;border-bottom:1px solid #0a1f1066;padding:3px 0}
  .log-ts{color:#226644;min-width:68px;font-size:10px;flex-shrink:0}
  .log-tag{min-width:72px;font-size:10px;font-weight:700;letter-spacing:1px;flex-shrink:0}
  .tag-trade{color:var(--g)}.tag-exec{color:#00ffcc}.tag-sig{color:var(--yellow)}
  .tag-settle{color:var(--b)}.tag-sys{color:#668877}.tag-l1{color:#aabbaa}
  .tag-win{color:var(--g)}.tag-loss{color:var(--red)}.tag-notrade{color:#556655}
  .log-msg{color:#aaccaa;flex:1;font-size:10px;word-break:break-word}
  .hi{color:var(--g)}.hib{color:var(--b)}.hir{color:var(--red)}.hiy{color:var(--yellow)}
  .cursor{display:inline-block;width:8px;height:12px;background:var(--g);
    animation:blink .7s step-end infinite;vertical-align:middle}
  @keyframes blink{50%{opacity:0}}

  /* ── TRADE TABLE ── */
  .panel{background:var(--card);border:1px solid var(--border)}
  .panel.blue{border-color:var(--borderb);background:var(--cardb)}
  .panel-hdr{padding:8px 12px;border-bottom:1px solid var(--border);font-size:10px;
    letter-spacing:2px;color:var(--g);display:flex;justify-content:space-between;align-items:center}
  .panel.blue .panel-hdr{border-color:var(--borderb);color:var(--b)}
  .tbl-wrap{overflow-x:auto}
  .trade-table{width:100%;font-size:10px;border-collapse:collapse;min-width:600px}
  .trade-table th{padding:5px 8px;color:#447766;font-size:9px;letter-spacing:1px;
    border-bottom:1px solid #0a2015;text-align:left;white-space:nowrap}
  .trade-table td{padding:5px 8px;border-bottom:1px solid #050f08;white-space:nowrap}
  .trade-table tr:hover td{background:#0a1f10}
  .badge-yes{background:#003322;color:var(--g);border:1px solid var(--g);padding:1px 5px;font-size:9px}
  .badge-no{background:#001a2e;color:var(--b);border:1px solid var(--b);padding:1px 5px;font-size:9px}
  .pnl-pos{color:var(--g)}.pnl-neg{color:var(--red)}

  /* ── SIDEBAR ── */
  .sidebar{display:flex;flex-direction:column;gap:10px}

  /* ── RING TIMERS ── */
  .timer-ring{display:flex;justify-content:center;padding:12px 8px;gap:18px;align-items:center}
  .ring-wrap{position:relative;width:70px;height:70px}
  .ring-wrap svg{transform:rotate(-90deg)}
  .ring-label{position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);text-align:center}
  .rl-val{font-family:'Orbitron',monospace;font-size:14px;font-weight:700}
  .rl-unit{font-size:8px;color:#448866;letter-spacing:1px}
  .sym-badges{display:flex;gap:8px;padding:0 10px 10px}
  .sym-b{flex:1;background:#020a05;border:1px solid var(--g);padding:6px;text-align:center}
  .sym-b.eth{border-color:var(--b)}
  .sym-price{font-family:'Orbitron',monospace;font-size:12px}
  .sym-delta{font-size:9px;margin-top:2px}

  /* ── LLM CONFIDENCE ── */
  .conf-row{display:flex;align-items:center;gap:8px;padding:5px 12px;border-bottom:1px solid #050f08;font-size:10px}
  .conf-label{min-width:90px;color:#448866;flex-shrink:0}
  .conf-bar-bg{flex:1;height:6px;background:#0a1a10;border:1px solid #0a2015}
  .conf-bar{height:100%;transition:width .5s ease}
  .conf-val{min-width:32px;text-align:right;font-size:10px;flex-shrink:0}

  /* ── L1 SIGNALS ── */
  .l1-row{display:flex;justify-content:space-between;font-size:10px;padding:4px 12px;border-bottom:1px solid #050f08}
  .l1-lbl{color:#448866}

  /* ── BALANCE CHART ── */
  .chart-wrap{padding:10px;height:110px;position:relative}

  /* ── OPEN TRADES ── */
  .open-trade-row{padding:6px 12px;border-bottom:1px solid #050f08;font-size:10px}
  .ot-sym{font-weight:700;margin-right:6px}
</style>
</head>
<body>
<div class="root">

  <!-- HEADER -->
  <div class="hdr">
    <div class="hdr-left">
      <div class="live-dot" id="liveDot"></div>
      <div class="logo">SNIPER</div>
      <div class="badge">POLYMARKET v2.0</div>
    </div>
    <div style="text-align:right">
      <div class="clock" id="clockEl">--:--:-- UTC</div>
      <div class="last-upd" id="lastUpd">last update: --</div>
    </div>
  </div>

  <!-- STATS ROW -->
  <div class="stats-grid">
    <div class="stat yellow">
      <div class="stat-lbl">Initial Balance</div>
      <div class="stat-val yellow" id="s-init">$100.00</div>
      <div class="stat-sub">starting capital</div>
    </div>
    <div class="stat blue">
      <div class="stat-lbl">Current Balance</div>
      <div class="stat-val blue" id="s-bal">$100.00</div>
      <div class="stat-sub" id="s-bal-sub">+$0.00 today</div>
    </div>
    <div class="stat">
      <div class="stat-lbl">Total Trades</div>
      <div class="stat-val green" id="s-trades">0</div>
      <div class="stat-sub" id="s-wr">W:0 / L:0</div>
    </div>
    <div class="stat">
      <div class="stat-lbl">Win Rate</div>
      <div class="stat-val green" id="s-winrate">0.0%</div>
      <div class="stat-sub" id="s-winrate-sub">0 closed</div>
    </div>
    <div class="stat">
      <div class="stat-lbl">Today P&amp;L</div>
      <div class="stat-val green" id="s-pnl">+$0.00</div>
      <div class="stat-sub">realized</div>
    </div>
    <div class="stat">
      <div class="stat-lbl">Value</div>
      <div class="stat-val green" id="s-value">+$0.00</div>
      <div class="stat-sub">open P/L</div>
    </div>
    <div class="stat">
      <div class="stat-lbl">To Win</div>
      <div class="stat-val yellow" id="s-win-potential">$0.00</div>
      <div class="stat-sub">open payout</div>
    </div>
    <div class="stat red">
      <div class="stat-lbl">Daily Spent</div>
      <div class="stat-val red" id="s-spent">$0.00</div>
      <div class="stat-sub">LLM calls: <span id="s-llm">0</span></div>
    </div>
  </div>

  <!-- MAIN GRID -->
  <div class="main-grid">
    <div class="left-col">

      <!-- TERMINAL -->
      <div class="terminal">
        <div class="term-hdr">
          <div class="term-dots">
            <div class="dot dot-r"></div>
            <div class="dot dot-y"></div>
            <div class="dot dot-g"></div>
          </div>
          <div class="term-title">LIVE TERMINAL — EVENT LOG</div>
          <div style="font-size:9px;color:#336655;letter-spacing:1px" id="pingEl">● POLLING</div>
        </div>
        <div class="term-body" id="termBody">
          <div class="log-line">
            <span class="log-ts" id="boot-ts"></span>
            <span class="log-tag tag-sys">[SYS]</span>
            <span class="log-msg">Dashboard connecting to bot... <span class="cursor"></span></span>
          </div>
        </div>
      </div>

      <!-- TRADE HISTORY TABLE -->
      <div class="panel">
        <div class="panel-hdr">
          <span>TRADE HISTORY</span>
          <span id="trade-count-lbl" style="color:#448866;font-size:9px">0 TRADES</span>
        </div>
        <div class="tbl-wrap">
          <table class="trade-table">
            <thead>
              <tr>
                <th>TIME</th><th>SYM</th><th>DIR</th><th>ENTRY</th>
                <th>SIZE</th><th>CONF</th><th>OUTCOME</th><th>P&amp;L</th><th>REASONING</th>
              </tr>
            </thead>
            <tbody id="tradeBody">
              <tr><td colspan="9" style="color:#336655;text-align:center;padding:14px;font-size:10px">AWAITING BOT DATA...</td></tr>
            </tbody>
          </table>
        </div>
      </div>

    </div><!-- /left-col -->

    <!-- SIDEBAR -->
    <div class="sidebar">

      <!-- WINDOW TIMERS + PRICES -->
      <div class="panel">
        <div class="panel-hdr">
          <span>ACTIVE POSITIONS</span>
          <span style="color:#448866;font-size:9px" id="open-count">0 OPEN</span>
        </div>
        <div class="timer-ring">
          <div class="ring-wrap">
            <svg width="70" height="70" viewBox="0 0 70 70">
              <circle cx="35" cy="35" r="28" fill="none" stroke="#0a1f10" stroke-width="6"/>
              <circle cx="35" cy="35" r="28" fill="none" stroke="#00ff88" stroke-width="6"
                stroke-dasharray="175.9" stroke-dashoffset="175.9" id="btcRing" stroke-linecap="round"/>
            </svg>
            <div class="ring-label">
              <div class="rl-val" style="color:var(--g)" id="btcSec">300</div>
              <div class="rl-unit">BTC SEC</div>
            </div>
          </div>
          <div class="ring-wrap">
            <svg width="70" height="70" viewBox="0 0 70 70">
              <circle cx="35" cy="35" r="28" fill="none" stroke="#001a2e" stroke-width="6"/>
              <circle cx="35" cy="35" r="28" fill="none" stroke="#00cfff" stroke-width="6"
                stroke-dasharray="175.9" stroke-dashoffset="175.9" id="ethRing" stroke-linecap="round"/>
            </svg>
            <div class="ring-label">
              <div class="rl-val" style="color:var(--b)" id="ethSec">300</div>
              <div class="rl-unit">ETH SEC</div>
            </div>
          </div>
        </div>
        <div class="sym-badges">
          <div class="sym-b">
            <div style="font-size:9px;color:#448866;letter-spacing:1px">BTC/USDT</div>
            <div class="sym-price" style="color:var(--g)" id="btcPrice">$--</div>
            <div class="sym-delta" id="btcDelta" style="color:#448866">--</div>
          </div>
          <div class="sym-b eth">
            <div style="font-size:9px;color:#336688;letter-spacing:1px">ETH/USDT</div>
            <div class="sym-price" style="color:var(--b)" id="ethPrice">$--</div>
            <div class="sym-delta" id="ethDelta" style="color:#448866">--</div>
          </div>
        </div>
      </div>

      <!-- LLM CONFIDENCE HISTORY -->
      <div class="panel blue">
        <div class="panel-hdr"><span>LLM CONFIDENCE HISTORY</span></div>
        <div id="confList" style="padding:6px 0">
          <div style="color:#336677;text-align:center;padding:10px;font-size:10px">NO CALLS YET</div>
        </div>
      </div>

      <!-- BALANCE CHART -->
      <div class="panel">
        <div class="panel-hdr"><span>BALANCE CHART</span></div>
        <div class="chart-wrap">
          <canvas id="balChart"></canvas>
        </div>
      </div>

      <!-- LAYER-1 SIGNALS -->
      <div class="panel">
        <div class="panel-hdr">
          <span>LAYER-1 SIGNALS</span>
          <span id="l1status" style="font-size:9px;color:#448866">SCANNING</span>
        </div>
        <div id="l1panel">
          <div class="l1-row"><span class="l1-lbl">Win Delta BTC</span><span id="l1-wd-btc" style="color:var(--yellow)">0.000%</span></div>
          <div class="l1-row"><span class="l1-lbl">Momentum 30s BTC</span><span id="l1-m30-btc" style="color:var(--yellow)">0.000%</span></div>
          <div class="l1-row"><span class="l1-lbl">Vol Surge BTC</span><span id="l1-vol-btc" style="color:var(--yellow)">1.00x</span></div>
          <div class="l1-row"><span class="l1-lbl">Win Delta ETH</span><span id="l1-wd-eth" style="color:var(--b)">0.000%</span></div>
          <div class="l1-row"><span class="l1-lbl">Momentum 30s ETH</span><span id="l1-m30-eth" style="color:var(--b)">0.000%</span></div>
          <div class="l1-row" style="border:none"><span class="l1-lbl">Vol Surge ETH</span><span id="l1-vol-eth" style="color:var(--b)">1.00x</span></div>
        </div>
      </div>

    </div><!-- /sidebar -->
  </div><!-- /main-grid -->

</div><!-- /root -->

<script>
// ── STATE ──────────────────────────────────────────────────────────────────
const state = {
  lastData:       null,
  seenEventTs:    new Set(),      // deduplicate terminal events
  seenTradeIds:   new Set(),      // deduplicate trades by timestamp+symbol
  termLines:      [],
  maxTermLines:   120,
  balChart:       null,
  pollInterval:   500,            // ms between API polls
  staleThreshold: 30,             // seconds before dot turns red
};

// ── CLOCK ─────────────────────────────────────────────────────────────────
document.getElementById('boot-ts').textContent = new Date().toTimeString().slice(0,8);
function updateClock(){
  document.getElementById('clockEl').textContent =
    new Date().toUTCString().slice(17,25)+' UTC';
}
setInterval(updateClock, 1000); updateClock();

// ── RING TIMERS (pure local — same for BTC & ETH, 5min windows) ───────────
function updateTimers(){
  const now    = Math.floor(Date.now()/1000);
  const secLeft = 300 - (now % 300);
  const circ   = 175.9;
  ['btc','eth'].forEach(sym => {
    document.getElementById(sym+'Sec').textContent = secLeft;
    document.getElementById(sym+'Ring').style.strokeDashoffset =
      circ * (1 - secLeft/300);
  });
}
setInterval(updateTimers, 1000); updateTimers();

// ── BALANCE CHART ─────────────────────────────────────────────────────────
function initChart(){
  const ctx = document.getElementById('balChart').getContext('2d');
  state.balChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: ['0'],
      datasets: [{
        data: [100],
        borderColor: '#00ff88',
        backgroundColor: 'rgba(0,255,136,0.07)',
        fill: true, tension: 0.4, pointRadius: 2,
        pointBackgroundColor: '#00ff88', borderWidth: 1.5
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: {
          ticks: { color:'#448866', font:{size:9}, callback: v=>'$'+v.toFixed(0) },
          grid: { color:'#0a1f10' }, border: { color:'#0a1f10' }
        }
      }
    }
  });
}
initChart();

// ── TERMINAL ───────────────────────────────────────────────────────────────
function classifyEvent(msg){
  const m = msg.toUpperCase();
  if(m.startsWith('EXECUTED'))  return {tag:'EXEC',  cls:'tag-exec'};
  if(m.startsWith('SIGNAL'))    return {tag:'SIGNAL',cls:'tag-sig'};
  if(m.startsWith('SETTLED'))   return {tag:'SETTLE',cls:'tag-settle'};
  if(m.startsWith('NO_TRADE'))  return {tag:'SKIP',  cls:'tag-notrade'};
  if(m.startsWith('EXEC_FAIL') || m.startsWith('EXEC_ERROR'))
                                 return {tag:'ERROR', cls:'tag-loss'};
  if(m.includes('L1 PASS'))     return {tag:'L1',    cls:'tag-l1'};
  if(m.includes('WIN'))         return {tag:'WIN',   cls:'tag-win'};
  if(m.includes('LOSS'))        return {tag:'LOSS',  cls:'tag-loss'};
  return                               {tag:'SYS',   cls:'tag-sys'};
}

function colorize(msg){
  return msg
    .replace(/\bBTC\b/g,'<span class="hi">BTC</span>')
    .replace(/\bETH\b/g,'<span class="hib">ETH</span>')
    .replace(/BUY_YES/g,'<span class="hi">BUY_YES</span>')
    .replace(/BUY_NO/g, '<span class="hib">BUY_NO</span>')
    .replace(/WIN\b/g,  '<span class="hi">WIN</span>')
    .replace(/LOSS\b/g, '<span class="hir">LOSS</span>')
    .replace(/(\$[\d.]+)/g,'<span class="hiy">$1</span>')
    .replace(/(conf=\d+%)/g,'<span class="hi">$1</span>');
}

function addTermLine(ts, tag, cls, msg){
  const tb = document.getElementById('termBody');
  const div = document.createElement('div');
  div.className = 'log-line';
  div.innerHTML =
    `<span class="log-ts">${ts.slice(11,19)}</span>` +
    `<span class="log-tag ${cls}">[${tag}]</span>` +
    `<span class="log-msg">${colorize(msg)}</span>`;
  tb.appendChild(div);
  state.termLines.push(div);
  if(state.termLines.length > state.maxTermLines){
    const old = state.termLines.shift();
    if(old.parentNode) old.parentNode.removeChild(old);
  }
  tb.scrollTop = tb.scrollHeight;
}

// ── STATS UPDATE ──────────────────────────────────────────────────────────
function updateStats(s){
  const diff = s.current_balance - s.initial_balance;
  document.getElementById('s-init').textContent    = '$'+s.initial_balance.toFixed(2);

  const balEl = document.getElementById('s-bal');
  balEl.textContent  = '$'+s.current_balance.toFixed(2);
  balEl.className    = 'stat-val '+(s.current_balance >= s.initial_balance ? 'blue' : 'red');

  document.getElementById('s-bal-sub').textContent =
    (diff>=0?'+':'')+diff.toFixed(2)+' today';
  document.getElementById('s-trades').textContent  = s.total_trades;
  document.getElementById('s-wr').textContent      = 'W:'+s.wins+' / L:'+s.losses;

  const wr = s.win_rate;
  const wrEl = document.getElementById('s-winrate');
  wrEl.textContent = wr.toFixed(1)+'%';
  wrEl.className   = 'stat-val '+(wr>=50?'green':wr>0?'yellow':'red');

  document.getElementById('s-winrate-sub').textContent = (s.wins+s.losses)+' closed';

  const pnlEl = document.getElementById('s-pnl');
  pnlEl.textContent = (s.today_pnl>=0?'+':'')+Math.abs(s.today_pnl).toFixed(2);
  pnlEl.className   = 'stat-val '+(s.today_pnl>=0?'green':'red');

  document.getElementById('s-spent').textContent = '$'+s.daily_spent.toFixed(2);
  document.getElementById('s-llm').textContent   = s.llm_calls;
}

// ── SYMBOL SUMMARY → L1 PANEL + PRICES ───────────────────────────────────
function updateSymbolSummary(sym){
  const btc = sym.BTC, eth = sym.ETH;
  if(btc){
    document.getElementById('btcPrice').textContent  = '$'+parseFloat(btc.current_price).toFixed(0);
    const wd = parseFloat(btc.window_delta_pct);
    const m30= parseFloat(btc.momentum_30s);
    const vs = parseFloat(btc.vol_surge);
    const wdEl = document.getElementById('l1-wd-btc');
    wdEl.textContent  = (wd>=0?'+':'')+wd.toFixed(3)+'%';
    wdEl.style.color  = Math.abs(wd)>0.025?'var(--g)':'var(--yellow)';
    document.getElementById('btcDelta').textContent  = (wd>=0?'+':'')+wd.toFixed(3)+'%';
    document.getElementById('btcDelta').style.color  = wd>=0?'var(--g)':'var(--red)';
    const m30El = document.getElementById('l1-m30-btc');
    m30El.textContent = (m30>=0?'+':'')+m30.toFixed(3)+'%';
    m30El.style.color = Math.abs(m30)>0.035?'var(--g)':'var(--yellow)';
    const vsEl = document.getElementById('l1-vol-btc');
    vsEl.textContent  = vs.toFixed(2)+'x';
    vsEl.style.color  = vs>2.0?'var(--g)':'var(--yellow)';
  }
  if(eth){
    document.getElementById('ethPrice').textContent  = '$'+parseFloat(eth.current_price).toFixed(2);
    const wd = parseFloat(eth.window_delta_pct);
    const m30= parseFloat(eth.momentum_30s);
    const vs = parseFloat(eth.vol_surge);
    const wdEl = document.getElementById('l1-wd-eth');
    wdEl.textContent  = (wd>=0?'+':'')+wd.toFixed(3)+'%';
    wdEl.style.color  = Math.abs(wd)>0.025?'var(--b)':'var(--b)';
    document.getElementById('ethDelta').textContent  = (wd>=0?'+':'')+wd.toFixed(3)+'%';
    document.getElementById('ethDelta').style.color  = wd>=0?'var(--b)':'var(--red)';
    const m30El = document.getElementById('l1-m30-eth');
    m30El.textContent = (m30>=0?'+':'')+m30.toFixed(3)+'%';
    m30El.style.color = 'var(--b)';
    const vsEl = document.getElementById('l1-vol-eth');
    vsEl.textContent  = vs.toFixed(2)+'x';
    vsEl.style.color  = vs>2.0?'var(--b)':'var(--b)';
  }
}

// ── CONFIDENCE HISTORY ────────────────────────────────────────────────────
function updateConfHistory(hist){
  const cl = document.getElementById('confList');
  if(!hist || hist.length===0){
    cl.innerHTML = '<div style="color:#336677;text-align:center;padding:10px;font-size:10px">NO CALLS YET</div>';
    return;
  }
  cl.innerHTML = hist.map(c=>{
    const pct = c.confidence;
    const col = pct>=80?'#00ff88':pct>=60?'#ffd700':'#ff3b3b';
    const sym = c.symbol;
    return `<div class="conf-row">
      <span class="conf-label" style="color:${sym==='BTC'?'var(--g)':'var(--b)'}">${sym} ${c.action||''}</span>
      <div class="conf-bar-bg"><div class="conf-bar" style="width:${pct}%;background:${col}"></div></div>
      <span class="conf-val" style="color:${col}">${pct}%</span>
    </div>`;
  }).join('');
}

// ── BALANCE CHART UPDATE ─────────────────────────────────────────────────
function updateBalChart(history){
  if(!history || history.length===0) return;
  const labels = history.map((h,i)=>i===0?'0':h.timestamp.slice(11,16));
  const data   = history.map(h=>parseFloat(h.balance));
  state.balChart.data.labels   = labels;
  state.balChart.data.datasets[0].data = data;
  state.balChart.update('none');
}

// ── TRADE TABLE ────────────────────────────────────────────────────────────
function renderTradeTable(trades){
  const tbody = document.getElementById('tradeBody');
  if(!trades || trades.length===0){
    tbody.innerHTML='<tr><td colspan="9" style="color:#336655;text-align:center;padding:14px;font-size:10px">AWAITING SIGNALS...</td></tr>';
    document.getElementById('trade-count-lbl').textContent='0 TRADES';
    return;
  }
  document.getElementById('trade-count-lbl').textContent = trades.length+' TRADES';

  // Open trades count
  const openCount = trades.filter(t=>t.outcome==='OPEN').length;
  document.getElementById('open-count').textContent = openCount+' OPEN';

  tbody.innerHTML = trades.slice().reverse().slice(0,25).map(t=>{
    const dir = t.direction==='BUY_YES'?
      '<span class="badge-yes">YES</span>':'<span class="badge-no">NO</span>';
    const pnlHtml = (t.pnl!==null && t.pnl!==undefined && t.outcome!=='OPEN')?
      `<span class="${t.pnl>=0?'pnl-pos':'pnl-neg'}">${t.pnl>=0?'+':''}$${Math.abs(t.pnl).toFixed(2)}</span>`:
      '<span style="color:#448866">OPEN</span>';
    const outHtml = t.outcome==='WIN'?
      `<span style="color:var(--g)">WIN</span>`:
      t.outcome==='LOSS'?
      `<span style="color:var(--red)">LOSS</span>`:
      `<span style="color:#888">OPEN</span>`;
    const reason = (t.reasoning||'').length>30 ? (t.reasoning||'').slice(0,30)+'…' : (t.reasoning||'');
    const entry  = parseFloat(t.entry_price||0);
    const size   = parseFloat(t.trade_size_usd||0);
    const conf   = parseInt(t.confidence||0);
    return `<tr>
      <td style="color:#448866">${(t.timestamp||'').slice(11,19)}</td>
      <td style="color:${t.symbol==='BTC'?'var(--g)':'var(--b)'};font-weight:700">${t.symbol}</td>
      <td>${dir}</td>
      <td style="color:#aaccaa">${entry.toFixed(4)}</td>
      <td style="color:var(--yellow)">$${size.toFixed(2)}</td>
      <td style="color:${conf>=80?'var(--g)':conf>=60?'var(--yellow)':'var(--red)'}">${conf}%</td>
      <td>${outHtml}</td>
      <td>${pnlHtml}</td>
      <td style="color:#668877;font-size:9px">${reason}</td>
    </tr>`;
  }).join('');
  updateOpenMetrics(trades);
}

function updateOpenMetrics(trades){
  const openTrades = (trades||[]).filter(t=>t.outcome==='OPEN');
  const openPnl = openTrades.reduce((sum,t)=>(sum + (parseFloat(t.pnl)||0)),0);
  const toWin = openTrades.reduce((sum,t)=>{
    const entry = parseFloat(t.entry_price||0);
    const size  = parseFloat(t.trade_size_usd||0);
    return sum + (entry > 0 ? size / entry : 0);
  },0);

  const valEl = document.getElementById('s-value');
  valEl.textContent = (openPnl>=0?'+':'')+Math.abs(openPnl).toFixed(2);
  valEl.className   = 'stat-val '+(openPnl>=0?'green':'red');

  document.getElementById('s-win-potential').textContent = '$'+toWin.toFixed(2);
}

// ── TERMINAL — sync from live_events ─────────────────────────────────────
function syncTerminal(events){
  if(!events || events.length===0) return;
  events.forEach(ev=>{
    const key = ev.timestamp + ev.message;
    if(state.seenEventTs.has(key)) return;
    state.seenEventTs.add(key);
    if(state.seenEventTs.size > 500){
      const iter = state.seenEventTs.values();
      state.seenEventTs.delete(iter.next().value);
    }
    const {tag, cls} = classifyEvent(ev.message);
    addTermLine(ev.timestamp, tag, cls, ev.message);
  });
}

// ── STALE CHECK ─────────────────────────────────────────────────────────
function checkStale(updatedAt){
  if(!updatedAt) return;
  const now  = Date.now()/1000;
  const upd  = new Date(updatedAt).getTime()/1000;
  const diff = now - upd;
  const dot  = document.getElementById('liveDot');
  if(diff > state.staleThreshold){
    dot.className='stale-dot';
    document.getElementById('lastUpd').textContent = 'STALE — '+Math.round(diff)+'s ago';
  } else {
    dot.className='live-dot';
    document.getElementById('lastUpd').textContent = 'updated '+Math.round(diff)+'s ago';
  }
}

// ── MAIN POLL LOOP ────────────────────────────────────────────────────────
async function poll(){
  try {
    const resp = await fetch('/data?t='+Date.now());
    if(!resp.ok){ console.warn('poll error', resp.status); return; }
    const d = await resp.json();
    state.lastData = d;

    if(d.stats)          updateStats(d.stats);
    if(d.symbol_summary) updateSymbolSummary(d.symbol_summary);
    if(d.llm_confidence_history) updateConfHistory(d.llm_confidence_history);
    if(d.balance_history)        updateBalChart(d.balance_history);
    if(d.trade_history)          renderTradeTable(d.trade_history);
    if(d.live_events)            syncTerminal(d.live_events);
    checkStale(d.updated_at);

    document.getElementById('pingEl').textContent = '● LIVE';
    document.getElementById('pingEl').style.color  = '#00ff88';
  } catch(e){
    console.error('poll failed', e);
    document.getElementById('pingEl').textContent = '● OFFLINE';
    document.getElementById('pingEl').style.color  = '#ff3b3b';
    document.getElementById('liveDot').className   = 'stale-dot';
  }
}

setInterval(poll, state.pollInterval);
poll();
</script>
</body>
</html>
"""

# ─────────────────────────────────────────────────────────────────────────────
# HTTP REQUEST HANDLER
# ─────────────────────────────────────────────────────────────────────────────
class DashboardHandler(http.server.BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        pass  # silence default access log

    def do_GET(self):
        path = self.path.split("?")[0]

        if path == "/" or path == "/index.html":
            self._send_html(DASHBOARD_HTML.encode("utf-8"))

        elif path == "/data":
            self._send_json()

        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")

    # ── helpers ─────────────────────────────────────────────────────────────────
    def _send_html(self, body: bytes):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self):
        data = _read_dashboard_json()
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


def _read_dashboard_json() -> dict:
    """dashboard_data.json ko read karo. Agar file nahi hai toh empty state return karo."""
    if not os.path.exists(DASHBOARD_JSON):
        return {
            "updated_at": None,
            "stats": {
                "initial_balance": 100, "current_balance": 100,
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "today_pnl": 0,
                "daily_spent": 0, "llm_calls": 0,
            },
            "live_events": [{"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "message": f"Waiting for bot... ({DASHBOARD_JSON} not found yet)"}],
            "trade_history": [],
            "symbol_summary": {},
            "llm_confidence_history": [],
            "balance_history": [],
        }
    try:
        with open(DASHBOARD_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"error": str(e), "updated_at": None}

# ─────────────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def main():
    server = http.server.ThreadingHTTPServer((HOST, PORT), DashboardHandler)

    # Show which directory we're watching
    abs_json = os.path.abspath(DASHBOARD_JSON)
    bar      = "─" * 56

    print(f"\n{bar}")
    print(f"  🖥️   SNIPER DASHBOARD SERVER")
    print(f"{bar}")
    print(f"  URL  : http://{HOST}:{PORT}")
    print(f"  Data : {abs_json}")
    print(f"  Poll : every 0.5s (browser-side)")
    print(f"{bar}")
    print(f"  Bot aur ye server DONO alag terminals mein run karo.")
    print(f"  Bot → python sniper_bot.py")
    print(f"  Dash → python dashboard_server.py   (ye wala)")
    print(f"{bar}\n")
    print(f"  Ctrl+C se band karo.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Dashboard server band kiya.")
        server.shutdown()


if __name__ == "__main__":
    main()
