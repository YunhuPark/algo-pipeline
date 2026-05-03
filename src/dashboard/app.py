"""
알고 웹 대시보드 — Flask
────────────────────────────────────────────────────────
실행: python src/dashboard/app.py
접속: http://localhost:5000

페이지:
  /           메인 대시보드 (게시물 현황, 큐, 성과 요약)
  /queue      큐 관리 (추가/스킵/자동생성)
  /analytics  성과 분석 (테이블 + 차트)
  /settings   설정 (persona.json 편집, API 키 현황)
"""
from __future__ import annotations

import io
import json
import os
import queue
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

from flask import Flask, request, redirect, url_for, send_file, Response, stream_with_context

from src.db import (
    get_posts, get_analytics, get_queue,
    enqueue, mark_queue_status, queue_count,
)

app = Flask(__name__)

# ── 생성 작업 상태 저장 (job_id → dict) ───────────────────
_JOBS: dict[str, dict] = {}   # {job_id: {status, logs, paths, script, error}}
_JOB_QUEUES: dict[str, queue.Queue] = {}   # SSE 이벤트 큐

# ── 공통 CSS / 레이아웃 ──────────────────────────────────
_CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
@import url('https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable/pretendardvariable.min.css');

:root {
  --bg:       #121318;
  --surface:  #1A1B23;
  --surface2: #22232E;
  --surface3: #2C2D3A;
  --border:   rgba(255,255,255,.06);
  --accent:   #3182F6;
  --accent-light: #6BB5FF;
  --accent2:  #00C9A7;
  --pink:     #FE6B8B;
  --pink-light: #FF8E53;
  --danger:   #FF4757;
  --success:  #00C9A7;
  --warn:     #FFB84D;
  --text:     #E8E8F0;
  --text-secondary: #8B8B9E;
  --muted:    #5C5C6F;
  --radius:   16px;
  --radius-sm: 12px;
  --radius-xs: 8px;
  --radius-pill: 100px;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
  background: var(--bg);
  color: var(--text);
  font-family: 'Pretendard Variable', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  font-size: 14px;
  min-height: 100vh;
  line-height: 1.65;
  letter-spacing: -0.01em;
  word-break: keep-all;
  -webkit-font-smoothing: antialiased;
}

a { color: var(--accent); text-decoration: none; transition: all .2s ease; }
a:hover { color: var(--accent-light); }

/* ── Sidebar nav ── */
.layout { display: flex; min-height: 100vh; }

.sidebar {
  width: 240px;
  background: rgba(26,27,35,.92);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  display: flex;
  flex-direction: column;
  padding: 28px 16px;
  position: fixed;
  top: 0; left: 0; bottom: 0;
  z-index: 100;
}

.sidebar .brand {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 4px 12px 28px;
  margin-bottom: 24px;
  position: relative;
}
.sidebar .brand::after {
  content: '';
  position: absolute;
  bottom: 0; left: 12px; right: 12px;
  height: 1px;
  background: linear-gradient(90deg, var(--accent), transparent);
}
.sidebar .brand-icon {
  width: 40px; height: 40px;
  background: linear-gradient(135deg, var(--accent), var(--accent-light));
  border-radius: 12px;
  display: flex; align-items: center; justify-content: center;
  font-size: 20px;
  box-shadow: 0 4px 16px rgba(49,130,246,.25);
}
.sidebar .brand-name {
  font-size: 18px; font-weight: 800; color: #fff;
  letter-spacing: -0.03em;
}
.sidebar .brand-sub {
  font-size: 11px; color: var(--text-secondary); margin-top: 2px;
  letter-spacing: 0;
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 14px;
  font-weight: 500;
  transition: all .2s ease;
  margin-bottom: 4px;
  cursor: pointer;
}
.nav-item:hover {
  background: rgba(49,130,246,.08);
  color: var(--text);
  transform: translateX(2px);
}
.nav-item.active {
  background: rgba(49,130,246,.12);
  color: var(--accent);
  font-weight: 600;
}
.nav-item .icon { font-size: 18px; width: 24px; text-align: center; }

.sidebar-bottom {
  margin-top: auto;
  padding: 16px 12px;
  border-radius: var(--radius-sm);
  background: rgba(49,130,246,.06);
  font-size: 12px;
  color: var(--text-secondary);
}

/* ── Main content ── */
.main { margin-left: 240px; flex: 1; min-height: 100vh; }

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 20px 36px;
  background: rgba(26,27,35,.85);
  backdrop-filter: blur(24px);
  -webkit-backdrop-filter: blur(24px);
  position: sticky; top: 0; z-index: 50;
}
.topbar h1 {
  font-size: 20px; font-weight: 700; color: #fff;
  letter-spacing: -0.03em;
}
.topbar .status-dot {
  width: 8px; height: 8px; border-radius: 50%;
  background: var(--success);
  display: inline-block; margin-right: 8px;
  box-shadow: 0 0 8px var(--success);
  animation: pulse-dot 2s infinite;
}
@keyframes pulse-dot {
  0%, 100% { opacity: 1; box-shadow: 0 0 8px var(--success); }
  50% { opacity: .6; box-shadow: 0 0 16px var(--success); }
}

.content { padding: 32px 36px; }

/* ── Cards / Stats ── */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 16px;
  margin-bottom: 32px;
}
.stat-card {
  background: var(--surface);
  border-radius: var(--radius);
  padding: 24px;
  position: relative;
  overflow: hidden;
  transition: all .25s ease;
}
.stat-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0,0,0,.2);
}
.stat-card::before {
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--accent), var(--accent-light));
  border-radius: var(--radius) var(--radius) 0 0;
}
.stat-card:nth-child(2)::before { background: linear-gradient(90deg, var(--pink), var(--pink-light)); }
.stat-card:nth-child(3)::before { background: linear-gradient(90deg, var(--accent2), #00E5FF); }
.stat-card:nth-child(4)::before { background: linear-gradient(90deg, var(--warn), #FFD700); }
.stat-card .val {
  font-size: 36px;
  font-weight: 800;
  color: #fff;
  line-height: 1;
  margin-bottom: 8px;
  letter-spacing: -0.03em;
  font-family: 'Inter', sans-serif;
}
.stat-card .lbl { font-size: 13px; color: var(--text-secondary); font-weight: 500; }
.stat-card .icon-bg {
  position: absolute; right: 20px; top: 50%;
  transform: translateY(-50%);
  font-size: 48px; opacity: .06;
}

/* ── Panel / Section ── */
.panel {
  background: var(--surface);
  border-radius: var(--radius);
  padding: 24px;
  margin-bottom: 20px;
  transition: all .2s ease;
}
.panel:hover {
  box-shadow: 0 4px 16px rgba(0,0,0,.1);
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20px;
}
.panel-title {
  font-size: 16px;
  font-weight: 700;
  color: #fff;
  letter-spacing: -0.02em;
}
.panel-sub {
  font-size: 13px;
  color: var(--text-secondary);
  margin-top: 4px;
}

/* ── Table ── */
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
thead th {
  background: var(--surface2);
  color: var(--text-secondary);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: .05em;
  padding: 12px 16px;
  text-align: left;
  border-radius: var(--radius-xs) var(--radius-xs) 0 0;
}
thead th:first-child { border-radius: var(--radius-xs) 0 0 0; }
thead th:last-child { border-radius: 0 var(--radius-xs) 0 0; }
tbody td {
  padding: 14px 16px;
  font-size: 13px;
  color: var(--text);
  border-bottom: 1px solid var(--border);
}
tbody tr:last-child td { border-bottom: none; }
tbody tr { transition: background .15s ease; }
tbody tr:hover td { background: rgba(49,130,246,.04); }

/* ── Badges ── */
.badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 4px 12px;
  border-radius: var(--radius-pill);
  font-size: 11px;
  font-weight: 600;
}
.badge-pending  { background: rgba(255,184,77,.12);  color: var(--warn); }
.badge-published{ background: rgba(0,201,167,.12);   color: var(--success); }
.badge-skipped  { background: rgba(92,92,111,.12);   color: var(--muted); }
.badge-ig       { background: rgba(254,107,139,.12);  color: var(--pink); }
.badge-instagram{ background: rgba(254,107,139,.12);  color: var(--pink); }
.badge-threads  { background: rgba(49,130,246,.12);   color: var(--accent); }
.badge-blog     { background: rgba(0,201,167,.12);    color: var(--accent2); }

/* ── Buttons ── */
.btn {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 10px 20px;
  border-radius: var(--radius-pill);
  font-size: 13px;
  font-weight: 600;
  border: none;
  cursor: pointer;
  transition: all .2s ease;
  white-space: nowrap;
  letter-spacing: -0.01em;
}
.btn-primary {
  background: linear-gradient(135deg, var(--accent), var(--accent-light));
  color: #fff;
  box-shadow: 0 4px 12px rgba(49,130,246,.25);
}
.btn-primary:hover {
  transform: translateY(-1px);
  box-shadow: 0 6px 20px rgba(49,130,246,.35);
}
.btn-secondary {
  background: var(--surface2);
  color: var(--text);
}
.btn-secondary:hover {
  background: var(--surface3);
  color: var(--accent);
}
.btn-danger {
  background: rgba(255,71,87,.1);
  color: var(--danger);
}
.btn-danger:hover { background: var(--danger); color: #fff; }
.btn-success {
  background: rgba(0,201,167,.1);
  color: var(--success);
}
.btn-success:hover { background: var(--success); color: #fff; }
.btn-lg {
  padding: 14px 32px;
  font-size: 15px;
  border-radius: var(--radius-pill);
  font-weight: 700;
}
.btn:disabled { opacity: .4; cursor: not-allowed; }

/* ── Form inputs ── */
.input, input[type=text], input[type=number], input[type=datetime-local],
select, textarea {
  background: var(--surface3);
  border: 1px solid var(--border);
  color: #ffffff !important;
  padding: 12px 16px;
  border-radius: var(--radius-sm);
  font-size: 14px;
  width: 100%;
  transition: all .2s ease;
  outline: none;
  font-family: inherit;
  line-height: 1.6;
}
.input:focus, input:focus, select:focus, textarea:focus {
  background: var(--surface2);
  box-shadow: 0 0 0 3px rgba(49,130,246,.15);
}
.input::placeholder, input::placeholder, textarea::placeholder {
  color: var(--muted);
}
.input-label {
  font-size: 13px; color: var(--text-secondary);
  margin-bottom: 8px; display: block; font-weight: 600;
}
.input-group { margin-bottom: 16px; }

/* ── Alerts ── */
.alert {
  display: flex; align-items: center; gap: 10px;
  padding: 14px 18px;
  border-radius: var(--radius-sm);
  font-size: 13px;
  margin-bottom: 20px;
  font-weight: 500;
}
.alert-ok  { background: rgba(0,201,167,.08); color: var(--accent2); }
.alert-err { background: rgba(255,71,87,.08); color: var(--danger); }
.alert-warn{ background: rgba(255,184,77,.08); color: var(--warn); }

/* ── Toggle / Checkbox ── */
.toggle-row {
  display: flex; align-items: center; gap: 10px;
  margin-bottom: 12px; cursor: pointer;
}
.toggle-row input[type=checkbox] {
  width: 18px; height: 18px;
  accent-color: var(--accent);
  cursor: pointer;
  border-radius: 4px;
}
.toggle-row span { font-size: 14px; color: var(--text); font-weight: 500; }

/* ── Log box ── */
.logbox {
  background: #0A0A12;
  border-radius: var(--radius-sm);
  padding: 16px 18px;
  height: 260px;
  overflow-y: auto;
  font-size: 12px;
  font-family: 'JetBrains Mono', 'Consolas', monospace;
  color: var(--muted);
  line-height: 1.9;
}
.logbox .log-step { color: var(--accent); font-weight: 600; }
.logbox .log-done { color: var(--success); }
.logbox .log-err  { color: var(--danger); }

/* ── Card grid (image preview) ── */
.card-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(148px, 1fr));
  gap: 12px;
}
.card-thumb {
  position: relative;
  border-radius: var(--radius-sm);
  overflow: hidden;
  aspect-ratio: 4/5;
  background: var(--surface2);
  cursor: pointer;
  transition: all .25s ease;
}
.card-thumb:hover {
  transform: scale(1.03);
  box-shadow: 0 8px 24px rgba(0,0,0,.3);
}
.card-thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.card-thumb .num-badge {
  position: absolute; bottom: 8px; left: 8px;
  background: rgba(0,0,0,.65);
  backdrop-filter: blur(8px);
  border-radius: var(--radius-xs); padding: 3px 8px;
  font-size: 10px; color: #fff; font-weight: 600;
}

/* ── History list ── */
.history-item {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 0;
  transition: background .15s;
}
.history-thumb {
  width: 48px; height: 60px;
  border-radius: var(--radius-xs); object-fit: cover;
  background: var(--surface2);
  flex-shrink: 0;
}
.history-info { flex: 1; min-width: 0; }
.history-topic {
  font-size: 14px; font-weight: 600; color: #fff;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.history-meta { font-size: 12px; color: var(--text-secondary); margin-top: 4px; }
.history-actions { display: flex; gap: 8px; flex-shrink: 0; }

/* ── Progress bar ── */
.progress-bar {
  height: 6px;
  background: var(--surface3);
  border-radius: var(--radius-pill);
  overflow: hidden;
  margin: 12px 0;
}
.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent-light));
  border-radius: var(--radius-pill);
  transition: width .4s ease;
  animation: shimmer 2s ease infinite;
}
@keyframes shimmer {
  0% { opacity: 1; } 50% { opacity: .65; } 100% { opacity: 1; }
}

/* ── Divider ── */
.divider {
  height: 1px;
  background: var(--border);
  margin: 24px 0;
}

/* ── Empty state ── */
.empty { text-align: center; padding: 48px 24px; color: var(--text-secondary); }
.empty .empty-icon { font-size: 48px; margin-bottom: 16px; opacity: .5; }
.empty p { font-size: 14px; line-height: 1.6; }

/* ── Scrollbar ── */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--surface3); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--muted); }

/* ── Responsive ── */
@media (max-width: 768px) {
  .sidebar { display: none; }
  .main { margin-left: 0; }
  .content { padding: 20px; }
  .stat-grid { grid-template-columns: 1fr 1fr; }
  .topbar { padding: 16px 20px; }
}

/* ── Micro animations ── */
@keyframes fadeIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: none; } }
.panel { animation: fadeIn .3s ease; }
.stat-card { animation: fadeIn .4s ease; }

/* ── Lightbox Modal ── */
.lightbox-overlay {
  position: fixed; inset: 0;
  background: rgba(0,0,0,.92);
  backdrop-filter: blur(16px);
  -webkit-backdrop-filter: blur(16px);
  z-index: 9999;
  display: none; align-items: center; justify-content: center;
  opacity: 0; transition: opacity .25s ease;
}
.lightbox-overlay.active { display: flex; opacity: 1; }
.lightbox-overlay .lb-img {
  max-width: 85vw; max-height: 88vh;
  border-radius: var(--radius);
  box-shadow: 0 24px 80px rgba(0,0,0,.6);
  object-fit: contain;
  animation: lbZoomIn .3s ease;
}
@keyframes lbZoomIn { from { transform: scale(.85); opacity: 0; } to { transform: scale(1); opacity: 1; } }
.lightbox-overlay video::cue {
  background: rgba(0,0,0,.75); color: #fff;
  font-size: 1.1em; font-family: 'Pretendard Variable', sans-serif;
  line-height: 1.5; padding: 2px 6px; border-radius: 3px;
}
.lightbox-overlay .lb-close {
  position: absolute; top: 24px; right: 28px;
  width: 44px; height: 44px;
  background: rgba(255,255,255,.1); border: none; border-radius: 50%;
  color: #fff; font-size: 22px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: background .2s;
}
.lightbox-overlay .lb-close:hover { background: rgba(255,255,255,.2); }
.lightbox-overlay .lb-nav {
  position: absolute; top: 50%; transform: translateY(-50%);
  width: 48px; height: 48px;
  background: rgba(255,255,255,.08); border: none; border-radius: 50%;
  color: #fff; font-size: 24px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  transition: all .2s;
}
.lightbox-overlay .lb-nav:hover { background: rgba(255,255,255,.18); transform: translateY(-50%) scale(1.08); }
.lightbox-overlay .lb-prev { left: 24px; }
.lightbox-overlay .lb-next { right: 24px; }
.lightbox-overlay .lb-counter {
  position: absolute; bottom: 28px; left: 50%; transform: translateX(-50%);
  background: rgba(255,255,255,.1); backdrop-filter: blur(8px);
  border-radius: var(--radius-pill); padding: 6px 18px;
  font-size: 13px; color: rgba(255,255,255,.8); font-weight: 600;
}

/* ── Example chips ── */
.example-chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.example-chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 6px 14px;
  background: rgba(49,130,246,.08); border: 1px solid rgba(49,130,246,.15);
  border-radius: var(--radius-pill);
  font-size: 12px; font-weight: 500; color: var(--accent);
  cursor: pointer; transition: all .2s ease;
}
.example-chip:hover { background: rgba(49,130,246,.18); border-color: var(--accent); transform: translateY(-1px); }
"""


def _nav(active: str) -> str:
    items = [
        ("/",          "🏠", "대시보드"),
        ("/generate",  "✨", "생성"),
        ("/queue",     "📋", "큐"),
        ("/analytics", "📊", "분석"),
        ("/settings",  "⚙️",  "설정"),
    ]
    links = "".join(
        f'<a href="{href}" class="nav-item {"active" if active == href else ""}">'
        f'<span class="icon">{icon}</span>{label}</a>'
        for href, icon, label in items
    )
    return f"""
<div class="sidebar">
  <div class="brand">
    <div class="brand-icon">📰</div>
    <div>
      <div class="brand-name">알고</div>
      <div class="brand-sub">AI 카드뉴스 자동화</div>
    </div>
  </div>
  {links}
  <div class="sidebar-bottom">
    <span style="color:var(--success)">●</span> 자동화 엔진 실행 중<br>
    <span style="opacity:.6;font-size:11px">algo__kr · v2.0</span>
  </div>
</div>"""


def _page(title: str, active: str, body: str, msg: str = "", err: str = "") -> str:
    alert = ""
    if msg:
        alert = f'<div class="alert alert-ok">✓ {msg}</div>'
    if err:
        alert = f'<div class="alert alert-err">✕ {err}</div>'
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title} — 알고</title>
  <style>{_CSS}</style>
</head>
<body>
<div class="layout">
  {_nav(active)}
  <div class="main">
    <div class="topbar">
      <h1>{title}</h1>
      <div style="font-size:12px;color:var(--muted)">
        <span class="status-dot"></span>시스템 정상
      </div>
    </div>
    <div class="content">
      {alert}{body}
    </div>
  </div>
</div>
<div class="lightbox-overlay" id="lightbox">
  <button class="lb-close" onclick="closeLightbox()">&times;</button>
  <button class="lb-nav lb-prev" onclick="navLightbox(-1)">&#8249;</button>
  <button class="lb-nav lb-next" onclick="navLightbox(1)">&#8250;</button>
  <div id="lbContentWrapper" style="display:flex;justify-content:center;align-items:center;max-width:85vw;max-height:88vh;"></div>
  <div class="lb-counter" id="lbCounter"></div>
</div>
<script>
let lbImages=[],lbVideoIds=[],lbIndex=0;
function _renderLb(){{
  const w=document.getElementById('lbContentWrapper');
  const src=lbImages[lbIndex];
  const vid=lbVideoIds[lbIndex]||'';
  if(src.toLowerCase().endsWith('.mp4')){{
    const trackHtml=vid?`<track kind="subtitles" src="/subtitle/${{vid}}" srclang="ko" label="한국어" default>`:'';
    w.innerHTML=`<video class="lb-img" src="${{src}}" controls autoplay loop playsinline crossorigin="anonymous">${{trackHtml}}</video>`;
    if(vid){{
      const v=w.querySelector('video');
      v.addEventListener('loadedmetadata',()=>{{if(v.textTracks[0])v.textTracks[0].mode='showing';}});
    }}
  }}else{{
    w.innerHTML=`<img class="lb-img" src="${{src}}">`;
  }}
}}
function openLightbox(src,all,idx,vids){{lbImages=all||[src];lbVideoIds=vids||[];lbIndex=idx||0;document.getElementById('lightbox').classList.add('active');document.getElementById('lbCounter').textContent=(lbIndex+1)+' / '+lbImages.length;document.body.style.overflow='hidden';_renderLb();}}
function closeLightbox(){{document.getElementById('lightbox').classList.remove('active');document.body.style.overflow='';document.getElementById('lbContentWrapper').innerHTML='';}}
function navLightbox(d){{lbIndex=(lbIndex+d+lbImages.length)%lbImages.length;document.getElementById('lbCounter').textContent=(lbIndex+1)+' / '+lbImages.length;_renderLb();}}
document.getElementById('lightbox').addEventListener('click',e=>{{if(e.target.id==='lightbox')closeLightbox();}});
document.addEventListener('keydown',e=>{{if(!document.getElementById('lightbox').classList.contains('active'))return;if(e.key==='Escape')closeLightbox();if(e.key==='ArrowLeft')navLightbox(-1);if(e.key==='ArrowRight')navLightbox(1);}});
</script>
</body></html>"""


# ── Instagram OAuth 콜백 ─────────────────────────────────
_ig_oauth_code: dict = {}

@app.route("/callback")
def ig_oauth_callback():
    from flask import request, Response
    code = request.args.get("code")
    error = request.args.get("error")
    if code:
        _ig_oauth_code["code"] = code
        return Response("<h2>Auth complete! Close this window.</h2>", mimetype="text/html")
    return Response(f"<h2>Auth failed: {error}</h2>", mimetype="text/html", status=400)

@app.route("/callback/code")
def ig_oauth_get_code():
    from flask import jsonify
    return jsonify(_ig_oauth_code)


# ── / 메인 대시보드 ───────────────────────────────────────

@app.route("/")
def index():
    posts = get_posts(limit=100)
    today = datetime.now().strftime("%Y-%m-%d")
    week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    total = len(posts)
    today_cnt = sum(1 for p in posts if str(p["posted_at"]).startswith(today))
    week_cnt = sum(1 for p in posts if str(p["posted_at"]) >= week_ago)
    q_cnt = queue_count("pending")

    analytics = get_analytics(limit=20)
    top_posts = sorted(analytics, key=lambda r: r["likes"], reverse=True)[:3]

    # 플랫폼별 집계
    platforms: dict[str, int] = {}
    for p in posts:
        pf = p["platform"] or "unknown"
        platforms[pf] = platforms.get(pf, 0) + 1

    # 최근 게시물 5개
    recent = list(posts)[:5]

    # stat cards
    stats = f"""
    <div class="stat-grid">
      <div class="stat-card">
        <div class="val">{today_cnt}</div>
        <div class="lbl">오늘 게시물</div>
        <div class="icon-bg">📅</div>
      </div>
      <div class="stat-card">
        <div class="val">{week_cnt}</div>
        <div class="lbl">이번주 게시물</div>
        <div class="icon-bg">📆</div>
      </div>
      <div class="stat-card">
        <div class="val">{total}</div>
        <div class="lbl">전체 게시물</div>
        <div class="icon-bg">📰</div>
      </div>
      <div class="stat-card">
        <div class="val">{q_cnt}</div>
        <div class="lbl">큐 대기 중</div>
        <div class="icon-bg">📋</div>
      </div>
    </div>"""

    # 플랫폼별
    pf_badges = "".join(
        f'<span class="badge badge-{pf}">{pf} {cnt}</span>&nbsp;'
        for pf, cnt in platforms.items()
    )

    # 최근 게시물 테이블
    recent_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td><span class='badge badge-{}'>{}</span></td><td>{}</td></tr>".format(
            p['topic'], p['angle'] or '-', p['platform'], p['platform'], str(p['posted_at'])[:16]
        )
        for p in recent
    )

    # 상위 성과
    top_rows = "".join(
        f"<tr><td>{r['topic']}</td><td>{r['angle'] or '-'}</td>"
        f"<td>❤️ {r['likes']}</td><td>💬 {r['comments']}</td><td>🔖 {r['saves']}</td></tr>"
        for r in top_posts
    ) or "<tr><td colspan='5' style='color:var(--muted)'>데이터 없음</td></tr>"

    body = f"""
    {stats}
    <div style="margin-bottom:16px">{pf_badges}</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
      <div class="panel">
        <div class="panel-header">
          <div>
            <div class="panel-title">최근 게시물</div>
            <div class="panel-sub">최근 5개</div>
          </div>
          <a href="/queue" class="btn btn-secondary" style="font-size:12px;padding:6px 14px">큐 관리 →</a>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>주제</th><th>앵글</th><th>플랫폼</th><th>날짜</th></tr></thead>
            <tbody>{recent_rows}</tbody>
          </table>
        </div>
      </div>
      <div class="panel">
        <div class="panel-header">
          <div>
            <div class="panel-title">성과 Top 3</div>
            <div class="panel-sub">좋아요 기준</div>
          </div>
          <a href="/analytics" class="btn btn-secondary" style="font-size:12px;padding:6px 14px">전체 보기 →</a>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>주제</th><th>앵글</th><th>❤️</th><th>💬</th><th>🔖</th></tr></thead>
            <tbody>{top_rows}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return _page("대시보드", "/", body)


# ── /queue 큐 관리 ────────────────────────────────────────

@app.route("/queue")
def queue_page():
    msg = request.args.get("msg", "")
    err = request.args.get("err", "")
    rows = get_queue()

    def _badge(s):
        cls = {"pending": "pending", "published": "published", "skipped": "skipped"}.get(s, "pending")
        return f'<span class="badge badge-{cls}">{s}</span>'

    trs = "".join(
        f"<tr><td style='color:var(--muted);font-size:12px'>#{r['id']}</td>"
        f"<td style='font-weight:500'>{r['topic']}</td>"
        f"<td>{_badge(r['status'])}</td>"
        f"<td style='color:var(--muted);font-size:12px'>{r['scheduled_at'] or '다음 차례'}</td>"
        f"<td><form method='post' action='/queue/skip/{r['id']}' style='margin:0'>"
        f"<button class='btn btn-danger' style='padding:5px 12px;font-size:12px'>건너뜀</button></form></td></tr>"
        for r in rows
    ) or "<tr><td colspan='5' class='empty' style='padding:30px'><div class='empty-icon'>📭</div><p>큐가 비어있습니다</p></td></tr>"

    body = f"""
    <div style="display:grid;grid-template-columns:1fr 320px;gap:20px;align-items:start">
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">예약 목록</div>
          <span class="badge badge-pending">{len(rows)}개 대기</span>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>#</th><th>주제</th><th>상태</th><th>예약 시간</th><th></th></tr></thead>
            <tbody>{trs}</tbody>
          </table>
        </div>
      </div>
      <div>
        <div class="panel" style="margin-bottom:16px">
          <div class="panel-header">
            <div class="panel-title">주제 직접 추가</div>
          </div>
          <form method="post" action="/queue/add">
            <div class="input-group">
              <label class="input-label">주제</label>
              <input name="topic" placeholder="예: AI 트렌드, GPT-5 발표" required>
            </div>
            <div class="input-group">
              <label class="input-label">예약 시간 (선택)</label>
              <input name="scheduled_at" type="datetime-local">
            </div>
            <button type="submit" class="btn btn-primary" style="width:100%">큐에 추가</button>
          </form>
        </div>
        <div class="panel" style="margin-bottom:16px">
          <div class="panel-header">
            <div class="panel-title">GPT 주제 추천</div>
          </div>
          <p class="panel-sub" style="margin-bottom:14px">GPT가 오늘의 AI/테크 트렌드 주제 5개를 추천합니다. 원하는 것만 골라서 큐에 추가하세요.</p>
          <a href="/queue/suggest" class="btn btn-primary" style="width:100%;display:block;text-align:center;text-decoration:none">✨ 주제 추천받기</a>
        </div>
        <div class="panel">
          <div class="panel-header">
            <div class="panel-title">뉴스 자동 수집</div>
          </div>
          <p class="panel-sub" style="margin-bottom:14px">최신 AI 뉴스에서 주제를 자동으로 수집해 큐에 추가합니다. (Tavily 필요)</p>
          <form method="post" action="/queue/generate">
            <div class="input-group">
              <label class="input-label">추가할 주제 수</label>
              <input name="count" type="number" value="3" min="1" max="10">
            </div>
            <button type="submit" class="btn btn-secondary" style="width:100%">자동 수집 시작</button>
          </form>
        </div>
      </div>
    </div>"""
    return _page("큐 관리", "/queue", body, msg=msg, err=err)


@app.route("/queue/add", methods=["POST"])
def queue_add():
    topic = request.form.get("topic", "").strip()
    scheduled_at = request.form.get("scheduled_at", "").strip() or None
    if not topic:
        return redirect(url_for("queue_page", err="주제를 입력하세요."))
    enqueue(topic=topic, scheduled_at=scheduled_at)
    return redirect(url_for("queue_page", msg=f"'{topic}' 큐에 추가됨"))


@app.route("/queue/skip/<int:qid>", methods=["POST"])
def queue_skip(qid: int):
    mark_queue_status(qid, "skipped")
    return redirect(url_for("queue_page", msg=f"#{qid} 건너뜀"))


@app.route("/queue/generate", methods=["POST"])
def queue_generate():
    count = int(request.form.get("count", 3))
    try:
        from src.agents.content_queue import bulk_generate
        bulk_generate(count=count, auto_news=True)
        return redirect(url_for("queue_page", msg=f"{count}개 자동 추가 완료"))
    except Exception as e:
        return redirect(url_for("queue_page", err=str(e)))


@app.route("/queue/suggest")
def queue_suggest():
    """GPT로 주제 5개 추천 → 선택해서 큐에 추가"""
    from datetime import datetime
    from openai import OpenAI
    import os
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    today = datetime.now().strftime("%Y년 %m월 %d일")
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {
                "role": "system",
                "content": (
                    "당신은 한국 인스타그램 AI/테크 계정 @algo__kr의 콘텐츠 기획자입니다. "
                    "MZ세대가 흥미로워할 AI, 테크, 스타트업 관련 카드뉴스 주제를 추천합니다."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"오늘은 {today}입니다. "
                    "인스타그램 카드뉴스로 만들기 좋은 AI/테크 트렌드 주제 5개를 추천해주세요. "
                    "각 줄에 주제 하나씩, 번호 없이, 15자 이내로 써주세요."
                ),
            },
        ],
        max_tokens=200,
        temperature=0.85,
    )
    raw = resp.choices[0].message.content.strip()
    topics = [t.strip().strip("-").strip() for t in raw.splitlines() if t.strip()][:5]

    cards = "".join(
        f"""<div style="display:flex;align-items:center;gap:12px;padding:14px 16px;
            background:var(--bg-card);border:1px solid var(--border);border-radius:10px;margin-bottom:10px">
          <input type="checkbox" name="topics" value="{t}" id="t{i}"
            style="width:18px;height:18px;accent-color:var(--accent);cursor:pointer">
          <label for="t{i}" style="font-size:15px;font-weight:500;cursor:pointer;flex:1">{t}</label>
        </div>"""
        for i, t in enumerate(topics)
    )

    body = f"""
    <div style="max-width:560px;margin:0 auto">
      <div class="panel">
        <div class="panel-header">
          <div class="panel-title">GPT 주제 추천</div>
          <span class="badge badge-pending">오늘의 AI 트렌드</span>
        </div>
        <p class="panel-sub" style="margin-bottom:18px">원하는 주제를 선택해서 큐에 추가하세요.</p>
        <form method="post" action="/queue/suggest/add">
          {cards}
          <div style="display:flex;gap:10px;margin-top:20px">
            <button type="submit" class="btn btn-primary" style="flex:1">선택한 주제 큐에 추가</button>
            <a href="/queue/suggest" class="btn btn-secondary" style="flex:1;text-align:center;text-decoration:none">다시 추천받기</a>
          </div>
        </form>
      </div>
      <div style="text-align:center;margin-top:12px">
        <a href="/queue" style="color:var(--muted);font-size:13px">← 큐 관리로 돌아가기</a>
      </div>
    </div>"""
    return _page("GPT 주제 추천", "/queue", body)


@app.route("/queue/suggest/add", methods=["POST"])
def queue_suggest_add():
    from src.db import enqueue
    topics = request.form.getlist("topics")
    if not topics:
        return redirect(url_for("queue_suggest"))
    for t in topics:
        if t.strip():
            enqueue(topic=t.strip())
    return redirect(url_for("queue_page", msg=f"{len(topics)}개 주제가 큐에 추가됐습니다"))


# ── /analytics 성과 분석 ──────────────────────────────────

@app.route("/analytics")
def analytics_page():
    msg = request.args.get("msg", "")
    rows = get_analytics(limit=30)

    # 앵글별 집계
    angle_stats: dict[str, dict] = {}
    for r in rows:
        a = r["angle"] or "미분류"
        if a not in angle_stats:
            angle_stats[a] = {"likes": 0, "saves": 0, "comments": 0, "count": 0}
        angle_stats[a]["likes"] += r["likes"]
        angle_stats[a]["saves"] += r["saves"]
        angle_stats[a]["comments"] += r["comments"]
        angle_stats[a]["count"] += 1

    angle_rows = "".join(
        f"<tr><td>{a}</td><td>{v['count']}</td>"
        f"<td>{v['likes']//max(v['count'],1)}</td>"
        f"<td>{v['saves']//max(v['count'],1)}</td>"
        f"<td>{v['comments']//max(v['count'],1)}</td></tr>"
        for a, v in sorted(angle_stats.items(), key=lambda x: x[1]["likes"], reverse=True)
    ) or "<tr><td colspan='5' style='color:#666'>데이터 없음</td></tr>"

    data_rows = "".join(
        f"<tr><td>{r['topic']}</td><td>{r['angle'] or '-'}</td>"
        f"<td>❤️ {r['likes']}</td><td>💬 {r['comments']}</td>"
        f"<td>🔖 {r['saves']}</td><td>👁 {r['reach']}</td>"
        f"<td>{str(r['checked_at'])[:10]}</td></tr>"
        for r in rows
    ) or "<tr><td colspan='7' style='color:#666'>데이터 없음</td></tr>"

    # 차트 이미지 경로
    chart_path = Path("data/performance_chart.png")
    chart_html = ""
    if chart_path.exists():
        chart_html = '<img src="/analytics/chart" style="max-width:100%;border-radius:8px;margin-top:16px">'

    body = f"""
    <div style="display:flex;justify-content:flex-end;margin-bottom:20px">
      <form method="post" action="/analytics/sync">
        <button type="submit" class="btn btn-secondary">🔄 Insights 동기화</button>
      </form>
    </div>
    <div class="panel" style="margin-bottom:20px">
      <div class="panel-header">
        <div>
          <div class="panel-title">앵글별 평균 성과</div>
          <div class="panel-sub">좋아요 높은 순 정렬</div>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>앵글</th><th>게시물 수</th><th>평균 좋아요</th><th>평균 저장</th><th>평균 댓글</th></tr></thead>
          <tbody>{angle_rows}</tbody>
        </table>
      </div>
    </div>
    <div class="panel">
      <div class="panel-header">
        <div class="panel-title">전체 게시물 성과</div>
      </div>
      {chart_html}
      <div class="table-wrap" style="margin-top:16px">
        <table>
          <thead><tr><th>주제</th><th>앵글</th><th>❤️ 좋아요</th><th>💬 댓글</th><th>🔖 저장</th><th>👁 도달</th><th>날짜</th></tr></thead>
          <tbody>{data_rows}</tbody>
        </table>
      </div>
    </div>"""
    return _page("성과 분석", "/analytics", body, msg=msg)


@app.route("/analytics/sync", methods=["POST"])
def analytics_sync():
    try:
        from src.agents.analytics import sync_all_insights
        sync_all_insights()
        return redirect(url_for("analytics_page", msg="Insights 동기화 완료"))
    except Exception as e:
        return redirect(url_for("analytics_page", msg=f"오류: {e}"))


@app.route("/analytics/chart")
def analytics_chart():
    chart_path = ROOT / "data" / "performance_chart.png"
    if not chart_path.exists():
        try:
            from src.agents.analytics import plot_performance
            plot_performance(str(chart_path))
        except Exception:
            pass
    if chart_path.exists():
        return send_file(str(chart_path), mimetype="image/png")
    return "차트 없음", 404


# ── /settings 설정 ────────────────────────────────────────

@app.route("/settings")
def settings_page():
    msg = request.args.get("msg", "")
    err = request.args.get("err", "")

    persona_path = ROOT / "persona.json"
    persona_raw = persona_path.read_text(encoding="utf-8") if persona_path.exists() else "{}"

    # .env 키 현황
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    keys = [
        "OPENAI_API_KEY", "TAVILY_API_KEY", "PEXELS_API_KEY",
        "IG_ACCESS_TOKEN", "IG_USER_ID",
        "THREADS_ACCESS_TOKEN", "THREADS_USER_ID",
        "TISTORY_ACCESS_TOKEN",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    ]
    def _key_badge(k: str) -> str:
        if os.getenv(k):
            return f"<tr><td>{k}</td><td><span style='color:#00E5FF'>✓ 설정됨</span></td></tr>"
        return f"<tr><td>{k}</td><td><span style='color:#ff6b6b'>✗ 없음</span></td></tr>"

    key_rows = "".join(_key_badge(k) for k in keys)

    body = f"""
    <div style="display:grid;grid-template-columns:1fr 360px;gap:20px;align-items:start">
      <div class="panel">
        <div class="panel-header">
          <div>
            <div class="panel-title">persona.json 편집</div>
            <div class="panel-sub">브랜드명, CTA, 해시태그, 톤 설정</div>
          </div>
        </div>
        <form method="post" action="/settings/persona">
          <textarea name="persona_json" rows="22" style="font-family:'Consolas',monospace;font-size:12px;line-height:1.6">{persona_raw}</textarea>
          <div style="margin-top:12px">
            <button type="submit" class="btn btn-primary">변경사항 저장</button>
          </div>
        </form>
      </div>
      <div class="panel">
        <div class="panel-header">
          <div>
            <div class="panel-title">API 키 현황</div>
            <div class="panel-sub">.env 파일에서 관리</div>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>키</th><th>상태</th></tr></thead>
            <tbody>{key_rows}</tbody>
          </table>
        </div>
      </div>
    </div>"""
    return _page("설정", "/settings", body, msg=msg, err=err)


@app.route("/settings/persona", methods=["POST"])
def settings_persona():
    raw = request.form.get("persona_json", "")
    try:
        json.loads(raw)  # 유효성 검사
        persona_path = ROOT / "persona.json"
        persona_path.write_text(raw, encoding="utf-8")
        return redirect(url_for("settings_page", msg="persona.json 저장 완료"))
    except json.JSONDecodeError as e:
        return redirect(url_for("settings_page", err=f"JSON 형식 오류: {e}"))


# ══════════════════════════════════════════════════════════
# ── /generate  카드뉴스 생성 + 실시간 진행 + 미리보기 + 발행
# ══════════════════════════════════════════════════════════

def _emit(q: queue.Queue, event: str, data: str) -> None:
    """SSE 이벤트 큐에 메시지 추가"""
    q.put(f"event: {event}\ndata: {data}\n\n")


def _run_pipeline_job(job_id: str, topic: str, auto: bool, make_reels: bool) -> None:
    """백그라운드 스레드에서 파이프라인 실행"""
    q = _JOB_QUEUES[job_id]
    job = _JOBS[job_id]
    job["status"] = "running"

    # print를 가로채서 SSE로 스트리밍
    class _StreamCapture(io.TextIOBase):
        def write(self, s: str):
            if s.strip():
                job["logs"].append(s.rstrip())
                _emit(q, "log", s.rstrip().replace("\n", " "))
            return len(s)
        def flush(self): pass

    old_stdout = sys.stdout
    sys.stdout = _StreamCapture()

    try:
        if auto:
            from src.agents.news_collector import collect_and_select
            sel = collect_and_select()
            _emit(q, "topic", sel.topic)
            actual_topic = sel.topic
            trend_ctx = sel.context
        else:
            actual_topic = topic
            trend_ctx = ""

        from src.pipeline import run_pipeline
        from src.persona import load_persona, resolve_persona
        from src.agents.topic_refiner import refine_topic

        # 주제 정제: 광범위한 주제 → 단일 기사 집중 (trend_ctx 없을 때만)
        # ★ 핵심: article_content를 trend_context로 쓰지 않는다.
        #   → TrendAnalyzer가 반드시 실행돼야 기사 전문과 영상 후보를 제대로 가져옴.
        #   → 정제는 주제 문자열만 바꾸고, topic_refined=True를 pipeline에 전달해 2번 정제를 막음.
        topic_was_refined = False
        if not trend_ctx:
            try:
                refined_topic, _rfr, _article_content = refine_topic(actual_topic)
                if refined_topic != actual_topic:
                    _emit(q, "log", f"🎯 주제 정제: '{actual_topic}' → '{refined_topic}'")
                    _emit(q, "topic", refined_topic)
                    actual_topic = refined_topic
                    topic_was_refined = True
            except Exception as _e:
                _emit(q, "log", f"주제 정제 스킵: {_e}")

        # 카테고리 감지 + 색상 뱃지 emit
        _base_p = load_persona()
        _resolved_p = resolve_persona(actual_topic, _base_p)
        _emit(q, "category", json.dumps({
            "name": _resolved_p.topic_category,
            "color": _resolved_p.primary_color,
        }))
        paths = run_pipeline(
            topic=actual_topic,
            trend_context=trend_ctx,
            make_reels=make_reels,
            fact_check=True,
            auto=True,   # 대시보드에서는 사용자 입력 없이 자동 선택
            topic_refined=topic_was_refined,  # 2번 정제 방지
        )

        sys.stdout = old_stdout

        if paths:
            job["status"] = "done"
            job["paths"] = [str(p) for p in paths]
            job["topic"] = actual_topic
            job["image_dir"] = str(paths[0].parent)

            # caption.txt 읽기
            caption_path = paths[0].parent / "caption.txt"
            if caption_path.exists():
                job["caption"] = caption_path.read_text(encoding="utf-8")

            _emit(q, "done", json.dumps({
                "job_id": job_id,
                "topic": actual_topic,
                "count": len(paths),
                "image_dir": paths[0].parent.name,
                "filenames": [p.name for p in paths]
            }))
        else:
            job["status"] = "error"
            job["error"] = "파이프라인이 빈 결과를 반환했습니다."
            _emit(q, "error", job["error"])

    except Exception as e:
        sys.stdout = old_stdout
        tb = traceback.format_exc()
        job["status"] = "error"
        job["error"] = str(e)
        job["traceback"] = tb
        # 전체 traceback을 로그에 emit (줄 단위)
        for tb_line in tb.splitlines():
            _emit(q, "log", f"[TB] {tb_line}")
        _emit(q, "error", str(e))
    finally:
        sys.stdout = old_stdout
        q.put(None)  # SSE 스트림 종료 신호


@app.route("/generate", methods=["GET"])
def generate_page():
    # 완료된 최근 job 목록
    recent_jobs = [
        (jid, j) for jid, j in _JOBS.items()
        if j["status"] == "done"
    ][-5:]

    # output 폴더의 기존 결과물도 목록에 포함
    output_dirs = sorted(
        [d for d in (ROOT / "output").iterdir() if d.is_dir() and not d.name.startswith(".")],
        key=lambda d: d.stat().st_mtime, reverse=True
    )[:10]

    history_rows = ""
    for d in output_dirs:
        pngs = sorted(d.glob("card_*.png"))
        if not pngs:
            continue
        thumb = f'<img src="/output_img/{d.name}/{pngs[0].name}" class="history-thumb">'
        count = len(pngs)
        mtime = datetime.fromtimestamp(d.stat().st_mtime).strftime("%m/%d %H:%M")
        history_rows += (
            f"<tr>"
            f"<td style='width:56px'>{thumb}</td>"
            f"<td style='font-weight:500'>{d.name[16:].replace('_',' ')[:32]}</td>"
            f"<td><span class='badge badge-ig'>{count}장</span></td>"
            f"<td style='color:var(--muted);font-size:12px'>{mtime}</td>"
            f"<td style='display:flex;gap:6px'>"
            f"<a href='/preview/{d.name}' class='btn btn-secondary' style='padding:5px 12px;font-size:12px'>미리보기</a>"
            f"<a href='/publish_page/{d.name}' class='btn btn-primary' style='padding:5px 12px;font-size:12px'>발행</a>"
            f"</td>"
            f"</tr>"
        )

    body = f"""
<div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:24px">

  <!-- 주제 지정 생성 -->
  <div class="panel">
    <div class="panel-header">
      <div>
        <div class="panel-title">✍️ 주제 지정 생성</div>
        <div class="panel-sub">원하는 주제를 입력하면 최신 뉴스를 수집해 카드뉴스를 만들어 드려요</div>
      </div>
    </div>
    <form id="manualForm">
      <div class="input-group">
        <label class="input-label">주제를 입력해 주세요</label>
        <input type="text" id="topicInput" class="input" placeholder="예) 애플 WWDC 2026 요약, 테슬라 로보택시 출시..." style="font-size:15px;padding:14px 18px">
        <div class="example-chips">
          <span class="example-chip" onclick="document.getElementById('topicInput').value='인공지능 에이전트 최신 트렌드'">🤖 AI 에이전트</span>
          <span class="example-chip" onclick="document.getElementById('topicInput').value='비트코인 시장 전망 2026'">📈 비트코인 전망</span>
          <span class="example-chip" onclick="document.getElementById('topicInput').value='애플 WWDC 2026 핵심 요약'">🍎 WWDC 요약</span>
          <span class="example-chip" onclick="document.getElementById('topicInput').value='MZ세대 소비 트렌드 분석'">🛒 MZ 트렌드</span>
        </div>
      </div>
      <div class="toggle-row" style="margin:20px 0 16px">
        <input type="checkbox" id="makeReels">
        <span>📹 Reels MP4도 함께 생성</span>
      </div>
      <button type="submit" id="manualBtn" class="btn btn-primary btn-lg" style="width:100%">✨ 카드뉴스 생성 시작</button>
    </form>
  </div>

  <!-- 자동 뉴스 선택 생성 -->
  <div class="panel">
    <div class="panel-header">
      <div>
        <div class="panel-title">🤖 AI 자동 뉴스 수집</div>
        <div class="panel-sub">AI가 오늘의 가장 핫한 뉴스를 직접 골라 카드뉴스를 만들어요</div>
      </div>
    </div>
    <div style="background:rgba(49,130,246,.06);border-radius:var(--radius-sm);padding:20px;margin-bottom:20px">
      <div style="font-size:32px;text-align:center;margin-bottom:12px">🧠</div>
      <p style="font-size:13px;color:var(--text-secondary);text-align:center;line-height:1.7">별도 주제 입력 없이<br>AI가 뉴스를 분석해 최적의 주제를 <strong style="color:var(--accent)">자동으로 선택</strong>합니다</p>
    </div>
    <div class="toggle-row" style="margin-bottom:20px">
      <input type="checkbox" id="autoReels">
      <span>📹 Reels MP4도 함께 생성</span>
    </div>
    <button id="autoBtn" class="btn btn-secondary btn-lg" style="width:100%">🚀 자동 선택 + 생성</button>
  </div>
</div>

<!-- 진행 상황 패널 -->
<div id="progressPanel" style="display:none;margin-bottom:24px">
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title" id="progressTitle">생성 중...</div>
      <div style="display:flex;gap:8px;align-items:center">
        <span id="categoryBadge" style="display:none;padding:4px 10px;border-radius:20px;font-size:11px;font-weight:600;letter-spacing:.5px"></span>
        <span id="progressBadge" class="badge badge-pending">실행 중</span>
      </div>
    </div>
    <div class="progress-bar"><div class="progress-fill" style="width:100%"></div></div>
    <div id="logBox" class="logbox"></div>
  </div>
</div>

<!-- 생성 완료 미리보기 패널 -->
<div id="previewPanel" style="display:none;margin-bottom:24px">
  <div class="panel">
    <div class="panel-header">
      <div class="panel-title" id="previewTitle">생성 완료</div>
      <div style="display:flex;gap:8px">
        <button id="publishBtn" class="btn btn-success">📤 Instagram 발행</button>
        <button id="newGenBtn" class="btn btn-secondary">+ 새로 생성</button>
      </div>
    </div>
    <div id="cardGrid" class="card-grid" style="margin-bottom:16px"></div>
    <div id="captionBox" class="logbox" style="display:none;height:auto;max-height:120px;white-space:pre-wrap"></div>
  </div>
</div>

<!-- 생성 이력 -->
<div class="panel">
  <div class="panel-header">
    <div class="panel-title">최근 생성 이력</div>
  </div>
  <div class="table-wrap">
    <table>
      <thead><tr><th></th><th>주제</th><th>카드</th><th>생성일</th><th>작업</th></tr></thead>
      <tbody>{history_rows or "<tr><td colspan='5'><div class='empty'><div class='empty-icon'>🗂️</div><p>생성된 카드뉴스가 없습니다</p></div></td></tr>"}</tbody>
    </table>
  </div>
</div>

<script>
let currentJobId = null;
let currentDirName = null;

function startJob(topic, auto, reels) {{
  document.getElementById('progressPanel').style.display = 'block';
  document.getElementById('previewPanel').style.display = 'none';
  document.getElementById('logBox').innerHTML = '';
  document.getElementById('progressBadge').textContent = '실행 중';
  document.getElementById('progressBadge').className = 'badge badge-pending';
  const catBadge = document.getElementById('categoryBadge');
  catBadge.style.display = 'none';
  catBadge.textContent = '';
  document.getElementById('progressTitle').textContent = auto ? '자동 뉴스 선택 + 생성 중...' : `"${{topic}}" 생성 중...`;

  fetch('/generate/start', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{topic, auto, reels}})
  }})
  .then(r => r.json())
  .then(data => {{
    currentJobId = data.job_id;
    listenSSE(currentJobId);
  }});
}}

function listenSSE(jobId) {{
  const evtSource = new EventSource(`/generate/stream/${{jobId}}`);
  const logBox = document.getElementById('logBox');

  evtSource.addEventListener('log', e => {{
    const line = document.createElement('div');
    line.textContent = e.data;
    if (e.data.match(/^\\[[\\d.]+\\]|완료!|✓/)) {{
      line.className = 'log-step';
    }} else if (e.data.match(/✓|passed|통과/i)) {{
      line.className = 'log-done';
    }} else if (e.data.match(/오류|error|fail/i)) {{
      line.className = 'log-err';
    }}
    logBox.appendChild(line);
    logBox.scrollTop = logBox.scrollHeight;
  }});

  evtSource.addEventListener('topic', e => {{
    document.getElementById('progressTitle').textContent = `"${{e.data}}" 생성 중...`;
  }});

  evtSource.addEventListener('category', e => {{
    try {{
      const cat = JSON.parse(e.data);
      const badge = document.getElementById('categoryBadge');
      badge.textContent = cat.name;
      badge.style.display = 'inline-block';
      badge.style.background = cat.color + '22';
      badge.style.color = cat.color;
      badge.style.border = `1px solid ${{cat.color}}55`;
      badge.style.transition = 'all .3s ease';
    }} catch(err) {{}}
  }});

  evtSource.addEventListener('done', e => {{
    evtSource.close();
    const info = JSON.parse(e.data);
    currentDirName = info.image_dir;
    document.getElementById('progressBadge').textContent = '완료';
    document.getElementById('progressBadge').className = 'badge badge-published';
    document.querySelector('#progressPanel .progress-fill').style.animation = 'none';
    document.querySelector('#progressPanel .progress-fill').style.opacity = '1';
    showPreview(info.image_dir, info.topic, info.count, info.filenames);
  }});

  evtSource.addEventListener('error', e => {{
    evtSource.close();
    document.getElementById('progressBadge').textContent = '오류';
    document.getElementById('progressBadge').className = 'badge badge-skipped';
    const line = document.createElement('div');
    line.className = 'log-err';
    line.textContent = '✕ 오류: ' + e.data;
    logBox.appendChild(line);
  }});
}}

function showPreview(dirName, topic, count, filenames) {{
  document.getElementById('previewPanel').style.display = 'block';
  document.getElementById('previewTitle').textContent = `✓ "${{topic}}" 완료 — ${{count}}장`;
  const grid = document.getElementById('cardGrid');
  grid.innerHTML = '';
  
  const safeFilenames = filenames || [];
  
  for (let i = 1; i <= count; i++) {{
    const num = String(i).padStart(2, '0');
    const fname = safeFilenames.find(f => f.startsWith(`card_${{num}}_`));
    const types = ['cover', 'content', 'content', 'content', 'content', 'cta'];
    const t = types[i-1] || 'content';
    const finalName = fname || `card_${{num}}_${{t}}.png`;
    
    const src = `/output_img/${{dirName}}/${{finalName}}`;
    const isVideo = finalName.toLowerCase().endsWith('.mp4');
    
    const div = document.createElement('div');
    div.className = 'card-thumb';
    
    if (isVideo) {{
      div.innerHTML = `<video src="${{src}}" autoplay loop muted playsinline style="width:100%;height:100%;object-fit:cover;border-radius:var(--radius-sm);background:#000;"></video>
        <div class="num-badge">${{i}}/${{count}}</div>`;
    }} else {{
      div.innerHTML = `<img src="${{src}}" onerror="this.src='/output_img/${{dirName}}/card_${{num}}.png'">
        <div class="num-badge">${{i}}/${{count}}</div>`;
    }}
    
    div.addEventListener('click', () => {{
      const mediaNodes = Array.from(grid.querySelectorAll('img, video'));
      const allSrcs = mediaNodes.map(m => m.src);
      openLightbox(mediaNodes[i-1].src, allSrcs, i-1);
    }});
    grid.appendChild(div);
  }}

  // caption 로드
  fetch(`/caption/${{dirName}}`)
    .then(r => r.text())
    .then(txt => {{
      if (txt) {{
        document.getElementById('captionBox').style.display = 'block';
        document.getElementById('captionBox').textContent = txt;
      }}
    }});
}}

document.getElementById('manualForm').addEventListener('submit', e => {{
  e.preventDefault();
  const topic = document.getElementById('topicInput').value.trim();
  if (!topic) return;
  const reels = document.getElementById('makeReels').checked;
  startJob(topic, false, reels);
}});

document.getElementById('autoBtn').addEventListener('click', () => {{
  const reels = document.getElementById('autoReels').checked;
  startJob('', true, reels);
}});

document.getElementById('publishBtn').addEventListener('click', () => {{
  if (!currentDirName) return;
  if (!confirm('Instagram에 바로 발행하시겠습니까?')) return;
  const btn = document.getElementById('publishBtn');
  btn.textContent = '발행 중...';
  btn.disabled = true;
  fetch('/publish_now', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{dir_name: currentDirName}})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.success) {{
      btn.textContent = '✓ 발행 완료!';
      btn.className = 'btn btn-success';
      alert('Instagram 발행 완료!\\npost_id: ' + data.post_id);
    }} else {{
      btn.textContent = '📤 Instagram 발행';
      btn.disabled = false;
      alert('발행 실패: ' + data.error);
    }}
  }});
}});

document.getElementById('newGenBtn').addEventListener('click', () => {{
  document.getElementById('progressPanel').style.display = 'none';
  document.getElementById('previewPanel').style.display = 'none';
  document.getElementById('topicInput').value = '';
  window.scrollTo(0, 0);
}});
</script>
"""
    return _page("생성", "/generate", body)


@app.route("/generate/start", methods=["POST"])
def generate_start():
    data = request.get_json()
    topic = data.get("topic", "").strip()
    auto = data.get("auto", False)
    make_reels = data.get("reels", False)

    job_id = str(uuid.uuid4())[:8]
    q = queue.Queue()
    _JOB_QUEUES[job_id] = q
    _JOBS[job_id] = {
        "status": "pending",
        "logs": [],
        "paths": [],
        "topic": topic,
        "image_dir": "",
        "caption": "",
        "error": "",
    }

    t = threading.Thread(
        target=_run_pipeline_job,
        args=(job_id, topic, auto, make_reels),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


@app.route("/generate/stream/<job_id>")
def generate_stream(job_id: str):
    if job_id not in _JOB_QUEUES:
        return Response("event: error\ndata: job not found\n\n", mimetype="text/event-stream")

    q = _JOB_QUEUES[job_id]

    def event_generator():
        while True:
            try:
                msg = q.get(timeout=60)
                if msg is None:
                    break
                yield msg
            except queue.Empty:
                yield "event: ping\ndata: .\n\n"

    return Response(
        stream_with_context(event_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ── 이미지 서빙 ───────────────────────────────────────────

@app.route("/output_img/<dir_name>/<filename>")
def output_img(dir_name: str, filename: str):
    img_path = ROOT / "output" / dir_name / filename
    if not img_path.exists():
        # 파일명 패턴 탐색
        parent = ROOT / "output" / dir_name
        candidates = list(parent.glob(filename.rsplit("_", 1)[0] + "*.*"))
        cans = [c for c in candidates if c.suffix.lower() in [".png", ".mp4"]]
        if cans:
            img_path = cans[0]
        else:
            return "not found", 404
            
    mt = "video/mp4" if img_path.suffix.lower() == ".mp4" else "image/png"
    return send_file(str(img_path), mimetype=mt)


@app.route("/caption/<dir_name>")
def caption(dir_name: str):
    p = ROOT / "output" / dir_name / "caption.txt"
    if p.exists():
        return p.read_text(encoding="utf-8")
    return ""


# ── 미리보기 페이지 ───────────────────────────────────────

@app.route("/preview/<dir_name>")
def preview_page(dir_name: str):
    d = ROOT / "output" / dir_name
    if not d.exists():
        return "폴더 없음", 404

    media_files = sorted(list(d.glob("card_*.png")) + list(d.glob("card_*.mp4")))
    topic = dir_name[16:].replace("_", " ").strip()

    # 슬라이드별 수정 UI (script.json 있을 때만)
    script_path = d / "script.json"
    has_script = script_path.exists()
    dir_name_js = json.dumps(dir_name)

    # script.json 슬라이드별 video 매핑 정보 로드
    video_map: dict[int, str] = {}        # {slide_number: video_title}
    start_sec_map: dict[int, int] = {}    # {slide_number: start_seconds}
    video_id_map: dict[int, str] = {}     # {slide_number: video_id}  ← 자막용
    if has_script:
        try:
            _sc = json.loads(script_path.read_text(encoding="utf-8"))
            for sl in _sc.get("slides", []):
                sn = sl.get("slide_number", -1)
                if sl.get("video_id"):
                    video_map[sn] = sl.get("video_title", "영상")
                    video_id_map[sn] = sl["video_id"]
                if sl.get("start_seconds", 0) > 0:
                    start_sec_map[sn] = sl["start_seconds"]
        except Exception:
            pass

    # 카드 그리드 — 각 카드에 수정 버튼 + 영상 배지 포함
    cards_html_parts = []
    for i, p in enumerate(media_files):
        is_video = p.suffix.lower() == ".mp4"
        slide_num = i + 1
        start_sec = start_sec_map.get(slide_num, 0)

        # MP4 src에 #t={초} 추가 → HTML5 video 재생 위치 지정
        if is_video:
            src_suffix = f"#t={start_sec}" if start_sec > 0 else ""
            tag = f'video autoplay loop muted playsinline style="width:100%;height:100%;object-fit:cover;border-radius:inherit" src="/output_img/{dir_name}/{p.name}{src_suffix}"'
            media_el = f'<{tag}></{tag.split()[0]}>'
        else:
            media_el = f'<img src="/output_img/{dir_name}/{p.name}">'

        # 영상 슬라이드 배지 (▶) + 구간 정보
        video_badge = ""
        if is_video or slide_num in video_map:
            vt = video_map.get(slide_num, "영상")[:20]
            sec_label = f" {start_sec}s~" if start_sec > 0 else ""
            video_badge = (
                f'<div style="position:absolute;top:6px;left:6px;'
                f'background:rgba(220,30,30,.85);color:#fff;'
                f'border-radius:5px;padding:2px 7px;font-size:10px;font-weight:700;'
                f'backdrop-filter:blur(6px);z-index:11" title="{vt}">▶ 영상{sec_label}</div>'
            )

        edit_btn = ""
        if has_script:
            edit_btn = (
                f'<button class="slide-edit-btn" onclick="event.stopPropagation();openEditModal({i},this)" '
                f'style="position:absolute;top:6px;right:6px;background:rgba(0,0,0,.7);'
                f'border:1px solid rgba(124,111,247,.5);color:var(--accent);'
                f'border-radius:6px;padding:3px 8px;font-size:10px;cursor:pointer;'
                f'font-weight:600;z-index:10">✏️ 수정</button>'
            )
        all_srcs_js = "[" + ",".join(
            f"'/output_img/{dir_name}/{pf.name}'" for pf in media_files
        ) + "]"
        # 슬라이드 순서대로 video_id 배열 (없으면 빈 문자열)
        all_vids_js = "[" + ",".join(
            f"'{video_id_map.get(j+1, '')}'" for j in range(len(media_files))
        ) + "]"
        card_src = f"/output_img/{dir_name}/{p.name}"
        cards_html_parts.append(
            f'<div class="card-thumb" style="width:180px;flex:0 0 auto;position:relative;cursor:zoom-in"'
            f" onclick=\"openLightbox('{card_src}',{all_srcs_js},{i},{all_vids_js})\">"
            f'{media_el}'
            f'<div class="num-badge">{i+1}/{len(media_files)}</div>'
            f'{video_badge}'
            f'{edit_btn}'
            f'</div>'
        )
    cards_html = "".join(cards_html_parts)

    cap_path = d / "caption.txt"
    cap_txt = cap_path.read_text(encoding="utf-8") if cap_path.exists() else ""
    # f-string 안전 처리 (중괄호 escape)
    cap_safe = cap_txt.replace("{", "{{").replace("}", "}}")

    caption_html = f'''<div class="panel" style="margin-top:20px" id="captionPanel">
  <div class="panel-header">
    <div class="panel-title">캡션</div>
    <button onclick="regenerateCaption()" class="btn btn-secondary" style="font-size:12px;padding:6px 14px">🔄 캡션 재생성</button>
  </div>
  <pre id="captionText" style="font-size:12px;color:var(--muted);white-space:pre-wrap;line-height:1.7">{cap_safe}</pre>
</div>''' if cap_txt else ""

    reels_html = ""
    reels_path = d / "reels.mp4"
    if reels_path.exists():
        reels_html = f'''<div class="panel" style="margin-top:20px">
  <div class="panel-header"><div class="panel-title">Reels MP4</div></div>
  <video controls style="max-width:360px;border-radius:var(--radius-sm);display:block">
    <source src="/output_video/{dir_name}/reels.mp4" type="video/mp4">
  </video>
</div>'''

    body = f"""
<div class="panel" style="margin-bottom:20px">
  <div class="panel-header">
    <div>
      <div class="panel-title">{topic}</div>
      <div class="panel-sub">{len(media_files)}장 · {dir_name[:15]}</div>
    </div>
    <a href="/publish_page/{dir_name}" class="btn btn-primary">📤 Instagram 발행</a>
  </div>
  <div style="display:flex;gap:10px;overflow-x:auto;padding-bottom:8px" id="cardRow">
    {cards_html}
  </div>
</div>
{caption_html}
{reels_html}

<!-- 슬라이드 수정 모달 -->
<div id="editModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:1000;align-items:center;justify-content:center">
  <div style="background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:28px;width:480px;max-width:90vw">
    <div style="font-size:16px;font-weight:600;color:#fff;margin-bottom:6px">슬라이드 <span id="editSlideNum"></span> 수정</div>
    <div style="font-size:12px;color:var(--muted);margin-bottom:16px">수정할 내용을 자연어로 입력하면 AI가 해당 슬라이드만 다시 씁니다.</div>
    <div class="input-group">
      <label class="input-label">수정 요청</label>
      <textarea id="editInstruction" rows="3" placeholder="예: 더 충격적인 수치로 바꿔줘 / 더 쉬운 말로 / 실사용 예시 추가해줘"></textarea>
    </div>
    <div style="display:flex;gap:8px;margin-top:12px">
      <button id="editSubmitBtn" class="btn btn-primary" onclick="submitEdit()">AI 수정 적용</button>
      <button class="btn btn-secondary" onclick="closeEditModal()">취소</button>
    </div>
    <div id="editStatus" style="margin-top:10px;font-size:12px;color:var(--muted)"></div>
  </div>
</div>

<script>
const DIR_NAME = {dir_name_js};
let editSlideIndex = -1;

function openEditModal(idx, btn) {{
  editSlideIndex = idx;
  document.getElementById('editSlideNum').textContent = idx + 1;
  document.getElementById('editInstruction').value = '';
  document.getElementById('editStatus').textContent = '';
  document.getElementById('editModal').style.display = 'flex';
}}
function closeEditModal() {{
  document.getElementById('editModal').style.display = 'none';
}}
function submitEdit() {{
  const instruction = document.getElementById('editInstruction').value.trim();
  if (!instruction) return;
  const btn = document.getElementById('editSubmitBtn');
  btn.textContent = 'AI 수정 중...';
  btn.disabled = true;
  document.getElementById('editStatus').textContent = '⏳ 수정 중... (보통 10~20초)';
  fetch('/generate/edit_slide', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{dir_name: DIR_NAME, slide_index: editSlideIndex, instruction}})
  }})
  .then(r => r.json())
  .then(data => {{
    btn.textContent = 'AI 수정 적용';
    btn.disabled = false;
    if (data.success) {{
      // 해당 카드 이미지 새로고침
      const cards = document.querySelectorAll('#cardRow .card-thumb');
      if (cards[editSlideIndex]) {{
        const img = cards[editSlideIndex].querySelector('img');
        if (img) img.src = '/output_img/' + DIR_NAME + '/' + data.filename + '?t=' + Date.now();
      }}
      // 캡션 갱신
      fetch('/caption/' + DIR_NAME).then(r => r.text()).then(txt => {{
        const el = document.getElementById('captionText');
        if (el && txt) el.textContent = txt;
      }});
      document.getElementById('editStatus').innerHTML = '<span style="color:var(--success)">✓ 수정 완료! — ' + data.title + '</span>';
      setTimeout(closeEditModal, 2000);
    }} else {{
      document.getElementById('editStatus').innerHTML = '<span style="color:var(--danger)">✕ ' + data.error + '</span>';
    }}
  }});
}}

function regenerateCaption() {{
  fetch('/caption/' + DIR_NAME + '?regen=1')
    .then(r => r.text())
    .then(txt => {{
      const el = document.getElementById('captionText');
      if (el && txt) el.textContent = txt;
    }});
}}
</script>
"""
    return _page(topic, "/generate", body)


@app.route("/subtitle/<video_id>")
def subtitle(video_id: str):
    """캐시된 트랜스크립트를 한국어로 번역해 WebVTT 반환"""
    import re as _re
    _TRANSCRIPT_DIR = ROOT / "data" / "yt_cache" / "transcripts"
    _KO_DIR = _TRANSCRIPT_DIR / "ko"
    _KO_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 한국어 번역 캐시 확인
    ko_path = _KO_DIR / f"{video_id}.vtt"
    if ko_path.exists():
        return Response(ko_path.read_text(encoding="utf-8"), mimetype="text/vtt")

    # 2) 원본 트랜스크립트 로드
    src_path = _TRANSCRIPT_DIR / f"{video_id}.txt"
    if not src_path.exists() or src_path.stat().st_size == 0:
        return Response("WEBVTT\n\n", mimetype="text/vtt")

    raw = src_path.read_text(encoding="utf-8").strip()
    ts_re = _re.compile(r'^\[(\d{2}:\d{2}:\d{2})\]\s*(.+)$')
    entries = []
    for line in raw.splitlines():
        m = ts_re.match(line.strip())
        if m:
            entries.append({"t": m.group(1), "text": m.group(2)})

    if not entries:
        return Response("WEBVTT\n\n", mimetype="text/vtt")

    # 3) GPT로 영어 → 한국어 일괄 번역 (30줄씩 배치)
    try:
        from langchain_openai import ChatOpenAI
        from src.config import OPENAI_API_KEY
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=OPENAI_API_KEY)

        translated: list[str] = []
        batch_size = 30
        for start in range(0, len(entries), batch_size):
            batch = entries[start:start + batch_size]
            numbered = "\n".join(f"{j+1}. {e['text']}" for j, e in enumerate(batch))
            prompt = (
                f"아래 영어 자막을 자연스러운 한국어로 번역해주세요.\n"
                f"번호는 그대로 유지하고, 각 줄을 '번호. 번역' 형식으로 출력하세요.\n\n{numbered}"
            )
            result = llm.invoke(prompt)
            for line in result.content.strip().splitlines():
                m2 = _re.match(r'^\d+\.\s*(.+)$', line.strip())
                translated.append(m2.group(1) if m2 else line.strip())
    except Exception:
        # 번역 실패 시 원문 그대로
        translated = [e["text"] for e in entries]

    # 4) WebVTT 생성 (타임코드: [HH:MM:SS] → HH:MM:SS.000 --> HH:MM:SS+5.000)
    def _next_ts(ts: str, secs: int = 5) -> str:
        h, mi, s = map(int, ts.split(":"))
        total = h * 3600 + mi * 60 + s + secs
        return f"{total//3600:02d}:{(total%3600)//60:02d}:{total%60:02d}.000"

    vtt_lines = ["WEBVTT", ""]
    for i, (e, ko) in enumerate(zip(entries, translated)):
        start = e["t"].replace(":", ":", 2) + ".000"
        end = _next_ts(e["t"])
        vtt_lines.append(f"{i+1}")
        vtt_lines.append(f"{start} --> {end}")
        vtt_lines.append(ko)
        vtt_lines.append("")

    vtt = "\n".join(vtt_lines)
    ko_path.write_text(vtt, encoding="utf-8")
    return Response(vtt, mimetype="text/vtt")


@app.route("/output_video/<dir_name>/<filename>")
def output_video(dir_name: str, filename: str):
    p = ROOT / "output" / dir_name / filename
    if not p.exists():
        return "not found", 404
    return send_file(str(p), mimetype="video/mp4")


# ── 슬라이드 부분 수정 ────────────────────────────────────

@app.route("/generate/edit_slide", methods=["POST"])
def edit_slide():
    """특정 슬라이드 1장만 GPT로 수정 후 재렌더링"""
    data = request.get_json()
    dir_name = data.get("dir_name", "")
    slide_index = int(data.get("slide_index", 0))   # 0-based
    instruction = data.get("instruction", "").strip()

    if not dir_name or not instruction:
        return {"success": False, "error": "dir_name / instruction 필수"}

    d = ROOT / "output" / dir_name
    script_path = d / "script.json"
    if not script_path.exists():
        return {"success": False, "error": "script.json 없음 (이전 버전 생성물)"}

    try:
        import json as _json
        script_data = _json.loads(script_path.read_text(encoding="utf-8"))
        slides = script_data["slides"]

        if slide_index < 0 or slide_index >= len(slides):
            return {"success": False, "error": f"슬라이드 인덱스 범위 초과 (0~{len(slides)-1})"}

        slide = slides[slide_index]

        # GPT로 해당 슬라이드만 재작성
        from langchain_openai import ChatOpenAI
        from src.config import OPENAI_API_KEY

        llm = ChatOpenAI(model="gpt-4o", temperature=0.5, api_key=OPENAI_API_KEY)
        prompt = (
            f"인스타그램 카드뉴스 슬라이드를 수정해주세요.\n\n"
            f"주제: {script_data['topic']}\n"
            f"슬라이드 타입: {slide['slide_type']}\n"
            f"현재 제목: {slide['title']}\n"
            f"현재 내용: {slide['body']}\n\n"
            f"수정 요청: {instruction}\n\n"
            f"규칙:\n"
            f"- 슬라이드 타입({slide['slide_type']})은 유지\n"
            f"- 제목: 15자 이내, 핵심 한 문장\n"
            f"- 내용: 3~5줄, 각 줄 30자 이내, 구체적 수치 포함\n"
            f"- 모호한 표현('~전망', '~예상') 금지\n\n"
            f"JSON으로만 출력:\n"
            f'{{ "title": "...", "body": "줄1\\n줄2\\n줄3" }}'
        )
        result = llm.invoke(prompt)
        raw = result.content.strip()
        # JSON 파싱 (코드블록 제거)
        raw = re.sub(r"```[a-z]*\n?", "", raw).strip().strip("`")
        new_data = _json.loads(raw)

        # script.json 업데이트
        slides[slide_index]["title"] = new_data["title"]
        slides[slide_index]["body"] = new_data["body"]
        script_data["slides"] = slides
        script_path.write_text(_json.dumps(script_data, ensure_ascii=False, indent=2), encoding="utf-8")

        # 해당 슬라이드 재렌더링
        from src.schemas.card_news import CardNewsScript, Slide
        from src.agents.design_renderer import render_card_set
        from src.persona import load_persona

        persona = load_persona()
        updated_slides = [
            Slide(
                slide_number=s["slide_number"],
                slide_type=s["slide_type"],
                title=s["title"],
                body=s["body"],
            )
            for s in slides
        ]
        updated_script = CardNewsScript(
            topic=script_data["topic"],
            hook=script_data["hook"],
            hashtags=script_data["hashtags"],
            slides=updated_slides,
        )

        # 기존 배경 이미지 재사용
        bg_path = d / "background.png"
        if bg_path.exists():
            from PIL import Image
            bg = Image.open(str(bg_path))
        else:
            from PIL import Image
            bg = Image.new("RGB", (1080, 1350), (15, 15, 20))

        # 단일 슬라이드만 재렌더링
        target_slide = updated_slides[slide_index]
        from src.agents.design_renderer import (
            _render_cover, _render_content, _render_cta, _render_split,
            _apply_background, _build_style,
        )
        from src.persona import load_persona as _lp
        p = _lp()
        import src.agents.design_renderer as _dr
        _dr.STYLE = _dr._build_style(p)
        rendered_bg = _dr._apply_background(bg)

        total = len(updated_slides)
        slide_data = slides[slide_index]

        # video 매핑이 있는 content 슬라이드 → split 레이아웃 유지
        if (
            target_slide.slide_type == "content"
            and slide_data.get("video_id")
        ):
            # yt_cache에서 기존 썸네일 재사용
            from src.agents.youtube_fetcher import _download_thumbnail, _CACHE_DIR as _YT_CACHE
            thumb = _download_thumbnail(slide_data["video_id"])
            if thumb:
                rendered = _dr._render_split(
                    rendered_bg, target_slide, total, persona.handle,
                    thumb, f"youtu.be/{slide_data['video_id']}"
                )
            else:
                rendered = _dr._render_content(rendered_bg, target_slide, total, persona.handle)
        elif target_slide.slide_type == "cover":
            rendered = _dr._render_cover(rendered_bg, target_slide, total, persona.handle, hook=updated_script.hook)
        elif target_slide.slide_type == "cta":
            rendered = _dr._render_cta(rendered_bg, target_slide, total, persona.handle, updated_script.hashtags)
        else:
            rendered = _dr._render_content(rendered_bg, target_slide, total, persona.handle)

        fname = f"card_{target_slide.slide_number:02d}_{target_slide.slide_type}.png"
        fpath = d / fname
        rendered.save(str(fpath), "PNG", optimize=True)

        # 캡션도 갱신
        from src.agents.design_renderer import _generate_caption
        new_caption = _generate_caption(updated_script, persona.handle)
        (d / "caption.txt").write_text(new_caption, encoding="utf-8")

        return {
            "success": True,
            "filename": fname,
            "title": new_data["title"],
            "body": new_data["body"],
        }

    except Exception as e:
        import traceback
        return {"success": False, "error": str(e), "detail": traceback.format_exc()}


# ── Instagram 발행 페이지 ─────────────────────────────────

@app.route("/publish_page/<dir_name>")
def publish_page(dir_name: str):
    d = ROOT / "output" / dir_name
    pngs = sorted(d.glob("card_*.png"))
    mp4s = sorted(d.glob("card_*.mp4"))
    # MP4와 동일 번호의 PNG가 있으면 중복 → 업로드는 PNG만
    mp4_stems = {p.stem for p in mp4s}
    png_stems = {p.stem for p in pngs}
    video_only_count = len(mp4_stems - png_stems)  # PNG 없는 MP4 (거의 없음)
    has_video_slides = len(mp4s) > 0

    topic = dir_name[16:].replace("_", " ").strip()

    cap_path = d / "caption.txt"
    caption_txt = cap_path.read_text(encoding="utf-8") if cap_path.exists() else ""

    # IG 토큰 상태
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    ig_ok = bool(os.getenv("IG_ACCESS_TOKEN")) and bool(os.getenv("IG_USER_ID"))
    ig_status = (
        '<span style="color:#00E5FF">✓ 설정됨 — 바로 발행 가능</span>'
        if ig_ok else
        '<span style="color:#ff6b6b">✗ IG_ACCESS_TOKEN / IG_USER_ID 없음 → <a href="/settings">설정</a></span>'
    )

    # 썸네일: PNG만 표시 (업로드되는 것과 동일하게)
    thumbs = "".join(
        f'<div class="card-thumb" style="width:80px;flex:0 0 auto;position:relative">'
        f'<img src="/output_img/{dir_name}/{p.name}">'
        + (f'<span style="position:absolute;bottom:2px;left:2px;font-size:9px;background:rgba(0,0,0,.7);color:#fff;padding:1px 4px;border-radius:4px">▶ 영상슬라이드</span>' if p.stem in mp4_stems else "")
        + f'</div>'
        for p in pngs
    )

    video_notice = ""
    if has_video_slides:
        video_notice = f"""
<div style="background:rgba(255,184,77,.08);border:1px solid rgba(255,184,77,.25);border-radius:12px;padding:12px 16px;margin-bottom:16px;font-size:12px;color:var(--warn)">
  ⚠️ 영상 슬라이드 {len(mp4s)}개 포함 — Instagram 캐러셀은 정지 이미지(PNG)로 업로드됩니다. 영상은 로컬 preview에서만 재생됩니다.
</div>"""

    body = f"""
<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px">
  <a href="/preview/{dir_name}" class="btn btn-secondary" style="font-size:12px;padding:6px 14px">← 미리보기</a>
  <div style="flex:1">
    <div style="font-size:16px;font-weight:600;color:#fff">{topic}</div>
    <div style="font-size:12px;color:var(--muted)">{len(pngs)}장 카드뉴스 업로드 예정</div>
  </div>
</div>

{video_notice}

<div class="panel" style="margin-bottom:16px">
  <div class="panel-header">
    <div class="panel-title">카드 미리보기 (업로드 대상)</div>
    <span class="badge badge-ig">{len(pngs)}장</span>
  </div>
  <div style="display:flex;gap:8px;overflow-x:auto;padding-bottom:4px">{thumbs}</div>
</div>

<div class="panel" style="margin-bottom:16px">
  <div class="panel-header">
    <div class="panel-title">Instagram 계정 상태</div>
  </div>
  <p style="font-size:13px">{ig_status}</p>
</div>

<div class="panel" style="margin-bottom:20px">
  <div class="panel-header">
    <div>
      <div class="panel-title">캡션 편집</div>
      <div class="panel-sub">발행 전 캡션을 수정할 수 있습니다</div>
    </div>
  </div>
  <textarea id="captionText" rows="8" style="font-family:'Consolas',monospace;font-size:12px;line-height:1.7">{caption_txt}</textarea>
</div>

<div style="display:flex;align-items:center;gap:12px">
  <button id="publishNowBtn" class="btn btn-primary btn-lg" {'disabled' if not ig_ok else ''}>
    📤 Instagram에 발행
  </button>
  <div id="publishStatus" style="font-size:13px"></div>
</div>

<script>
document.getElementById('publishNowBtn')?.addEventListener('click', () => {{
  if (!confirm('Instagram 캐러셀로 발행하시겠습니까?')) return;
  const btn = document.getElementById('publishNowBtn');
  btn.textContent = '발행 중...';
  btn.disabled = true;
  const caption = document.getElementById('captionText').value;
  fetch('/publish_now', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json'}},
    body: JSON.stringify({{dir_name: '{dir_name}', caption}})
  }})
  .then(r => r.json())
  .then(data => {{
    if (data.success) {{
      btn.textContent = '✓ 발행 완료!';
      btn.className = 'btn btn-success btn-lg';
      const igUrl = data.permalink || `https://www.instagram.com/`;
      document.getElementById('publishStatus').innerHTML =
        `<span style="color:var(--accent2)">✓ 업로드 완료 &nbsp;·&nbsp; <a href="${{igUrl}}" target="_blank">Instagram에서 보기 →</a></span>`;
    }} else {{
      btn.textContent = '📤 Instagram에 발행';
      btn.disabled = false;
      document.getElementById('publishStatus').innerHTML =
        `<span style="color:var(--danger)">✕ 오류: ${{data.error}}</span>`;
    }}
  }});
}});
</script>
"""
    return _page(f"발행 — {topic}", "/generate", body)


@app.route("/publish_now", methods=["POST"])
def publish_now():
    data = request.get_json()
    dir_name = data.get("dir_name", "")
    custom_caption = data.get("caption", "")

    d = ROOT / "output" / dir_name
    if not d.exists():
        return {"success": False, "error": "폴더 없음"}

    pngs = sorted(d.glob("card_*.png"))
    if not pngs:
        return {"success": False, "error": "PNG 파일 없음"}

    try:
        from src.agents.publisher import publish as ig_publish

        # caption에서 hook / hashtags 파싱
        cap_path = d / "caption.txt"
        if custom_caption:
            caption_text = custom_caption
        elif cap_path.exists():
            caption_text = cap_path.read_text(encoding="utf-8")
        else:
            caption_text = ""

        lines = [l for l in caption_text.split("\n") if l.strip()]
        hook = lines[0] if lines else dir_name[16:].replace("_", " ")
        hashtags = [w for w in caption_text.split() if w.startswith("#")]

        post_id = ig_publish(
            image_paths=pngs,
            hook=hook,
            hashtags=hashtags,
        )

        # 실제 Instagram permalink 조회 (shortcode URL)
        permalink = ""
        try:
            from src.agents.publisher import get_post_permalink
            permalink = get_post_permalink(post_id)
        except Exception:
            pass

        # DB 기록
        try:
            from src.db import insert_post
            insert_post(
                platform="instagram",
                topic=dir_name[16:].replace("_", " "),
                post_id=post_id,
                angle="",
                hook=hook,
                hashtags=hashtags,
                image_dir=str(d),
                posted_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        except Exception:
            pass

        return {"success": True, "post_id": post_id, "permalink": permalink}

    except Exception as e:
        return {"success": False, "error": str(e)}


# ── 실행 ─────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5001))
    print(f"알고 대시보드: http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
